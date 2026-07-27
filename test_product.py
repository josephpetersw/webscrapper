from scraper.client import ScraperClient
import json

if __name__ == '__main__':
    client = ScraperClient()
    print("Fetching sample product...")
    soup = client.fetch_soup('https://www.phoneplacekenya.com/product/samsung-galaxy-s24-ultra/')
    if soup:
        data = {}
        title_elem = soup.find('h1', class_='product_title')
        data['title'] = title_elem.text.strip() if title_elem else ''

        short_desc_elem = soup.find('div', class_='woocommerce-product-details__short-description')
        data['short_description'] = str(short_desc_elem) if short_desc_elem else ''

        long_desc_elem = soup.find('div', id='tab-description')
        data['long_description'] = str(long_desc_elem) if long_desc_elem else ''
        
        # Categories
        cat_elems = soup.select('.posted_in a')
        data['categories'] = [cat.text.strip() for cat in cat_elems]
        
        # Images
        img_elems = soup.select('.woocommerce-product-gallery__wrapper img')
        data['images'] = [img.get('src') for img in img_elems]

        # Price
        price_elem = soup.select_one('.price .woocommerce-Price-amount bdi')
        data['price'] = price_elem.text.strip() if price_elem else ''
        
        print(json.dumps(data, indent=2))
    else:
        print("Failed to fetch.")
