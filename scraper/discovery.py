"""Work out what platform a store runs on, and find every product URL on it.

Two related jobs live here:

* ``analyze_site`` - a fast, read-only reconnaissance pass used to show the
  user what we're about to scrape before they commit to it. Deliberately kept
  to a handful of requests so it can run while a dialog is open.
* ``discover_products`` - the exhaustive hunt for product URLs, run at scrape
  time. It layers several independent strategies and merges their results,
  because no single one works everywhere: sitemaps go missing, REST APIs get
  disabled, and some stores only expose products through paginated listings.

Every function here is defensive. Discovery failing to find products is a
normal outcome to report, never an exception to raise.
"""

import json
import logging
import re
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# ── Platform fingerprints ────────────────────────────────────────────────
# Ordered most-specific first: WooCommerce must be matched before plain
# WordPress, or every Woo store would be reported as "WordPress".
PLATFORM_RULES = [
    ('woocommerce', 'WordPress + WooCommerce', True, [
        (r'/plugins/woocommerce/', 'WooCommerce plugin assets'),
        (r'woocommerce-page|woocommerce-js|wc-block', 'WooCommerce CSS/JS markers'),
        (r'/wp-json/wc/', 'WooCommerce REST API reference'),
    ]),
    ('shopify', 'Shopify', True, [
        (r'cdn\.shopify\.com', 'Shopify CDN assets'),
        (r'Shopify\.theme|shopify-section', 'Shopify theme runtime'),
        (r'myshopify\.com', 'myshopify.com domain reference'),
    ]),
    ('wordpress', 'WordPress (no store plugin detected)', False, [
        (r'/wp-content/', 'wp-content asset paths'),
        (r'/wp-includes/', 'wp-includes asset paths'),
        (r'name=["\']generator["\'] content=["\']WordPress', 'WordPress generator meta tag'),
    ]),
    ('magento', 'Magento', False, [
        (r'/static/version\d+', 'Magento versioned static assets'),
        (r'Magento_|mage/requirejs', 'Magento JS modules'),
    ]),
    ('prestashop', 'PrestaShop', False, [
        (r'prestashop', 'PrestaShop marker'),
        (r'/modules/ps_', 'PrestaShop core modules'),
    ]),
    ('opencart', 'OpenCart', False, [
        (r'route=common/home', 'OpenCart routing'),
        (r'catalog/view/theme', 'OpenCart theme path'),
    ]),
    ('bigcommerce', 'BigCommerce', False, [
        (r'cdn\d*\.bigcommerce\.com', 'BigCommerce CDN'),
    ]),
    ('wix', 'Wix', False, [
        (r'static\.parastorage\.com', 'Wix static hosting'),
        (r'wixstatic\.com', 'Wix media CDN'),
    ]),
    ('squarespace', 'Squarespace', False, [
        (r'Static\.SQUARESPACE_CONTEXT', 'Squarespace runtime context'),
        (r'squarespace\.com', 'Squarespace reference'),
    ]),
]

PRODUCT_SITEMAP_HINTS = ('product', 'produkt')
TAXONOMY_SITEMAP_HINTS = ('product_cat', 'product-cat', 'product_tag', 'product-tag',
                          'category', 'categories', 'product_brand', 'product-brand',
                          'brand', 'tag', 'pa_', 'attribute')
# Path segments that mark a product page. '/shop/' and '/store/' are
# deliberately absent — they match catalogue indexes far more often.
PRODUCT_URL_HINTS = ('/product/', '/produkt/', '/products/', '/item/', '/p/',
                     '/catalogue/', '/catalog/', '/product-detail/', '/pd/',
                     '/dp/', '/buy/')
ARCHIVE_ROOT_PATHS = ('/shop/', '/store/', '/products/', '/shop', '/store', '/products',
                      '/catalogue/', '/catalogue', '/catalog/', '/catalog')
# The same names, as bare segments, so the check survives a front controller.
_ARCHIVE_ROOT_NAMES = {p.strip('/').lower() for p in ARCHIVE_ROOT_PATHS if p.strip('/')}

_FRONT_CONTROLLERS = ('index.php', 'index.html', 'index.htm', 'index.asp', 'index.aspx')
SITEMAP_FALLBACK_PATHS = ('/sitemap_index.xml', '/sitemap.xml', '/wp-sitemap.xml',
                          '/product-sitemap.xml', '/sitemap.xml.gz',
                          '/sitemap_products_1.xml', '/pub/sitemap.xml',
                          '/sitemap/sitemap.xml')

# Files a sitemap may list that are assets, not pages worth scraping.
_ASSET_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.svg', '.ico',
                   '.pdf', '.zip', '.mp4', '.webm', '.mp3', '.css', '.js', '.xml')

# Non-product page paths that show up in a whole-site sitemap.
_NON_PRODUCT_PATH_TOKENS = ('/blog/', '/news/', '/author/', '/tag/', '/category/',
                            '/page/', '/cart', '/checkout', '/my-account', '/wishlist',
                            '/contact', '/about', '/privacy', '/terms', '/faq',
                            '/wp-content/', '/wp-json/', '/feed')


def base_url(url):
    parsed = urlparse(url if '://' in url else f'https://{url}')
    return f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path.split('/')[0]}"


def _page_segments(path):
    """Path segments that carry meaning, with any front controller dropped."""
    segments = [s for s in (path or '').split('/') if s]
    while segments and segments[0].lower() in _FRONT_CONTROLLERS:
        segments.pop(0)
    return segments


def _is_archive_root(url):
    """True for the site root or a catalogue index, rather than a product."""
    segments = _page_segments(urlparse(url).path)
    return (not segments
            or (len(segments) == 1 and segments[0].lower() in _ARCHIVE_ROOT_NAMES))


def _is_scrapable_page(url, host=None):
    """Filter out things a sitemap lists that are not product pages.

    Sitemaps routinely include images on a separate media host, PDFs, blog
    posts and account pages. Feeding those to the scraper wastes a request each
    and lands them in failed_urls.json looking like real failures.
    """
    if not url or not url.startswith(('http://', 'https://')):
        return False
    parsed = urlparse(url)
    if host and parsed.netloc.lower() != host:
        return False
    path = parsed.path.lower()
    if path.endswith(_ASSET_SUFFIXES):
        return False
    if any(token in path for token in _NON_PRODUCT_PATH_TOKENS):
        return False

    # The homepage, and language stubs like '/zh' or '/en', turn up in product
    # sitemaps more often than you would hope. Scraped, they become a record
    # titled after the store itself, which then counts as "already done" on the
    # next run. No product slug is a single segment of three characters or less.
    segments = _page_segments(path)
    if not segments or (len(segments) == 1 and len(segments[0]) <= 3):
        return False

    return not _is_archive_root(url)


def _infer_product_urls(urls, host=None):
    """Pick the product URLs out of a whole-site sitemap by shape.

    Plenty of stores publish one undifferentiated ``sitemap.xml`` — no
    ``product-sitemap.xml`` to key off. But a catalogue dominates such a
    sitemap: on a Django-Oscar shop nearly every URL is ``/catalogue/<slug>/``.
    So group the URLs by their first path segment and take the segment that
    both looks like a product path and accounts for most of the file.

    Returns [] rather than guessing when no segment dominates, so a site with a
    genuinely flat URL scheme falls through to the other strategies instead of
    scraping its About page.
    """
    pages = [u for u in urls if _is_scrapable_page(u, host)]
    if len(pages) < 5:
        return []

    groups = {}
    for url in pages:
        segments = [s for s in urlparse(url).path.split('/') if s]
        key = f"/{segments[0]}/" if segments else '/'
        groups.setdefault(key, []).append(url)

    # A known product prefix wins outright, however small its share.
    for key, members in groups.items():
        if key in PRODUCT_URL_HINTS and len(members) >= 5:
            logger.info(f"Sitemap URL shape '{key}' matched a known product path "
                        f"-> {len(members)} URL(s)")
            return members

    key, members = max(groups.items(), key=lambda kv: len(kv[1]))
    share = len(members) / len(pages)
    # Two-thirds of a store's pages being one shape means that shape is the
    # catalogue. Below that it is just as likely to be a blog.
    if share >= 0.66 and len(members) >= 20 and key != '/':
        logger.info(f"Sitemap URL shape '{key}' is {share:.0%} of the site "
                    f"-> treating its {len(members)} URL(s) as products")
        return members
    return []


def detect_platform(html):
    """Identify the storefront platform from homepage markup."""
    if not html:
        return {'id': 'unknown', 'name': 'Unknown', 'supported': False,
                'confidence': 'none', 'evidence': []}

    for pid, name, supported, rules in PLATFORM_RULES:
        evidence = [desc for pattern, desc in rules
                    if re.search(pattern, html, re.IGNORECASE)]
        if evidence:
            confidence = 'high' if len(evidence) >= 2 else 'medium'
            return {'id': pid, 'name': name, 'supported': supported,
                    'confidence': confidence, 'evidence': evidence}

    return {'id': 'unknown', 'name': 'Unrecognised platform', 'supported': False,
            'confidence': 'none', 'evidence': []}


def inspect_wordpress(html):
    """Theme, plugins and version from asset URLs in the page source."""
    if not html:
        return None
    # Slug charset only - asset URLs sometimes contain wildcards or template
    # placeholders that would otherwise be reported as real theme/plugin names.
    slug = r'([a-z0-9][a-z0-9._-]*)'
    themes = sorted(set(re.findall(rf'/wp-content/themes/{slug}', html, re.IGNORECASE)))
    plugins = sorted(set(re.findall(rf'/wp-content/plugins/{slug}', html, re.IGNORECASE)))
    version = re.search(r'content=["\']WordPress\s+([\d.]+)', html, re.IGNORECASE)
    if not (themes or plugins or version):
        return None
    return {
        'version': version.group(1) if version else None,
        'themes': themes[:5],
        'plugins': plugins[:25],
        'plugin_count': len(plugins),
    }


# ── Discovery strategies ─────────────────────────────────────────────────
# Each returns a list of product URLs and never raises.

def _safe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def discover_via_sitemaps(client, parser, base):
    """Walk robots.txt and the conventional sitemap locations."""
    host = urlparse(base).netloc.lower()
    queue, found_sitemaps = [], []
    robots = client.fetch_page(f"{base}/robots.txt", retries=1)
    if robots:
        for line in robots.splitlines():
            if line.strip().lower().startswith('sitemap:'):
                advertised = line.split(':', 1)[1].strip()
                if advertised:
                    queue.append(advertised)

    for path in SITEMAP_FALLBACK_PATHS:
        if base + path not in queue:
            queue.append(base + path)

    seen, from_product_sitemaps, all_leaf_urls = set(), [], []
    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen or len(seen) > 60:
            continue
        seen.add(sitemap_url)

        xml = client.fetch_page(sitemap_url, retries=1)
        if not xml:
            continue
        locs = parser.parse_sitemap(xml)
        if not locs:
            continue
        found_sitemaps.append(sitemap_url)

        if '<sitemapindex' in xml[:2000].lower():
            queue.extend(locs)
            continue

        name = sitemap_url.lower()
        is_product_sitemap = (any(h in name for h in PRODUCT_SITEMAP_HINTS)
                              and not any(t in name for t in TAXONOMY_SITEMAP_HINTS))
        pages = [u for u in locs if _is_scrapable_page(u, host)]
        all_leaf_urls.extend(pages)
        if is_product_sitemap:
            logger.info(f"Product sitemap {sitemap_url} -> {len(pages)} product URL(s)")
            from_product_sitemaps.extend(pages)

    if from_product_sitemaps:
        return from_product_sitemaps, found_sitemaps

    # No sitemap advertised itself as products. Try the known product path
    # shapes first, then fall back to inferring the shape from the sitemap.
    guessed = [u for u in all_leaf_urls if any(h in u.lower() for h in PRODUCT_URL_HINTS)]
    if guessed:
        return guessed, found_sitemaps
    return _infer_product_urls(all_leaf_urls, host), found_sitemaps


def discover_via_shopify(client, base, max_pages=60):
    """Shopify exposes its whole catalogue as JSON at /products.json."""
    urls = []
    for page in range(1, max_pages + 1):
        text = client.fetch_page(f"{base}/products.json?limit=250&page={page}", retries=1)
        data = _safe_json(text) if text else None
        products = (data or {}).get('products') if isinstance(data, dict) else None
        if not products:
            break
        for item in products:
            handle = item.get('handle')
            if handle:
                urls.append(f"{base}/products/{handle}")
        if len(products) < 250:
            break
    if urls:
        logger.info(f"Shopify products.json -> {len(urls)} product URL(s)")
    return urls


def discover_via_woo_store_api(client, base, max_pages=100):
    """WooCommerce's Store API is public and needs no credentials."""
    urls = []
    for page in range(1, max_pages + 1):
        text = client.fetch_page(
            f"{base}/wp-json/wc/store/products?per_page=100&page={page}", retries=1)
        data = _safe_json(text) if text else None
        if not isinstance(data, list) or not data:
            break
        for item in data:
            link = item.get('permalink')
            if link:
                urls.append(link)
        if len(data) < 100:
            break
    if urls:
        logger.info(f"WooCommerce Store API -> {len(urls)} product URL(s)")
    return urls


def discover_via_wp_rest(client, base, max_pages=100):
    """Generic WP REST endpoint, for stores exposing a 'product' post type."""
    urls = []
    for page in range(1, max_pages + 1):
        text = client.fetch_page(
            f"{base}/wp-json/wp/v2/product?per_page=100&page={page}", retries=1)
        data = _safe_json(text) if text else None
        if not isinstance(data, list) or not data:
            break
        for item in data:
            link = item.get('link')
            if link:
                urls.append(link)
        if len(data) < 100:
            break
    if urls:
        logger.info(f"WP REST API -> {len(urls)} product URL(s)")
    return urls


def discover_via_opencart(client, base, max_pages=60):
    """OpenCart stores rarely publish a sitemap, but always have search.

    ``index.php?route=product/search`` with an empty-ish term and a large limit
    pages the entire catalogue, and OpenCart's own HTML sitemap route lists
    every category. Both are stock routes present on any default install.
    """
    from bs4 import BeautifulSoup

    host = urlparse(base).netloc.lower()
    products, seen_pages = set(), set()

    # OpenCart stores are usually run with SEO URLs on, so products appear as
    # flat slugs with no 'product_id=' anywhere. The stock listing markup is
    # the reliable signal instead.
    listing_selectors = ('.product-thumb a', '.product-layout a', '.product-item a',
                         '.caption a', '.product-grid a', '.product-list a')

    def harvest(html, page_url):
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            return
        anchors = []
        for selector in listing_selectors:
            try:
                anchors.extend(soup.select(selector))
            except Exception:
                continue
        anchors.extend(a for a in soup.find_all('a', href=True)
                       if 'product_id=' in a['href'] or 'route=product/product' in a['href'])
        for anchor in anchors:
            href = anchor.get('href')
            if not href:
                continue
            href = urljoin(page_url, href.split('#')[0])
            if urlparse(href).netloc.lower() != host:
                continue
            # Listing links carry the search/paging params through; strip them
            # so the same product isn't scraped once per originating page.
            if 'product_id=' not in href and 'route=' not in href:
                href = href.split('?')[0]
            if _is_scrapable_page(href, host):
                products.add(href)

    # Paged search over the whole catalogue.
    for page in range(1, max_pages + 1):
        url = (f"{base}/index.php?route=product/search&search=&limit=100"
               f"&description=true&page={page}")
        if url in seen_pages:
            break
        seen_pages.add(url)
        html = client.fetch_page(url, retries=1)
        if not html:
            break
        before = len(products)
        harvest(html, url)
        if len(products) == before:
            break  # no new items on this page — end of the catalogue

    # The stock HTML sitemap lists categories; walk them for anything missed.
    sitemap_html = client.fetch_page(f"{base}/index.php?route=information/sitemap", retries=1)
    if sitemap_html:
        try:
            soup = BeautifulSoup(sitemap_html, 'lxml')
            categories = [urljoin(base, a['href']) for a in soup.find_all('a', href=True)
                          if 'path=' in a['href'] or 'route=product/category' in a['href']]
        except Exception:
            categories = []
        for category in categories[:max_pages]:
            html = client.fetch_page(f"{category}&limit=100", retries=1)
            if html:
                harvest(html, category)

    if products:
        logger.info(f"OpenCart routes -> {len(products)} product URL(s)")
    return sorted(products)


def discover_via_crawl(client, base, max_pages=150):
    """Last resort: walk the storefront itself, harvesting product links.

    Used when a store publishes no usable sitemap or API. A bounded
    breadth-first walk rather than a pagination-only follow, because plenty of
    storefronts — anything rendering its homepage carousels in JavaScript, for
    one — expose no product link at all until you reach a category page, and
    their category URLs are bare slugs with nothing to pattern-match on.

    Pages that look like listings are visited first so the budget is spent
    where products actually are. If hint-matching finds nothing, the shape of
    everything collected is inferred instead, which is what rescues stores
    using an unusual product path.
    """
    from bs4 import BeautifulSoup

    host = urlparse(base).netloc.lower()
    queue = [base + p for p in ('/', '/shop/', '/store/', '/products/', '/catalogue/',
                                '/catalog/', '/categories', '/product-category/')]
    seen_pages, products, all_links = set(), set(), set()

    def looks_like_listing(url):
        low = url.lower()
        return any(token in low for token in
                   ('/page/', 'paged=', 'product-category', '/category/', '/categories',
                    'route=product/category', '/collections/', '/shop', '/store',
                    '/catalog', '/departments/', '/brand'))

    while queue and len(seen_pages) < max_pages:
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)

        html = client.fetch_page(page, retries=1)
        if not html:
            continue
        try:
            soup = BeautifulSoup(html, 'lxml')
        except Exception:
            continue

        for anchor in soup.find_all('a', href=True):
            href = urljoin(page, anchor['href'].split('#')[0]).split('?')[0]
            if urlparse(href).netloc.lower() != host:
                continue
            if not _is_scrapable_page(href, host):
                continue
            all_links.add(href)
            if any(h in href.lower() for h in PRODUCT_URL_HINTS):
                products.add(href)
            elif href not in seen_pages and len(queue) < max_pages * 2:
                # Listings first, everything else behind them.
                if looks_like_listing(href):
                    queue.insert(0, href)
                else:
                    queue.append(href)

    if not products and all_links:
        # No recognised product path. Let the dominant URL shape decide.
        products = set(_infer_product_urls(sorted(all_links), host))

    if products:
        logger.info(f"Listing crawl -> {len(products)} product URL(s) "
                    f"from {len(seen_pages)} page(s)")
    return sorted(products)


def discover_products(client, parser, url):
    """Find every product URL on a store, combining all applicable strategies.

    Strategies are additive: a store may publish an incomplete sitemap *and* a
    complete API, so results are merged rather than taking the first hit. This
    is what stops a catalogue of hundreds coming back as a handful.
    """
    base = base_url(url)
    homepage = client.fetch_page(base, retries=1)
    platform = detect_platform(homepage)
    logger.info(f"Platform detected: {platform['name']} (confidence: {platform['confidence']})")

    urls, strategies = [], {}

    def run(label, fn):
        try:
            found = fn() or []
        except Exception as e:
            logger.error(f"Discovery strategy '{label}' failed ({type(e).__name__}: {e}) - continuing")
            found = []
        strategies[label] = len(found)
        urls.extend(found)

    sitemap_urls, sitemaps = [], []

    def sitemap_strategy():
        nonlocal sitemap_urls, sitemaps
        sitemap_urls, sitemaps = discover_via_sitemaps(client, parser, base)
        return sitemap_urls

    run('sitemaps', sitemap_strategy)

    # Shopify's products.json is worth probing even when the homepage did not
    # look like Shopify — headless and white-labelled storefronts hide every
    # other marker but keep the endpoint.
    if platform['id'] == 'shopify' or not platform['supported']:
        run('shopify_api', lambda: discover_via_shopify(client, base))
    if platform['id'] in ('woocommerce', 'wordpress'):
        run('woocommerce_api', lambda: discover_via_woo_store_api(client, base))
        if not strategies.get('woocommerce_api'):
            run('wp_rest_api', lambda: discover_via_wp_rest(client, base))
    if platform['id'] == 'opencart':
        run('opencart_routes', lambda: discover_via_opencart(client, base))

    deduped = list(dict.fromkeys(urls))

    # Nothing structured worked - fall back to walking the storefront itself.
    if not deduped:
        logger.warning("No products found via sitemaps or APIs - falling back to crawling listing pages")
        run('listing_crawl', lambda: discover_via_crawl(client, base))
        deduped = list(dict.fromkeys(urls))

    logger.info(f"Discovery complete: {len(deduped)} unique product URL(s) "
                f"({', '.join(f'{k}={v}' for k, v in strategies.items())})")

    return {
        'urls': deduped,
        'platform': platform,
        'strategies': strategies,
        'sitemaps': sitemaps,
    }


# ── Fast pre-scrape reconnaissance ───────────────────────────────────────

def analyze_site(client, parser, url):
    """A quick report on a store, for showing the user before they commit.

    Kept to a handful of requests: homepage, robots.txt, one sitemap fetch and
    one API probe. Product counts come from response headers or a single page
    of results where possible, rather than paging the whole catalogue.
    """
    base = base_url(url)
    report = {
        'url': base,
        'reachable': False,
        'platform': {'id': 'unknown', 'name': 'Unknown', 'supported': False,
                     'confidence': 'none', 'evidence': []},
        'wordpress': None,
        'robots_txt': False,
        'sitemaps': [],
        'product_sitemaps': [],
        'apis': [],
        'estimated_products': None,
        'strategy': None,
        'fingerprint': None,
        'warnings': [],
        'notes': [],
    }

    # probe() reports *why* a host refused us, and which browser fingerprint
    # got in — worth surfacing, because "blocked by a bot wall" and "the site
    # is down" need completely different responses from the user.
    attempt = client.probe(base)
    homepage = attempt.get('html')
    if not homepage:
        reason = attempt.get('reason') or 'unreachable'
        report['reason'] = reason
        if 'challenge' in reason.lower() or 'HTTP 403' in reason:
            report['warnings'].append(
                'This site is behind an interactive bot challenge (Cloudflare or similar) that '
                'cannot be solved automatically. Scraping it will not work without a real browser '
                'session.')
        else:
            report['warnings'].append(
                f'Could not fetch the homepage ({reason}). The site may be down or unreachable '
                'from this machine.')
        return report

    report['reachable'] = True
    report['fingerprint'] = attempt.get('profile')
    if attempt.get('profile') and attempt['profile'] != 'chrome':
        report['notes'].append(
            f"This store rejects the default browser fingerprint; the scraper will use "
            f"'{attempt['profile']}' for it automatically.")
    report['platform'] = detect_platform(homepage)
    report['wordpress'] = inspect_wordpress(homepage)

    # robots.txt + advertised sitemaps
    robots = client.fetch_page(f"{base}/robots.txt", retries=1)
    if robots:
        report['robots_txt'] = True
        report['sitemaps'] = [l.split(':', 1)[1].strip() for l in robots.splitlines()
                              if l.strip().lower().startswith('sitemap:')][:10]

    # One sitemap fetch to enumerate the children without downloading them all
    index_url = report['sitemaps'][0] if report['sitemaps'] else f"{base}/sitemap_index.xml"
    xml = client.fetch_page(index_url, retries=1)
    if xml:
        children = parser.parse_sitemap(xml)
        if '<sitemapindex' in xml[:2000].lower():
            if index_url not in report['sitemaps']:
                report['sitemaps'].append(index_url)
            report['product_sitemaps'] = [
                c for c in children
                if any(h in c.lower() for h in PRODUCT_SITEMAP_HINTS)
                and not any(t in c.lower() for t in TAXONOMY_SITEMAP_HINTS)]
        elif children:
            report['product_sitemaps'] = [index_url]

    if report['product_sitemaps']:
        report['strategy'] = 'sitemap'

    # Platform APIs - a single probe each, reading the count from headers
    pid = report['platform']['id']
    if pid in ('woocommerce', 'wordpress'):
        resp = client.fetch_response(f"{base}/wp-json/wc/store/products?per_page=1")
        if resp is not None and resp.status_code == 200:
            total = resp.headers.get('X-WP-Total')
            report['apis'].append({'name': 'WooCommerce Store API', 'available': True,
                                   'products': int(total) if total and total.isdigit() else None})
            if total and total.isdigit():
                report['estimated_products'] = int(total)
                report['strategy'] = report['strategy'] or 'woocommerce_api'
        wp = client.fetch_response(f"{base}/wp-json/")
        if wp is not None and wp.status_code == 200:
            report['apis'].append({'name': 'WordPress REST API', 'available': True, 'products': None})

    # Shopify's endpoint is probed even on unrecognised platforms — headless
    # storefronts hide every other marker but keep it.
    if pid == 'shopify' or not report['platform']['supported']:
        text = client.fetch_page(f"{base}/products.json?limit=250", retries=1)
        data = _safe_json(text) if text else None
        if isinstance(data, dict) and isinstance(data.get('products'), list):
            count = len(data['products'])
            report['apis'].append({'name': 'Shopify products.json', 'available': True,
                                   'products': count if count < 250 else None})
            report['strategy'] = report['strategy'] or 'shopify_api'
            if count < 250:
                report['estimated_products'] = count

    if pid == 'opencart':
        resp = client.fetch_response(
            f"{base}/index.php?route=product/search&search=&limit=100")
        if resp is not None and resp.status_code == 200:
            report['apis'].append({'name': 'OpenCart search route', 'available': True,
                                   'products': None})
            report['strategy'] = report['strategy'] or 'opencart_routes'

    # Guidance for the user. Product *extraction* no longer depends on the
    # platform — it reads schema.org structured data, which every storefront
    # publishes — so an unrecognised platform is a discovery question now,
    # not an extraction one.
    if report['platform']['supported']:
        report['notes'].append(f"{report['platform']['name']} is fully supported.")
    elif pid == 'wordpress':
        report['notes'].append(
            'WordPress detected but no store plugin was identified. Product details will be read '
            'from the structured data on each page.')
    elif pid != 'unknown':
        report['notes'].append(
            f"{report['platform']['name']} has no dedicated discovery strategy, so product URLs "
            f"come from sitemaps or a listing crawl. Product details are read from structured "
            f"data and should still be complete.")
    else:
        report['notes'].append(
            'Could not identify the platform. Product URLs will come from generic sitemap and '
            'listing-page discovery; product details are read from structured data.')

    if not report['product_sitemaps'] and not report['apis']:
        report['strategy'] = 'listing_crawl'
        report['warnings'].append(
            'No product sitemap or API found. Products will be discovered by crawling listing '
            'pages, which is slower and may miss items.')

    return report
