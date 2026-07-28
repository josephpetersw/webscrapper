import asyncio
import json
import logging
import os
import re
import time
import argparse
from datetime import datetime
from urllib.parse import urlparse
from curl_cffi.requests import AsyncSession
from scraper.client import ScraperClient
from scraper.parser import Parser
from scraper.downloader import ImageDownloader
from scraper import discovery
from scraper import paths as paths_util

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
# Live run state, shared by whichever site is being scraped, so the dashboard
# always has a single place to poll for progress.
PROGRESS_FILE = os.path.join(DATA_DIR, 'progress.json')
ACTIVE_SITE_FILE = os.path.join(DATA_DIR, '.active_site')


def update_progress(current, total, eta=0):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({'current': current, 'total': total, 'eta': eta}, f)


def site_folder_name(url):
    """Folder name for a store, derived from its domain.

    'https://www.example.co.ke/product/x' -> 'example.co.ke'. The leading www.
    is dropped so the same store entered either way maps to one folder.
    """
    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = (parsed.netloc or parsed.path).lower().split('/')[0].split('@')[-1]
    if host.startswith('www.'):
        host = host[4:]
    return re.sub(r'[^a-z0-9.\-]', '_', host) or 'unknown-site'


def resolve_site_dir(url, new_version=False):
    """Where this run should write. Each store gets its own folder under data/.

    With new_version, the existing folder is left untouched and a sibling
    '<site>_v2_<timestamp>' (v3, v4, ...) is created instead.
    """
    base = site_folder_name(url)
    root = os.path.join(DATA_DIR, base)
    if not new_version or not os.path.isdir(root):
        return root

    existing = os.listdir(DATA_DIR) if os.path.isdir(DATA_DIR) else []
    version = 2
    while any(d.startswith(f"{base}_v{version}_") for d in existing):
        version += 1
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return os.path.join(DATA_DIR, f"{base}_v{version}_{stamp}")


def site_paths(site_dir):
    return {
        'dir': site_dir,
        'products': os.path.join(site_dir, 'products.json'),
        'categories': os.path.join(site_dir, 'categories.json'),
        'failed': os.path.join(site_dir, 'failed_urls.json'),
        'structured': os.path.join(site_dir, 'structured'),
        'images': os.path.join(site_dir, 'images'),
    }


def set_active_site(folder_name):
    """Record which site the dashboard should display."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ACTIVE_SITE_FILE, 'w', encoding='utf-8') as f:
        f.write(folder_name)


def discover_product_urls(client, parser, site_url):
    """Find every product URL on a store (see scraper/discovery.py)."""
    return discovery.discover_products(client, parser, site_url)['urls']


def load_existing_products(resume, paths):
    """Return (products, scraped_urls) from a previous run so it can be continued."""
    if not resume or not os.path.exists(paths['products']):
        return [], set()
    try:
        with open(paths['products'], 'r', encoding='utf-8') as f:
            products = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read existing {paths['products']} ({e}) - starting fresh.")
        return [], set()
    # Only records that actually parsed count as done; empty/junk rows get re-scraped.
    valid = [p for p in products if p.get('url') and p.get('title')]
    return valid, {p['url'] for p in valid}


def write_json_atomic(path, payload):
    """Write JSON via a temp file + rename.

    These files are rewritten every few seconds while the dashboard polls them.
    Writing in place lets a reader observe a half-written file and fail to
    parse it; renaming swaps the file in as a single step so a reader always
    sees either the old copy or the new one.
    """
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_results(state):
    paths = state['paths']
    try:
        os.makedirs(paths['dir'], exist_ok=True)
        write_json_atomic(paths['products'], state['products'])
        write_json_atomic(paths['categories'], sorted(state['categories']))
        write_json_atomic(paths['failed'], state['failed'])
    except Exception as e:
        # Losing one periodic flush must never end the run - the next one retries.
        logger.error(f"Could not save results: {e}")


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


# Path safety (segment sanitising and the whole-path budget) lives in
# scraper/paths.py so the scraper and the image downloader cannot disagree
# about how much room is left. Re-exported here for backwards compatibility.
safe_path_segment = paths_util.safe_path_segment


async def scrape_product(url, async_session, parser, downloader, semaphore, state):
    """Scrape one product. Never raises - a single bad page must not end the run."""
    try:
        return await _scrape_product_inner(url, async_session, parser, downloader, semaphore, state)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        state['failed'].append({'url': url, 'reason': f'{type(e).__name__}: {e}'})
        logger.error(f"Unexpected error on {url}: {type(e).__name__}: {e}")
        try:
            mark_completed(state, url, 'Errored')
        except Exception:
            pass
        return None


async def _scrape_product_inner(url, async_session, parser, downloader, semaphore, state):
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

        # Category trail + product folder, budgeted so there is still room for
        # the image filenames underneath it.
        structured_dir = paths_util.build_product_dir(
            state['paths']['structured'], data.get('categories'), data.get('title'), url)
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


async def run_concurrent_scraper(product_urls, workers, paths, existing_products=None):
    parser = Parser()
    client = ScraperClient()
    # Concurrency limit for image downloads. The downloader shares the client so
    # images are fetched with whichever browser fingerprint works for the host -
    # otherwise a store scrapes fine and comes back with none of its pictures.
    downloader = ImageDownloader(base_dir=paths['images'], concurrency=workers,
                                 client=client)
    # Concurrency limit for product page fetching
    semaphore = asyncio.Semaphore(workers)

    existing_products = existing_products or []
    state = {
        'completed': 0,
        'total': len(product_urls),
        'products': list(existing_products),
        'categories': set(),
        'failed': [],
        'client': client,
        'paths': paths,
        'start_time': time.time(),
    }
    for product in existing_products:
        for cat in product.get('categories') or []:
            state['categories'].add(cat)

    update_progress(0, state['total'], 0)

    try:
        async with AsyncSession(impersonate="chrome") as async_session:
            tasks = [
                scrape_product(url, async_session, parser, downloader, semaphore, state)
                for url in product_urls
            ]
            # return_exceptions so one unexpected failure can never abandon the
            # remaining products - the run always reaches the end or is stopped.
            await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.warning("Scrape cancelled - saving what was collected so far.")
    except Exception as e:
        logger.error(f"Scrape loop aborted ({type(e).__name__}: {e}) - saving partial results.")
    finally:
        # The client opens its own sessions when a host needs a non-default
        # browser fingerprint; those are not covered by the block above.
        try:
            await state['client'].aclose()
        except Exception:
            pass

    save_results(state)

    scraped_now = len(state['products']) - len(existing_products)
    logger.info(f"Scraped {scraped_now} new product(s); {len(state['products'])} total in database.")
    if state['failed']:
        logger.warning(f"{len(state['failed'])} URL(s) failed - see {paths['failed']} "
                       f"(re-run to retry them automatically)")
    else:
        logger.info("No failures.")


def run_scraper(limit=None, target_url=None, workers=8, resume=True,
                single_product=False, new_version=False):
    if not target_url:
        logger.error("No target URL provided. Pass --target_url with any URL on the "
                     "store you want to scrape.")
        update_progress(0, 0)
        return

    client = ScraperClient()
    parser = Parser()

    site_dir = resolve_site_dir(target_url, new_version)
    paths = site_paths(site_dir)
    os.makedirs(site_dir, exist_ok=True)
    set_active_site(os.path.basename(site_dir))
    logger.info(f"Output directory: {site_dir}")

    if single_product:
        logger.info(f"Single-product mode: {target_url}")
        product_urls = [target_url]
    else:
        logger.info(f"Crawling entire site: {target_url}")
        product_urls = discover_product_urls(client, parser, target_url)
        logger.info(f"Found {len(product_urls)} total product URLs.")

    if not product_urls:
        logger.error("No product URLs discovered - nothing to scrape. The site may not "
                     "expose a sitemap, or may be blocking automated requests.")
        update_progress(0, 0)
        return

    existing_products, done_urls = load_existing_products(resume, paths)
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
    asyncio.run(run_concurrent_scraper(product_urls, workers, paths, existing_products))
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
    parser.add_argument('--new-version', action='store_true',
                        help='Scrape into a new timestamped folder instead of updating the existing one')
    args = parser.parse_args()

    run_scraper(limit=args.limit, target_url=args.target_url, workers=args.workers,
                resume=not args.no_resume, single_product=args.single_product,
                new_version=args.new_version)
