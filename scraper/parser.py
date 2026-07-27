import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class Parser:
    @staticmethod
    def parse_sitemap(xml_content):
        """Extract all <loc> URLs from a sitemap XML"""
        if not xml_content:
            return []
        soup = BeautifulSoup(xml_content, 'xml')
        return [loc.text for loc in soup.find_all('loc')]

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

        # Extract Brand
        # Stores commonly name categories after the brand, e.g. 'Realme Phones'
        brand = "Unknown"
        if data['categories']:
            last_cat = data['categories'][-1]
            brand = last_cat.replace('Phones', '').replace('Smartphones', '').replace('in Kenya', '').strip()
        elif data.get('title'):
            # Fallback to the first word of the title (e.g., "Apple iPhone 14" -> "Apple")
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
        data['images'] = list(images)

        price_elem = soup.select_one('.price .woocommerce-Price-amount bdi')
        data['price'] = price_elem.text.strip() if price_elem else ''
        
        return data
