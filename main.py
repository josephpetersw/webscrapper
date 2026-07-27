import asyncio
import json
import logging
import os
import re
import time
import argparse
from urllib.parse import urlparse
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

# scraper.client calls logging.basicConfig(), which installs a root handler;
# without this every line would be emitted twice.
logger.propagate = False

DATA_DIR = 'data'
PRODUCTS_FILE = os.path.join(DATA_DIR, 'products.json')
CATEGORIES_FILE = os.path.join(DATA_DIR, 'categories.json')
FAILED_FILE = os.path.join(DATA_DIR, 'failed_urls.json')

DEFAULT_SITE = 'https://www.phoneplacekenya.com'

# Sitemaps whose name suggests they list products. WooCommerce/Yoast name these
# product-sitemap.xml, wp-sitemap-posts-product-1.xml, etc.
# Note: no 'item' here - the word "sitemap" itself contains "item", so it would
# match every sitemap on the site.
PRODUCT_SITEMAP_HINTS = ('product', 'produkt')
# ...but the same generators also emit product_cat-sitemap.xml, pa_colour-sitemap.xml
# and friends, which list category/tag/attribute archive pages rather than products.
TAXONOMY_SITEMAP_HINTS = ('product_cat', 'product-cat', 'product_tag', 'product-tag',
                          'category', 'categories', 'product_brand', 'product-brand',
                          'brand', 'tag', 'pa_', 'attribute')
# Fallback for sites whose sitemaps aren't helpfully named. Deliberately narrow:
# '/shop/' and '/store/' are excluded because they match catalogue index and
# filtered-archive pages far more often than individual products.
PRODUCT_URL_HINTS = ('/product/', '/produkt/', '/item/')
# Catalogue roots that show up inside product sitemaps but aren't products.
ARCHIVE_ROOT_PATHS = ('/shop/', '/store/', '/products/', '/shop', '/store', '/products')

SITEMAP_FALLBACK_PATHS = ('/sitemap_index.xml', '/sitemap.xml', '/wp-sitemap.xml', '/product-sitemap.xml')


def update_progress(current, total, eta=0):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, 'progress.json'), 'w') as f:
        json.dump({'current': current, 'total': total, 'eta': eta}, f)


def discover_product_urls(client, parser, site_url):
    """Walk a store's sitemaps and collect every product URL.

    Works against any WordPress/WooCommerce-style store: reads robots.txt for
    Sitemap: directives, falls back to the conventional sitemap locations, then
    walks sitemap indexes down to their leaf sitemaps.
    """
    parsed = urlparse(site_url if '://' in site_url else f'https://{site_url}')
    base = f"{parsed.scheme}://{parsed.netloc}"
    logger.info(f"Discovering sitemaps for {base} ...")

    queue = []
    robots = client.fetch_page(f"{base}/robots.txt", retries=1)
    if robots:
        for line in robots.splitlines():
            if line.strip().lower().startswith('sitemap:'):
                advertised = line.split(':', 1)[1].strip()
                if advertised:
                    queue.append(advertised)
        if queue:
            logger.info(f"robots.txt advertised {len(queue)} sitemap(s)")

    for path in SITEMAP_FALLBACK_PATHS:
        candidate = base + path
        if candidate not in queue:
            queue.append(candidate)

    seen_sitemaps = set()
    from_product_sitemaps = []   # URLs out of sitemaps explicitly listing products
    all_leaf_urls = []           # every page URL seen, used only as a fallback

    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        xml = client.fetch_page(sitemap_url, retries=1)
        if not xml:
            continue
        locs = parser.parse_sitemap(xml)
        if not locs:
            continue

        # A sitemap index points at more sitemaps; a urlset points at pages.
        if '<sitemapindex' in xml[:2000].lower():
            logger.info(f"Sitemap index {sitemap_url} -> {len(locs)} child sitemap(s)")
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

    # A store that publishes product sitemaps is authoritative about its own
    # catalogue, so trust those exclusively. Only when none exist do we fall back
    # to guessing products from URL shape.
    if from_product_sitemaps:
        candidates = from_product_sitemaps
    else:
        logger.info("No product sitemap found - falling back to URL pattern matching")
        candidates = [u for u in all_leaf_urls if any(h in u.lower() for h in PRODUCT_URL_HINTS)]

    return list(dict.fromkeys(candidates))


def _is_archive_root(url):
    path = urlparse(url).path.rstrip('/')
    return (path or '/') in [p.rstrip('/') or '/' for p in ARCHIVE_ROOT_PATHS]


def load_existing_products(resume):
    """Return (products, scraped_urls) from a previous run so it can be continued."""
    if not resume or not os.path.exists(PRODUCTS_FILE):
        return [], set()
    try:
        with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
            products = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read existing {PRODUCTS_FILE} ({e}) - starting fresh.")
        return [], set()
    # Only records that actually parsed count as done; empty/junk rows get re-scraped.
    valid = [p for p in products if p.get('url') and p.get('title')]
    return valid, {p['url'] for p in valid}


def save_results(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(state['products'], f, indent=2, ensure_ascii=False)
    with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(sorted(state['categories']), f, indent=2, ensure_ascii=False)
    with open(FAILED_FILE, 'w', encoding='utf-8') as f:
        json.dump(state['failed'], f, indent=2, ensure_ascii=False)


def mark_completed(state, url, note):
    state['completed'] += 1
    elapsed = time.time() - state['start_time']
    if state['completed'] > 0:
        avg_time = elapsed / state['completed']
        eta_seconds = (state['total'] - state['completed']) * avg_time
    else:
        eta_seconds = 0
    update_progress(state['completed'], state['total'], eta_seconds)
    logger.info(f"{note} {state['completed']}/{state['total']}: {url}")
    if state['completed'] % 10 == 0:
        save_results(state)


async def scrape_product(url, async_session, parser, downloader, semaphore, state):
    async with semaphore:
        html, reason = await state['client'].fetch_page_async(async_session, url)
        if not html:
            state['failed'].append({'url': url, 'reason': reason or 'fetch failed'})
            mark_completed(state, url, 'Failed')
            return None

        data = parser.parse_product(html, url)
        if not data or not data.get('title'):
            state['failed'].append({'url': url, 'reason': 'no product data found in page'})
            mark_completed(state, url, 'No data')
            return None

        # Determine primary category path
        cats = data.get('categories', [])
        cat_path = "Uncategorized"
        if cats:
            cat_path = "/".join([re.sub(r'[^a-zA-Z0-9]', '_', c).strip('_') for c in cats])

        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', data.get('title') or url.split('/')[-2])
        safe_name = re.sub(r'_+', '_', safe_name).strip('_')

        structured_dir = os.path.join(DATA_DIR, 'structured', cat_path, safe_name)
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
        image_tasks = [
            downloader.download_image(async_session, img_url, image_dir)
            for img_url in data.get('images', [])
        ]
        if image_tasks:
            await asyncio.gather(*image_tasks)

        state['products'].append(data)
        for cat in data.get('categories') or []:
            state['categories'].add(cat)

        mark_completed(state, url, 'Scraped product')
        return data


async def run_concurrent_scraper(product_urls, workers, existing_products=None):
    parser = Parser()
    # Concurrency limit for image downloads
    downloader = ImageDownloader(base_dir=os.path.join(DATA_DIR, 'images'), concurrency=workers)
    # Concurrency limit for product page fetching
    semaphore = asyncio.Semaphore(workers)

    existing_products = existing_products or []
    state = {
        'completed': 0,
        'total': len(product_urls),
        'products': list(existing_products),
        'categories': set(),
        'failed': [],
        'client': ScraperClient(),
        'start_time': time.time(),
    }
    for product in existing_products:
        for cat in product.get('categories') or []:
            state['categories'].add(cat)

    update_progress(0, state['total'], 0)

    async with AsyncSession(impersonate="chrome") as async_session:
        tasks = [
            scrape_product(url, async_session, parser, downloader, semaphore, state)
            for url in product_urls
        ]
        await asyncio.gather(*tasks)

    save_results(state)

    scraped_now = len(state['products']) - len(existing_products)
    logger.info(f"Scraped {scraped_now} new product(s); {len(state['products'])} total in database.")
    if state['failed']:
        logger.warning(f"{len(state['failed'])} URL(s) failed - see {FAILED_FILE} "
                       f"(re-run to retry them automatically)")
    else:
        logger.info("No failures.")


def run_scraper(limit=None, target_url=None, workers=8, resume=True, single_product=False):
    client = ScraperClient()
    parser = Parser()

    site = target_url or DEFAULT_SITE

    if single_product and target_url:
        logger.info(f"Single-product mode: {target_url}")
        product_urls = [target_url]
    else:
        logger.info(f"Crawling entire site: {site}")
        product_urls = discover_product_urls(client, parser, site)
        logger.info(f"Found {len(product_urls)} total product URLs.")

    if not product_urls:
        logger.error("No product URLs discovered - nothing to scrape. The site may not "
                     "expose a sitemap, or may be blocking automated requests.")
        update_progress(0, 0)
        return

    existing_products, done_urls = load_existing_products(resume)
    if done_urls:
        before = len(product_urls)
        product_urls = [u for u in product_urls if u not in done_urls]
        logger.info(f"Resume: {len(done_urls)} already scraped, "
                    f"{before - len(product_urls)} skipped, {len(product_urls)} remaining.")

    if limit:
        product_urls = product_urls[:limit]
        logger.info(f"Limiting scrape to {limit} products.")

    if not product_urls:
        logger.info("Everything already scraped - nothing to do.")
        update_progress(len(existing_products), len(existing_products))
        return

    logger.info(f"Starting concurrent scraping of {len(product_urls)} products with {workers} workers...")
    asyncio.run(run_concurrent_scraper(product_urls, workers, existing_products))
    logger.info("Scraping finished successfully!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='E-commerce product scraper')
    parser.add_argument('--limit', type=int, help='Limit the number of products to scrape', default=None)
    parser.add_argument('--target_url', type=str,
                        help='Any URL on the store to scrape; its whole catalogue is discovered via sitemaps',
                        default=None)
    parser.add_argument('--workers', type=int, help='Number of concurrent workers', default=8)
    parser.add_argument('--no-resume', action='store_true',
                        help='Re-scrape everything instead of skipping already-scraped products')
    parser.add_argument('--single-product', action='store_true',
                        help='Treat --target_url as one product page instead of crawling the site')
    args = parser.parse_args()

    run_scraper(limit=args.limit, target_url=args.target_url, workers=args.workers,
                resume=not args.no_resume, single_product=args.single_product)
