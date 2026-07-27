import asyncio
import json
import logging
import os
import argparse
from curl_cffi.requests import AsyncSession
from scraper.client import ScraperClient
from scraper.parser import Parser
from scraper.downloader import ImageDownloader

# Setup logging to both console and file
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

fh = logging.FileHandler('scraper.log')
fh.setFormatter(formatter)
logger.addHandler(fh)

def update_progress(current, total, eta=0):
    os.makedirs('data', exist_ok=True)
    with open('data/progress.json', 'w') as f:
        json.dump({'current': current, 'total': total, 'eta': eta}, f)

async def scrape_product(url, async_session, parser, downloader, semaphore, state):
    async with semaphore:
        client = ScraperClient()
        html = await client.fetch_page_async(async_session, url)
        if not html:
            state['completed'] += 1
            update_progress(state['completed'], state['total'])
            return None
            
        data = parser.parse_product(html, url)
        if data:
            import re
            
            # Determine primary category path
            cats = data.get('categories', [])
            cat_path = "Uncategorized"
            if cats:
                cat_path = "/".join([re.sub(r'[^a-zA-Z0-9]', '_', c).strip('_') for c in cats])
                
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', data.get('title') or url.split('/')[-2])
            safe_name = re.sub(r'_+', '_', safe_name).strip('_')
            
            structured_dir = os.path.join('data', 'structured', cat_path, safe_name)
            os.makedirs(structured_dir, exist_ok=True)
            
            # Save descriptions
            if data.get('long_description'):
                with open(os.path.join(structured_dir, 'description.md'), 'w', encoding='utf-8') as f:
                    import markdownify
                    md = markdownify.markdownify(data['long_description'], heading_style="ATX")
                    f.write(md)
            if data.get('short_description'):
                with open(os.path.join(structured_dir, 'short_description.txt'), 'w', encoding='utf-8') as f:
                    from bs4 import BeautifulSoup
                    txt = BeautifulSoup(data['short_description'], 'lxml').get_text(strip=True, separator='\n')
                    f.write(txt)
                    
            # Save data.json
            with open(os.path.join(structured_dir, 'data.json'), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            image_dir = os.path.join(structured_dir, 'images')
            
            # Download images for this product immediately
            image_tasks = []
            for img_url in data.get('images', []):
                image_tasks.append(
                    downloader.download_image(async_session, img_url, image_dir)
                )
            if image_tasks:
                await asyncio.gather(*image_tasks)
            
            # Save product immediately
            state['products'].append(data)
            for cat in data['categories']:
                state['categories'].add(cat)
                
            # Write to disk periodically (every 10 products or at the end)
            if len(state['products']) % 10 == 0:
                with open('data/products.json', 'w', encoding='utf-8') as f:
                    json.dump(state['products'], f, indent=2, ensure_ascii=False)
                with open('data/categories.json', 'w', encoding='utf-8') as f:
                    json.dump(list(state['categories']), f, indent=2, ensure_ascii=False)
                    
        state['completed'] += 1
        
        import time
        elapsed = time.time() - state['start_time']
        if state['completed'] > 0:
            avg_time = elapsed / state['completed']
            remaining = state['total'] - state['completed']
            eta_seconds = remaining * avg_time
        else:
            eta_seconds = 0
            
        update_progress(state['completed'], state['total'], eta_seconds)
        logger.info(f"Scraped product {state['completed']}/{state['total']}: {url}")
        return data

async def run_concurrent_scraper(product_urls, workers):
    parser = Parser()
    # Concurrency limit for image downloads
    downloader = ImageDownloader(base_dir='data/images', concurrency=workers)
    # Concurrency limit for product page fetching
    semaphore = asyncio.Semaphore(workers) 
    
    import time
    state = {
        'completed': 0,
        'total': len(product_urls),
        'products': [],
        'categories': set(),
        'start_time': time.time()
    }
    
    update_progress(0, state['total'], 0)
    
    async with AsyncSession(impersonate="chrome") as async_session:
        tasks = [
            scrape_product(url, async_session, parser, downloader, semaphore, state)
            for url in product_urls
        ]
        await asyncio.gather(*tasks)
        
    # Final save
    with open('data/products.json', 'w', encoding='utf-8') as f:
        json.dump(state['products'], f, indent=2, ensure_ascii=False)
    with open('data/categories.json', 'w', encoding='utf-8') as f:
        json.dump(list(state['categories']), f, indent=2, ensure_ascii=False)

def run_scraper(limit=None, target_url=None, workers=20):
    client = ScraperClient()
    parser = Parser()
    product_urls = []
    
    if target_url:
        logger.info(f"Target URL specified: {target_url}")
        product_urls.append(target_url)
    else:
        logger.info("Fetching sitemap index...")
        sitemap_index_xml = client.fetch_page('https://www.phoneplacekenya.com/sitemap_index.xml')
        all_sitemaps = parser.parse_sitemap(sitemap_index_xml)
        product_sitemaps = [s for s in all_sitemaps if 'product-sitemap' in s]
        
        for sitemap_url in product_sitemaps:
            logger.info(f"Parsing product sitemap: {sitemap_url}")
            xml = client.fetch_page(sitemap_url)
            urls = parser.parse_sitemap(xml)
            valid_urls = [u for u in urls if '/product/' in u and '/wp-content/' not in u]
            product_urls.extend(valid_urls)
            
        logger.info(f"Found {len(product_urls)} total product URLs.")
    
    if limit:
        product_urls = product_urls[:limit]
        logger.info(f"Limiting scrape to {limit} products.")

    logger.info(f"Starting concurrent scraping of {len(product_urls)} products with {workers} workers...")
    asyncio.run(run_concurrent_scraper(product_urls, workers))
    logger.info("Scraping finished successfully!")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PhonePlaceKenya Scraper')
    parser.add_argument('--limit', type=int, help='Limit the number of products to scrape', default=None)
    parser.add_argument('--target_url', type=str, help='Specific URL to scrape', default=None)
    parser.add_argument('--workers', type=int, help='Number of concurrent workers', default=20)
    args = parser.parse_args()
    
    run_scraper(limit=args.limit, target_url=args.target_url, workers=args.workers)
