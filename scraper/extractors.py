"""Platform-neutral ways to read a product out of a page.

The original parser keyed entirely off WooCommerce's default theme class
names. That covers a lot of stores and none of the rest: a Shopify theme, a
Django-Oscar shop, a Next.js storefront or a Woo store on a heavily customised
theme all render a product with completely different markup, and the parser
came back empty on every one of them.

What those pages *do* all have is structured data — schema.org ``Product`` as
JSON-LD, Open Graph product meta tags, or RDFa/microdata ``itemprop``
attributes. Every store that wants to appear correctly in Google search results
publishes at least one of them, which in practice means all of them do.

So this module extracts from the structured data first and leaves the
theme-specific CSS selectors as a fallback, rather than the other way round.
Every function is total: it returns empty values rather than raising, because
one malformed JSON-LD block must never cost us the whole product.
"""

import html as html_module
import json
import logging
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# ── schema.org type matching ─────────────────────────────────────────────
# Types are written as 'Product', 'schema:Product', 'http://schema.org/Product'
# and subtypes like 'IndividualProduct' or 'ProductGroup'. Compare on the last
# path segment, case-insensitively.
_PRODUCT_TYPES = {'product', 'individualproduct', 'productmodel', 'productgroup',
                  'vehicle', 'book', 'softwareapplication', 'itempage'}
_BREADCRUMB_TYPES = {'breadcrumblist'}

_CURRENCY_SYMBOLS = {
    'KES': 'KSh', 'USD': '$', 'EUR': '€', 'GBP': '£', 'UGX': 'USh',
    'TZS': 'TSh', 'NGN': '₦', 'ZAR': 'R', 'INR': '₹',
}

# Money as it appears in markup: 'KSh 12,999.00', '12999', 'Ksh12,999'
_PRICE_RE = re.compile(r'(\d[\d.,\s]*\d|\d)')
_CURRENCY_RE = re.compile(
    r'(KSh|Ksh|KES|USD|EUR|GBP|UGX|TZS|NGN|ZAR|INR|\$|€|£|₦|₹)', re.IGNORECASE)


def _type_names(node):
    raw = node.get('@type') if isinstance(node, dict) else None
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    out = []
    for value in values:
        if isinstance(value, str):
            out.append(re.split(r'[/#:]', value.strip())[-1].lower())
    return out


def unescape(value):
    """Decode HTML entities, which JSON-LD payloads are full of.

    A site that writes ``"name": "Home &amp; Living"`` into its JSON-LD is
    double-encoding, but it is common enough that leaving it alone puts raw
    ``&amp;`` into titles, folder names and exports.
    """
    if not isinstance(value, str):
        return value
    out = html_module.unescape(value)
    # Two passes catches '&amp;amp;', which templating engines produce often.
    return html_module.unescape(out) if '&' in out else out


def _first_str(value):
    """Pull a plain string out of the several shapes schema.org allows."""
    if isinstance(value, str):
        return unescape(value).strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ('name', 'url', '@id', 'value', 'contentUrl'):
            got = value.get(key)
            if isinstance(got, (str, int, float)) and str(got).strip():
                return unescape(str(got)).strip()
        return ''
    if isinstance(value, list):
        for item in value:
            got = _first_str(item)
            if got:
                return got
    return ''


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ── JSON-LD ──────────────────────────────────────────────────────────────

def _loads_lenient(text):
    """Parse a JSON-LD block, tolerating the ways sites break them."""
    if not text:
        return None
    text = text.strip()
    # CDATA wrappers and HTML comments are both common in WP output.
    text = re.sub(r'^<!\[CDATA\[|\]\]>$', '', text).strip()
    text = re.sub(r'^<!--|-->$', '', text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Trailing commas are the single most common hand-rolled-template mistake.
    try:
        return json.loads(re.sub(r',\s*([}\]])', r'\1', text))
    except Exception:
        return None


def jsonld_nodes(soup):
    """Every JSON-LD node on the page, with @graph containers flattened out."""
    nodes = []
    for tag in soup.find_all('script', type=lambda t: t and 'ld+json' in t.lower()):
        data = _loads_lenient(tag.string or tag.get_text() or '')
        if data is None:
            continue
        stack = [data]
        seen = 0
        while stack and seen < 200:
            current = stack.pop()
            seen += 1
            if isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, dict):
                nodes.append(current)
                if isinstance(current.get('@graph'), (list, dict)):
                    stack.extend(_as_list(current['@graph']))
                # Some themes nest the Product inside a WebPage's mainEntity.
                for key in ('mainEntity', 'mainEntityOfPage', 'item', 'about'):
                    nested = current.get(key)
                    if isinstance(nested, dict):
                        stack.append(nested)
    return nodes


def jsonld_product(nodes):
    """The best Product node on the page.

    Pages carry Organization, WebSite, BreadcrumbList and often several
    Products (related-items carousels). Prefer the node with the most fields
    filled in, which is reliably the page's own product rather than a teaser.
    """
    candidates = [n for n in nodes if _PRODUCT_TYPES & set(_type_names(n))]
    products = [n for n in candidates if 'product' in ' '.join(_type_names(n))]
    pool = products or candidates
    if not pool:
        return None

    def score(node):
        return sum(1 for key in ('name', 'offers', 'image', 'description', 'sku', 'brand')
                   if node.get(key))

    return max(pool, key=score)


def jsonld_breadcrumbs(nodes):
    """Category trail from a BreadcrumbList node, ordered and Home-stripped."""
    for node in nodes:
        if not (_BREADCRUMB_TYPES & set(_type_names(node))):
            continue
        items = []
        for element in _as_list(node.get('itemListElement')):
            if not isinstance(element, dict):
                continue
            name = _first_str(element.get('name')) or _first_str(element.get('item'))
            position = element.get('position')
            try:
                position = int(position)
            except (TypeError, ValueError):
                position = len(items) + 1
            if name:
                items.append((position, name))
        if items:
            items.sort(key=lambda pair: pair[0])
            return [name for _, name in items]
    return []


def offers_from(node):
    """(display_price, numeric_price, currency, availability) from a Product node.

    ``offers`` is variously a dict, a list of dicts, or an AggregateOffer with
    lowPrice/highPrice instead of price.
    """
    price_value, currency, availability = None, '', ''
    for offer in _as_list(node.get('offers')):
        if not isinstance(offer, dict):
            continue
        raw = (offer.get('price') if offer.get('price') not in (None, '')
               else offer.get('lowPrice'))
        if raw in (None, '') and isinstance(offer.get('priceSpecification'), dict):
            raw = offer['priceSpecification'].get('price')
        value = to_number(raw)
        if value is not None and (price_value is None or value < price_value):
            price_value = value
        currency = currency or _first_str(offer.get('priceCurrency'))
        availability = availability or _first_str(offer.get('availability'))
    availability = re.split(r'[/#]', availability)[-1] if availability else ''
    return format_price(price_value, currency), price_value, currency.upper(), availability


def to_number(raw):
    """Best-effort float from whatever a site put in a price field."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    digits = re.sub(r'[\s,]', '', match.group(1))
    # A lone trailing '.00' is decimals; '1.234.567' is thousands separators.
    if digits.count('.') > 1:
        digits = digits.replace('.', '')
    try:
        return float(digits)
    except ValueError:
        return None


def format_price(value, currency):
    if value is None:
        return ''
    symbol = _CURRENCY_SYMBOLS.get((currency or '').upper(), (currency or '').upper())
    amount = f'{value:,.2f}'.rstrip('0').rstrip('.') if value % 1 else f'{value:,.0f}'
    return f'{symbol} {amount}'.strip()


def split_price_text(text):
    """(currency_code, numeric) out of a rendered price like 'KSh 12,999.00'."""
    if not text:
        return '', None
    match = _CURRENCY_RE.search(text)
    code = ''
    if match:
        token = match.group(1).upper()
        code = {'KSH': 'KES', '$': 'USD', '€': 'EUR', '£': 'GBP',
                '₦': 'NGN', '₹': 'INR'}.get(token, token)
    return code, to_number(text)


# ── Open Graph / meta tags ───────────────────────────────────────────────

def meta_content(soup, *keys):
    """First non-empty <meta> content for any of these property/name keys."""
    for key in keys:
        for attr in ('property', 'name', 'itemprop'):
            tag = soup.find('meta', attrs={attr: key})
            if tag:
                content = (tag.get('content') or '').strip()
                if content:
                    return content
    return ''


def meta_contents(soup, *keys):
    """All values for these meta keys — og:image is routinely repeated."""
    out = []
    for key in keys:
        for attr in ('property', 'name', 'itemprop'):
            for tag in soup.find_all('meta', attrs={attr: key}):
                content = (tag.get('content') or '').strip()
                if content and content not in out:
                    out.append(content)
    return out


# ── Microdata / RDFa ─────────────────────────────────────────────────────

def microdata(soup, prop):
    """Value of an itemprop, reading the attribute the tag actually uses."""
    for tag in soup.select(f'[itemprop="{prop}"]'):
        for attr in ('content', 'value', 'href', 'src', 'data-src'):
            value = tag.get(attr)
            if value and str(value).strip():
                return str(value).strip()
        text = tag.get_text(strip=True)
        if text:
            return text
    return ''


def _host(url):
    """Hostname of a URL, or '' — used to spot self-referential values."""
    try:
        from urllib.parse import urlparse
        return urlparse(url or '').netloc.lower()
    except Exception:
        return ''


def absolutize(url, base):
    """Resolve a possibly protocol-relative or root-relative URL."""
    if not url:
        return ''
    url = url.strip()
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith(('http://', 'https://')):
        return url
    if url.startswith('data:'):
        return ''
    try:
        return urljoin(base, url)
    except Exception:
        return ''
