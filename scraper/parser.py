"""Turn a product page into a record, whatever the storefront is built on.

Extraction is layered, and the layers are ordered by how universal they are:

1. **schema.org structured data** — JSON-LD, then microdata/RDFa. Every store
   that wants Google to show a rich result publishes this, so it is the only
   source that is present on Shopify, WooCommerce, Magento, Django-Oscar and
   bespoke Next.js storefronts alike.
2. **Open Graph meta tags** — nearly as universal, and survives themes that
   rename every CSS class.
3. **Theme CSS selectors** — WooCommerce defaults plus the common alternates.
   Precise when they match, useless when they don't.

Each field takes the first layer that yields a value, so a store that publishes
half its data as JSON-LD and the other half only in markup still comes out
complete. ``extracted_by`` records which layer won for each field, which is the
fastest way to tell a "this store needs new selectors" problem from a "this
store blocked us" one.

Nothing in here raises: a page that defeats every layer returns a record with
empty fields, and the caller decides what to do about it.
"""

import html as html_module
import logging
import re

from bs4 import BeautifulSoup

from . import extractors as ex
from . import schema

logger = logging.getLogger(__name__)

# ── Selector tables ──────────────────────────────────────────────────────
# Ordered most-specific first. These are the *fallback* layer — structured
# data is tried before any of them — so it is safe for them to be broad.

TITLE_SELECTORS = (
    'h1.product_title',                    # WooCommerce default
    'h1.product-title', '.product-title h1', '.product_title',
    'h1.page-title', '.page-title .base',  # Magento
    'h1[itemprop="name"]', '.product-name h1', 'h1.entry-title',
    'h1.product-single__title', '.product__title h1',  # Shopify themes
    'h1.product-detail-name', '#product-title',
    # A bare 'h1' deliberately does not belong here: on plenty of themes the
    # only h1 is the store name. It is tried last of all, after Open Graph and
    # microdata, in the fallback chain in _parse_product_inner.
)

PRICE_SELECTORS = (
    '.summary .price ins .woocommerce-Price-amount bdi',   # Woo sale price
    '.summary .price .woocommerce-Price-amount bdi',
    '.price ins .woocommerce-Price-amount bdi',
    '.price .woocommerce-Price-amount bdi',
    '.woocommerce-Price-amount bdi', '.woocommerce-Price-amount',
    '[data-price-amount]', '.price-wrapper .price', '.product-info-price .price',
    '.product__price .price-item--sale', '.product__price', '.price-item--regular',
    '.product-price', '.price-box .price', '.current-price', '.price',
)

BREADCRUMB_SELECTORS = (
    '.woocommerce-breadcrumb', '.breadcrumbs-container', 'nav.woocommerce-breadcrumb',
    '.rank-math-breadcrumb', '.yoast-breadcrumb', '#breadcrumbs', '.breadcrumbs',
    'nav.breadcrumb', 'ol.breadcrumb', 'ul.breadcrumb', '.breadcrumb',
    '.page-breadcrumb', '[class*="breadcrumb"]',
)

GALLERY_SELECTORS = (
    '.woocommerce-product-gallery__wrapper img',       # WooCommerce default
    '.woocommerce-product-gallery img',
    '.product-gallery img', '.product-images img', '.product-image-gallery img',
    '.flex-control-thumbs img', '.product__media img', '.product-single__media img',
    '.fotorama img', '.gallery-placeholder img',       # Magento
    '.swiper-wrapper .product-image img',
    '[data-product-images] img', '#product-images img',
    '.product-detail-images img', '.thumbnails img',
)

SHORT_DESC_SELECTORS = (
    '.woocommerce-product-details__short-description',
    '.product-short-description', '.short-description', '.product__description--short',
    '.summary [itemprop="description"]',
)

LONG_DESC_SELECTORS = (
    '#tab-description', '.woocommerce-Tabs-panel--description',
    '#description', '.product-description', '.description-tab',
    '.product__description', '.product-single__description',
    '.product.attribute.description .value',            # Magento
    '[itemprop="description"]', '.tab-content .description',
)

BRAND_SELECTORS = (
    '[itemprop="brand"]',
    '.woocommerce-product-attributes-item--attribute_pa_brand td',
    '.brand a', '.product-brand', '.product-brand-name',
)

SKU_SELECTORS = ('.sku', '[itemprop="sku"]', '.product_meta .sku', '.product-sku')

# Image attributes in the order a lazy-loading theme actually populates them.
IMAGE_ATTRS = ('data-large_image', 'data-o_src', 'data-full-url', 'data-zoom-image',
               'data-src', 'data-lazy-src', 'data-original', 'data-image', 'src')

# Chrome, spacers and UI furniture that live inside gallery markup.
_JUNK_IMAGE_TOKENS = ('placeholder', 'spacer', 'blank.gif', 'loading.gif', 'lazy.gif',
                      'woocommerce-placeholder', '/loader', 'transparent.png',
                      'data:image', 'sprite', '/flags/', 'payment', 'trustbadge')

# Words that dress a brand up as a category name: 'Samsung Phones in Kenya'.
_CATEGORY_NOISE = re.compile(
    r'\b(phones?|smartphones?|laptops?|tablets?|tvs?|televisions?|accessories|'
    r'products?|shop|store|collection|in kenya|kenya)\b', re.IGNORECASE)

# Navigation crumbs that are not categories.
_BREADCRUMB_FILLER = {
    'home', 'homepage', 'shop', 'store', 'all', 'all products', 'products',
    'categories', 'category', 'catalogue', 'catalog', 'browse', 'main',
    'index', 'you are here', 'back',
}


def _clean_text(value):
    return re.sub(r'\s+', ' ', (value or '')).strip()


def _comparison_key(value):
    """Lowercase alphanumeric form, for comparing names that differ only in
    punctuation or entity encoding ('AX-5400' vs 'AX 5400', '&' vs '&amp;')."""
    return re.sub(r'[^a-z0-9]', '', ex.unescape(value or '').lower())


def _select_one_text(soup, selectors):
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node:
            text = _clean_text(node.get_text(' ', strip=True))
            if text:
                return text, selector
    return '', None


def _select_one_html(soup, selectors):
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node and node.get_text(strip=True):
            return str(node), selector
    return '', None


def _largest_from_srcset(value):
    """Biggest candidate in a srcset, by width or pixel-density descriptor."""
    best, best_score = '', -1.0
    for candidate in (value or '').split(','):
        parts = candidate.strip().split()
        if not parts:
            continue
        url = parts[0]
        score = 1.0
        if len(parts) > 1:
            descriptor = parts[1].strip().lower()
            try:
                score = float(descriptor[:-1]) if descriptor[-1] in 'wx' else 1.0
            except ValueError:
                score = 1.0
        if score > best_score:
            best, best_score = url, score
    return best


def brand_from_context(title, categories):
    """Brand inferred from a product's own category trail and title.

    The last resort, for pages publishing no brand of their own in structured
    data, meta tags or markup. Split out of the parser so a repair of an
    already-scraped catalogue cannot drift from what the parser decides today.
    Returns ``(brand, source)``, or ``('', None)`` when nothing can be trusted.
    """
    if categories:
        category = categories[-1]
        candidate = _CATEGORY_NOISE.sub('', category).strip(' -–|,')
        if candidate and _comparison_key(candidate) != _comparison_key(category):
            return candidate, 'category-heuristic'

    words = (title or '').split()
    if words and words[0][:1].isalpha():
        return words[0], 'title-first-word'
    return '', None


def _is_junk_image(url):
    low = (url or '').lower()
    return (not low) or any(token in low for token in _JUNK_IMAGE_TOKENS)


_SIZE_SUFFIX = re.compile(r'-(\d+)x(\d+)(?=\.[a-z0-9]{2,5}$)', re.IGNORECASE)


def dedupe_image_variants(urls):
    """Collapse resizes of one picture onto a single URL, preserving order.
    """
    best, order = {}, []
    for url in urls or []:
        if not url:
            continue
        match = _SIZE_SUFFIX.search(url)
        if match:
            base = _SIZE_SUFFIX.sub('', url)
            # An original outranks every resize; among resizes, biggest wins.
            score = (0, int(match.group(1)) * int(match.group(2)))
        else:
            base, score = url, (1, 0)
        if base not in best:
            order.append(base)
            best[base] = (score, url)
        elif score > best[base][0]:
            best[base] = (score, url)
    return [best[base][1] for base in order]


def _image_from_tag(tag, base_url):
    """Best available source for one <img>, preferring the full-size variant."""
    for attr in ('data-srcset', 'srcset'):
        value = tag.get(attr)
        if value:
            candidate = ex.absolutize(_largest_from_srcset(value), base_url)
            if candidate and not _is_junk_image(candidate):
                return candidate
    for attr in IMAGE_ATTRS:
        value = tag.get(attr)
        if value:
            candidate = ex.absolutize(str(value).split()[0], base_url)
            if candidate and not _is_junk_image(candidate):
                return candidate
    return ''


class Parser:
    # ── sitemaps ─────────────────────────────────────────────────────────

    @staticmethod
    def parse_sitemap(xml_content):
        """Extract all <loc> URLs from a sitemap XML."""
        if not xml_content:
            return []
        try:
            soup = BeautifulSoup(xml_content, 'xml')
            locs = [loc.text.strip() for loc in soup.find_all('loc') if loc.text]
        except Exception as e:
            logger.debug(f'Sitemap parse failed ({e}); falling back to regex')
            locs = []
        if not locs:
            # Malformed or oddly-namespaced sitemaps still yield to a plain scan.
            locs = [m.strip() for m in re.findall(r'<loc>\s*(.*?)\s*</loc>',
                                                  xml_content, re.DOTALL | re.IGNORECASE)]
        return [l for l in locs if l]

    # ── products ─────────────────────────────────────────────────────────

    @staticmethod
    def parse_product(html_content, url):
        """Extract product details from HTML. Returns a dict, or None if no HTML."""
        if not html_content:
            return None
        try:
            return schema.normalize(Parser._parse_product_inner(html_content, url))
        except Exception as e:
            # A single unparseable page must never take down a run; return the
            # shell so the caller records it as "no data" rather than a crash.
            logger.error(f'Parser failed on {url}: {type(e).__name__}: {e}')
            record = schema.blank_record(url)
            record['extracted_by'] = {'error': type(e).__name__}
            return record

    @staticmethod
    def _parse_product_inner(html_content, url):
        soup = BeautifulSoup(html_content, 'lxml')
        # The raw response is handed over too: lxml drops anything after
        # </html>, which is where some sites put their structured data.
        nodes = ex.jsonld_nodes(soup, html_content)
        product = ex.jsonld_product(nodes) or {}
        origin = url or ''
        sources = {}

        data = {'url': url}

        # ── title ────────────────────────────────────────────────────────
        title, selector = _select_one_text(soup, TITLE_SELECTORS)
        if title:
            sources['title'] = f'css:{selector}'
        if not title:
            title = ex.first_str(product.get('name'))
            if title:
                sources['title'] = 'jsonld'
        if not title:
            title = ex.meta_content(soup, 'og:title', 'twitter:title')
            if title:
                sources['title'] = 'opengraph'
        if not title:
            title = ex.microdata(soup, 'name')
            if title:
                sources['title'] = 'microdata'
        if not title:
            # Last resort: a bare <h1>, then the document title with the site
            # name (' - Store', ' | Store') trimmed off.
            node = soup.find('h1')
            title = _clean_text(node.get_text(' ', strip=True)) if node else ''
            if title:
                sources['title'] = 'h1'
            elif soup.title:
                title = re.split(r'\s+[|–—-]\s+', _clean_text(soup.title.get_text()))[0]
                if title:
                    sources['title'] = 'document-title'
        data['title'] = _clean_text(title)

        # ── price ────────────────────────────────────────────────────────
        display, value, currency, availability = ex.offers_from(product)
        if value is not None:
            sources['price'] = 'jsonld'
        if value is None:
            meta_price = ex.meta_content(soup, 'product:price:amount', 'og:price:amount')
            value = ex.to_number(meta_price)
            currency = currency or ex.meta_content(
                soup, 'product:price:currency', 'og:price:currency')
            if value is not None:
                display = ex.format_price(value, currency)
                sources['price'] = 'opengraph'
        if value is None:
            text, selector = _select_one_text(soup, PRICE_SELECTORS)
            if text:
                # A price range ('KSh 100 – KSh 200') renders both ends; the
                # first is the one a listing would show.
                text = re.split(r'[–—]|\.\.\.', text)[0]
                code, value = ex.split_price_text(text)
                currency = currency or code
                # Kept even when no number could be parsed: 'Call for price' is
                # more use to a reader than a blank cell. The source is recorded
                # either way, so extracted_by never disagrees with the field.
                display = _clean_text(text)
                sources['price'] = f'css:{selector}'
        if value is None:
            micro = ex.microdata(soup, 'price')
            value = ex.to_number(micro)
            if value is not None:
                currency = currency or ex.microdata(soup, 'priceCurrency')
                display = ex.format_price(value, currency)
                sources['price'] = 'microdata'

        data['price'] = display or ''
        data['price_value'] = value
        data['currency'] = (currency or '').upper()

        # ── availability ─────────────────────────────────────────────────
        if not availability:
            availability = re.split(r'[/#]', ex.microdata(soup, 'availability'))[-1]
        data['availability'] = availability or ''
        low = (availability or '').lower()
        data['in_stock'] = (True if 'instock' in low or 'in_stock' in low or 'limited' in low
                            else False if 'outofstock' in low or 'soldout' in low
                            else None)

        # ── categories ───────────────────────────────────────────────────
        categories = Parser._clean_breadcrumbs(ex.jsonld_breadcrumbs(nodes), data['title'])
        if categories:
            sources['categories'] = 'jsonld'
        else:
            markup, selector = Parser._breadcrumbs_from_markup(soup)
            categories = Parser._clean_breadcrumbs(markup, data['title'])
            if categories:
                sources['categories'] = f'css:{selector}'
        data['categories'] = categories

        # ── brand ────────────────────────────────────────────────────────
        # Many stores put their own name in the brand field. That is worse than
        # useless — every product in the catalogue collapses to one brand — so
        # a brand that matches the store's own domain is rejected and the next
        # layer gets a turn.
        site_key = _comparison_key(re.sub(r'^www\.|\.[a-z.]+$', '', ex.host_of(origin)))
        brand = ex.first_str(product.get('brand')) or ex.first_str(product.get('manufacturer'))
        if brand and site_key and _comparison_key(brand) == site_key:
            brand = ''
        if brand:
            sources['brand'] = 'jsonld'
        if not brand:
            brand = ex.meta_content(soup, 'product:brand', 'og:brand')
            if brand:
                sources['brand'] = 'opengraph'
        if not brand:
            text, selector = _select_one_text(soup, BRAND_SELECTORS)
            if text and len(text) < 40:
                brand, sources['brand'] = text, f'css:{selector}'
        if not brand:
            brand, via = brand_from_context(data['title'], categories)
            if brand:
                sources['brand'] = via
        data['brand'] = _clean_text(brand) or 'Unknown'

        # ── sku ──────────────────────────────────────────────────────────
        sku = ex.first_str(product.get('sku')) or ex.first_str(product.get('mpn'))
        if not sku:
            sku = ex.microdata(soup, 'sku')
        if not sku:
            text, _ = _select_one_text(soup, SKU_SELECTORS)
            sku = text
        data['sku'] = _clean_text(sku)[:80]

        # ── descriptions ─────────────────────────────────────────────────
        short_html, selector = _select_one_html(soup, SHORT_DESC_SELECTORS)
        if short_html:
            sources['short_description'] = f'css:{selector}'
        else:
            fallback = (ex.first_str(product.get('description'))
                        or ex.meta_content(soup, 'og:description', 'description'))
            if fallback:
                # Re-escape: this came out of a JSON string or meta attribute,
                # and downstream consumers treat the field as HTML.
                short_html = f'<p>{html_module.escape(fallback)}</p>'
                sources['short_description'] = 'jsonld/meta'
        data['short_description'] = short_html

        long_html, selector = _select_one_html(soup, LONG_DESC_SELECTORS)
        if long_html:
            sources['long_description'] = f'css:{selector}'
        data['long_description'] = long_html

        # ── images ───────────────────────────────────────────────────────
        images, image_source = Parser._extract_images(soup, product, origin)
        data['images'] = images
        if images:
            sources['images'] = image_source

        data['extracted_by'] = sources
        return data

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _breadcrumbs_from_markup(soup):
        for selector in BREADCRUMB_SELECTORS:
            try:
                container = soup.select_one(selector)
            except Exception:
                continue
            if not container:
                continue
            names = [_clean_text(a.get_text(' ', strip=True))
                     for a in container.find_all('a')]
            names = [n for n in names if n]
            if names:
                return names, selector
        return [], None

    @staticmethod
    def _clean_breadcrumbs(names, title):
        """Drop the Home link, navigation filler, the product itself, and repeats.

        The last crumb on a product page is nearly always the product's own
        name. Left in, it becomes a category — which then becomes a directory
        per product in the structured output, and a junk entry in the category
        list the dashboard shows.
        """
        out, seen = [], set()
        title_key = _comparison_key(title)
        for name in names:
            name = ex.unescape(_clean_text(name))
            low = name.lower().strip(' .')
            if not low or low in _BREADCRUMB_FILLER:
                continue
            key = _comparison_key(name)
            if title_key and key and (key == title_key
                                      or (len(key) > 20 and (key in title_key or title_key in key))):
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out

    @staticmethod
    def _extract_images(soup, product, origin):
        """Gallery images, preferring structured data then theme galleries."""
        images, source = [], None

        def add(candidates):
            added = False
            for candidate in candidates:
                candidate = ex.absolutize(candidate, origin)
                if not candidate or _is_junk_image(candidate):
                    continue
                # Strip cache-busting/resize query strings to get the original.
                candidate = candidate.split('?')[0]
                if candidate not in images:
                    images.append(candidate)
                    added = True
            return added

        # 1. JSON-LD: str, list, or ImageObject(s).
        jsonld_images = []
        for item in ex.as_list(product.get('image')):
            got = ex.first_str(item)
            if got:
                jsonld_images.append(got)
        if add(jsonld_images):
            source = 'jsonld'

        # 2. Theme gallery markup — the only layer that reliably yields *all*
        #    the shots rather than just the primary one.
        for selector in GALLERY_SELECTORS:
            try:
                tags = soup.select(selector)
            except Exception:
                continue
            if not tags:
                continue
            found = [_image_from_tag(tag, origin) for tag in tags]
            if add([f for f in found if f]):
                source = source or f'css:{selector}'
            if len(images) > 1:
                break

        # 3. Open Graph — usually one image, but present when all else fails.
        if not images:
            if add(ex.meta_contents(soup, 'og:image', 'og:image:secure_url', 'twitter:image')):
                source = 'opengraph'

        # 4. Microdata, then the largest image inside the main product region.
        if not images:
            if add([ex.microdata(soup, 'image')]):
                source = 'microdata'

        if not images:
            region = (soup.select_one('.product, #product, [itemtype*="Product"], main')
                      or soup)
            found = [_image_from_tag(tag, origin) for tag in region.find_all('img')[:12]]
            if add([f for f in found if f]):
                source = 'content-scan'

        return dedupe_image_variants(images)[:30], source
