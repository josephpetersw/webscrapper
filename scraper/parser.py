import json
import logging
import re

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# WooCommerce resized-copy suffix, e.g. photo-300x300.jpg
_IMG_SIZE_SUFFIX = re.compile(r'-(\d+)x(\d+)(?=\.\w{3,4}$)')


class Parser:
    @staticmethod
    def parse_sitemap(xml_content):
        """Extract all <loc> URLs from a sitemap XML"""
        if not xml_content:
            return []
        soup = BeautifulSoup(xml_content, 'xml')
        return [loc.text for loc in soup.find_all('loc')]

    @staticmethod
    def _extract_json_ld_product(soup):
        """WooCommerce embeds a schema.org Product block — cheaper and more
        reliable than scraping the rendered markup for price/brand/sku."""
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                payload = json.loads(script.string or '')
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                nodes = payload.get('@graph', [payload])
            elif isinstance(payload, list):
                nodes = payload
            else:
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = node.get('@type')
                types = node_type if isinstance(node_type, list) else [node_type]
                if 'Product' in types:
                    return node
        return None

    @staticmethod
    def _dedupe_image_variants(urls):
        """Collapse resized copies of the same image onto a single URL,
        preferring the original (no -WxH suffix), else the largest variant.
        Only URLs actually seen on the page are returned."""
        groups = {}
        for url in urls:
            base = _IMG_SIZE_SUFFIX.sub('', url)
            m = _IMG_SIZE_SUFFIX.search(url)
            area = int(m.group(1)) * int(m.group(2)) if m else float('inf')
            best = groups.get(base)
            if best is None or area > best[0]:
                groups[base] = (area, url)
        return [url for _, url in groups.values()]

    @staticmethod
    def parse_product(html_content, url):
        """Extract product details from HTML"""
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, 'lxml')
        data = {'url': url}

        title_elem = soup.find('h1', class_='product_title')
        data['title'] = title_elem.text.strip() if title_elem else ''

        short_desc_elem = soup.find('div', class_='woocommerce-product-details__short-description')
        data['short_description'] = str(short_desc_elem) if short_desc_elem else ''

        long_desc_elem = soup.find('div', id='tab-description')
        data['long_description'] = str(long_desc_elem) if long_desc_elem else ''

        # Categories via breadcrumbs
        breadcrumbs = soup.find('div', class_='breadcrumbs-container')
        if breadcrumbs:
            cats = [a.text.strip() for a in breadcrumbs.find_all('a')]
            if cats and cats[0].lower() == 'home':
                cats = cats[1:]
            data['categories'] = cats
        else:
            data['categories'] = []

        # Brand heuristic from categories ('Realme Phones' -> 'Realme'),
        # overridden below when JSON-LD carries a real brand field.
        brand = "Unknown"
        if data['categories']:
            last_cat = data['categories'][-1]
            brand = last_cat.replace('Phones', '').replace('Smartphones', '').replace('in Kenya', '').strip()
        elif data.get('title'):
            brand = data['title'].split()[0]
        data['brand'] = brand

        # Images (handle lazy loading)
        images = set()
        img_elems = soup.select('.woocommerce-product-gallery__wrapper img')
        for img in img_elems:
            src = img.get('data-src') or img.get('data-large_image') or img.get('src')
            if src and not src.startswith('data:'):
                # Clean query params from image URL to get the original file
                clean_src = src.split('?')[0]
                images.add(clean_src)
        data['images'] = Parser._dedupe_image_variants(images)

        price_elem = soup.select_one('.price .woocommerce-Price-amount bdi')
        data['price'] = price_elem.text.strip() if price_elem else ''

        # Structured data: numeric price, currency, sku, authoritative brand
        ld = Parser._extract_json_ld_product(soup)
        if ld:
            if not data['title'] and ld.get('name'):
                data['title'] = str(ld['name']).strip()
            if ld.get('sku'):
                data['sku'] = str(ld['sku'])
            offers = ld.get('offers')
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                price = offers.get('price', offers.get('lowPrice'))
                if price is not None:
                    data['price_amount'] = str(price)
                if offers.get('priceCurrency'):
                    data['currency'] = offers['priceCurrency']
            ld_brand = ld.get('brand')
            if isinstance(ld_brand, dict):
                ld_brand = ld_brand.get('name')
            if ld_brand:
                data['brand'] = str(ld_brand).strip()

        return data
