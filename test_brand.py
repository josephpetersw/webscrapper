from scraper.client import ScraperClient
from bs4 import BeautifulSoup
import json

urls = [
    'https://example-store.com/product/realme-5/',
    'https://example-store.com/product/samsung-galaxy-s23-ultra/',
    'https://example-store.com/product/apple-iphone-14-pro-max/'
]

client = ScraperClient()

for url in urls:
    html = client.fetch_page(url)
    if not html:
        print("Failed to fetch", url)
        continue
    
    soup = BeautifulSoup(html, 'lxml')
    breadcrumbs = soup.find('nav', class_='woocommerce-breadcrumb')
    cats = []
    if breadcrumbs:
        cats = [a.text.strip() for a in breadcrumbs.find_all('a')]
        if cats and cats[0].lower() == 'home':
            cats = cats[1:]
    print(url.split('/')[-2])
    print("Categories:", cats)
    
    # Check if there is a script tag with gtm4wp
    script_data = soup.find('script', string=lambda t: t and 'dataLayer.push' in t and 'ecommerce' in t)
    if script_data:
        try:
            # Extract JSON from dataLayer.push({...});
            json_str = script_data.text.split('dataLayer.push(')[1].rsplit(')', 1)[0]
            data = json.loads(json_str)
            item = data.get('ecommerce', {}).get('items', [{}])[0]
            print("GTM Brand:", item.get('item_brand'))
            print("GTM Category:", item.get('item_category'))
            print("GTM Category2:", item.get('item_category2'))
        except Exception as e:
            print("GTM Error:", e)
            
    # Try looking for product-brand link in the page
    brand_link = soup.select_one('a[href*="/product-brand/"]')
    if brand_link:
        print("Brand link:", brand_link.get('href'))
    print("-" * 50)
