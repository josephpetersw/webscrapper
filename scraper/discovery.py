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
PRODUCT_URL_HINTS = ('/product/', '/produkt/', '/products/', '/item/', '/p/')
ARCHIVE_ROOT_PATHS = ('/shop/', '/store/', '/products/', '/shop', '/store', '/products')
SITEMAP_FALLBACK_PATHS = ('/sitemap_index.xml', '/sitemap.xml', '/wp-sitemap.xml',
                          '/product-sitemap.xml', '/sitemap.xml.gz')


def base_url(url):
    parsed = urlparse(url if '://' in url else f'https://{url}')
    return f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path.split('/')[0]}"


def _is_archive_root(url):
    path = urlparse(url).path.rstrip('/')
    return (path or '/') in [p.rstrip('/') or '/' for p in ARCHIVE_ROOT_PATHS]


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
        pages = [u for u in locs if '/wp-content/' not in u and not _is_archive_root(u)]
        all_leaf_urls.extend(pages)
        if is_product_sitemap:
            logger.info(f"Product sitemap {sitemap_url} -> {len(pages)} product URL(s)")
            from_product_sitemaps.extend(pages)

    if from_product_sitemaps:
        return from_product_sitemaps, found_sitemaps
    # No sitemap advertised itself as products; guess from URL shape instead.
    guessed = [u for u in all_leaf_urls if any(h in u.lower() for h in PRODUCT_URL_HINTS)]
    return guessed, found_sitemaps


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


def discover_via_crawl(client, base, max_pages=150):
    """Last resort: walk listing pages and follow pagination, harvesting links.

    Used when a store publishes no usable sitemap or API. Slower and less
    complete than the other strategies, but it works on almost anything.
    """
    from bs4 import BeautifulSoup

    host = urlparse(base).netloc
    queue = [base + p for p in ('/shop/', '/store/', '/products/', '/product-category/', '/')]
    seen_pages, products = set(), set()

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
            href = urljoin(page, anchor['href'].split('#')[0])
            if urlparse(href).netloc != host:
                continue
            low = href.lower()
            if any(h in low for h in PRODUCT_URL_HINTS) and not _is_archive_root(href):
                products.add(href)
            elif ('/page/' in low or 'paged=' in low or 'product-category' in low) \
                    and href not in seen_pages and len(queue) < max_pages:
                queue.append(href)

    if products:
        logger.info(f"Listing crawl -> {len(products)} product URL(s) from {len(seen_pages)} page(s)")
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

    if platform['id'] == 'shopify':
        run('shopify_api', lambda: discover_via_shopify(client, base))
    if platform['id'] in ('woocommerce', 'wordpress'):
        run('woocommerce_api', lambda: discover_via_woo_store_api(client, base))
        if not strategies.get('woocommerce_api'):
            run('wp_rest_api', lambda: discover_via_wp_rest(client, base))

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
        'warnings': [],
        'notes': [],
    }

    homepage = client.fetch_page(base, retries=1)
    if not homepage:
        report['warnings'].append(
            'Could not fetch the homepage. The site may be down, or blocking automated requests '
            '(for example with an interactive Cloudflare challenge).')
        return report

    report['reachable'] = True
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

    if pid == 'shopify':
        text = client.fetch_page(f"{base}/products.json?limit=250", retries=1)
        data = _safe_json(text) if text else None
        if isinstance(data, dict) and isinstance(data.get('products'), list):
            count = len(data['products'])
            report['apis'].append({'name': 'Shopify products.json', 'available': True,
                                   'products': count if count < 250 else None})
            report['strategy'] = report['strategy'] or 'shopify_api'
            if count < 250:
                report['estimated_products'] = count

    # Guidance for the user
    if report['platform']['supported']:
        report['notes'].append(f"{report['platform']['name']} is fully supported.")
    elif pid == 'wordpress':
        report['warnings'].append(
            'WordPress detected but no store plugin was identified. Scraping will still be '
            'attempted, but product pages may not match the expected structure.')
    elif pid != 'unknown':
        report['warnings'].append(
            f"{report['platform']['name']} is not explicitly supported yet. The scraper will fall "
            f"back to sitemap and listing-page discovery, and product details may be incomplete.")
    else:
        report['warnings'].append(
            'Could not identify the platform. The scraper will fall back to generic sitemap and '
            'listing-page discovery.')

    if not report['product_sitemaps'] and not report['apis']:
        report['strategy'] = 'listing_crawl'
        report['warnings'].append(
            'No product sitemap or API found. Products will be discovered by crawling listing '
            'pages, which is slower and may miss items.')

    return report
