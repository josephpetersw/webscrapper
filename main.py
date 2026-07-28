import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler

import markdownify
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from scraper.client import ScraperClient
from scraper.downloader import ImageDownloader
from scraper.parser import Parser

try:
    import uvloop
    _run_async = uvloop.run
except ImportError:
    _run_async = asyncio.run

DATA_DIR = 'data'
STRUCTURED_DIR = os.path.join(DATA_DIR, 'structured')
HTML_CACHE_DIR = os.path.join(DATA_DIR, 'cache', 'html')
PRODUCTS_JSONL = os.path.join(DATA_DIR, 'products.jsonl')
PRODUCTS_JSON = os.path.join(DATA_DIR, 'products.json')
CATEGORIES_JSON = os.path.join(DATA_DIR, 'categories.json')
PROGRESS_JSON = os.path.join(DATA_DIR, 'progress.json')

# Root logger config (force=True overrides scraper.client's basicConfig) so
# client/downloader retry warnings also reach scraper.log for the dashboard.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('scraper.log', maxBytes=5 * 1024 * 1024, backupCount=3),
    ],
    force=True,
)
logger = logging.getLogger(__name__)

_jsonl_lock = threading.Lock()


def _write_json_atomic(path, payload, indent=None):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


class ProgressReporter:
    """Throttled, atomic writer for data/progress.json (polled by the dashboard)."""

    def __init__(self, total, min_interval=0.5):
        self.total = total
        self.min_interval = min_interval
        self.start = time.monotonic()
        self._last_write = 0.0

    def update(self, current, force=False):
        now = time.monotonic()
        if not force and now - self._last_write < self.min_interval:
            return
        self._last_write = now
        elapsed = now - self.start
        eta = (self.total - current) * (elapsed / current) if current else 0
        _write_json_atomic(PROGRESS_JSON, {'current': current, 'total': self.total, 'eta': eta})


# ── HTML cache ───────────────────────────────────────────────
def _html_cache_path(url):
    return os.path.join(HTML_CACHE_DIR, hashlib.sha1(url.encode('utf-8')).hexdigest() + '.html.gz')


def _read_cached_html(url):
    try:
        with gzip.open(_html_cache_path(url), 'rt', encoding='utf-8') as f:
            return f.read()
    except (OSError, EOFError):
        return None


def _write_cached_html(url, html):
    os.makedirs(HTML_CACHE_DIR, exist_ok=True)
    path = _html_cache_path(url)
    tmp = path + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as f:
        f.write(html)
    os.replace(tmp, path)


# ── Structured storage ───────────────────────────────────────
def _safe_component(text):
    safe = re.sub(r'[^a-zA-Z0-9]', '_', text)
    return re.sub(r'_+', '_', safe).strip('_')


def _structured_dir_for(data, url):
    cats = data.get('categories', [])
    cat_path = "/".join(filter(None, (_safe_component(c) for c in cats))) or "Uncategorized"
    safe_name = _safe_component(data.get('title') or url.rstrip('/').split('/')[-1])
    return os.path.join(STRUCTURED_DIR, cat_path, safe_name)


def write_product_files(data, structured_dir):
    """Blocking CPU + disk work — always called via asyncio.to_thread."""
    os.makedirs(os.path.join(structured_dir, 'images'), exist_ok=True)

    if data.get('long_description'):
        md = markdownify.markdownify(data['long_description'], heading_style="ATX")
        with open(os.path.join(structured_dir, 'description.md'), 'w', encoding='utf-8') as f:
            f.write(md)
    if data.get('short_description'):
        txt = BeautifulSoup(data['short_description'], 'lxml').get_text(strip=True, separator='\n')
        with open(os.path.join(structured_dir, 'short_description.txt'), 'w', encoding='utf-8') as f:
            f.write(txt)

    _write_json_atomic(os.path.join(structured_dir, 'data.json'), data, indent=2)


def _append_product_record(data):
    with _jsonl_lock, open(PRODUCTS_JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def _read_all_products():
    """Read products.jsonl, deduping by URL (last record wins)."""
    products = {}
    if os.path.exists(PRODUCTS_JSONL):
        with open(PRODUCTS_JSONL, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn final line from a killed run
                products[rec.get('url') or f'#{i}'] = rec
    return list(products.values())


def _load_scraped_urls():
    urls = {rec['url'] for rec in _read_all_products() if rec.get('url')}
    if not urls and os.path.exists(PRODUCTS_JSON):
        try:
            with open(PRODUCTS_JSON, 'r', encoding='utf-8') as f:
                urls = {rec['url'] for rec in json.load(f) if rec.get('url')}
        except (OSError, json.JSONDecodeError):
            pass
    return urls


# ── Scraping ─────────────────────────────────────────────────
async def scrape_product(url, session, downloader, fetch_sem, state, progress, use_cache=True):
    try:
        html = await asyncio.to_thread(_read_cached_html, url) if use_cache else None
        if html is None:
            # The semaphore covers only the network fetch: image downloads and
            # parsing must not hold a page-worker slot hostage.
            async with fetch_sem:
                html = await ScraperClient.fetch_page_async(session, url)
            if html:
                await asyncio.to_thread(_write_cached_html, url, html)

        data = None
        if html:
            data = await asyncio.to_thread(Parser.parse_product, html, url)

        if data:
            structured_dir = _structured_dir_for(data, url)
            data['image_dir'] = os.path.relpath(os.path.join(structured_dir, 'images'), DATA_DIR)
            await asyncio.to_thread(write_product_files, data, structured_dir)

            image_tasks = [
                downloader.download_image(session, img_url, os.path.join(structured_dir, 'images'))
                for img_url in data.get('images', [])
            ]
            if image_tasks:
                await asyncio.gather(*image_tasks)

            await asyncio.to_thread(_append_product_record, data)
            state['categories'].update(data.get('categories', []))
        else:
            state['failed'].append(url)
    except Exception as e:
        state['failed'].append(url)
        logger.error(f"Unexpected error scraping {url}: {e}")
    finally:
        state['completed'] += 1
        progress.update(state['completed'])
        if state['completed'] % 25 == 0:
            _write_json_atomic(CATEGORIES_JSON, sorted(state['categories']))
        logger.info(f"Scraped product {state['completed']}/{state['total']}: {url}")


async def run_concurrent_scraper(product_urls, workers, use_cache=True):
    downloader = ImageDownloader(base_dir=os.path.join(DATA_DIR, 'images'), concurrency=workers)
    fetch_sem = asyncio.Semaphore(workers)

    state = {'completed': 0, 'total': len(product_urls), 'categories': set(), 'failed': []}
    if os.path.exists(CATEGORIES_JSON):
        try:
            with open(CATEGORIES_JSON, 'r', encoding='utf-8') as f:
                state['categories'].update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    progress = ProgressReporter(len(product_urls))
    progress.update(0, force=True)

    # max_clients bounds actual curl handles; pages and images share the pool
    async with AsyncSession(impersonate="chrome", max_clients=max(workers * 2, 10)) as session:
        tasks = [
            scrape_product(url, session, downloader, fetch_sem, state, progress, use_cache)
            for url in product_urls
        ]
        await asyncio.gather(*tasks)

    progress.update(state['completed'], force=True)
    _write_json_atomic(CATEGORIES_JSON, sorted(state['categories']))
    # Legacy products.json kept for compatibility (raw export, check_fields.py)
    _write_json_atomic(PRODUCTS_JSON, _read_all_products(), indent=2)

    if state['failed']:
        preview = ', '.join(state['failed'][:5])
        logger.warning(f"{len(state['failed'])} URLs failed permanently. First few: {preview}")


def run_scraper(limit=None, target_url=None, workers=20, force=False):
    os.makedirs(DATA_DIR, exist_ok=True)
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

        product_urls = list(dict.fromkeys(product_urls))
        logger.info(f"Found {len(product_urls)} total product URLs.")

        if not force:
            done = _load_scraped_urls()
            if done:
                before = len(product_urls)
                product_urls = [u for u in product_urls if u not in done]
                logger.info(f"Resuming: skipping {before - len(product_urls)} already-scraped products.")

    if limit:
        product_urls = product_urls[:limit]
        logger.info(f"Limiting scrape to {limit} products.")

    # An explicit target URL or --force means "refresh": bypass the HTML cache
    use_cache = not force and not target_url

    logger.info(f"Starting concurrent scraping of {len(product_urls)} products with {workers} workers...")
    _run_async(run_concurrent_scraper(product_urls, workers, use_cache))
    logger.info("Scraping finished successfully!")


if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(description='PhonePlaceKenya Scraper')
    arg_parser.add_argument('--limit', type=int, help='Limit the number of products to scrape', default=None)
    arg_parser.add_argument('--target_url', type=str, help='Specific URL to scrape', default=None)
    arg_parser.add_argument('--workers', type=int, help='Number of concurrent workers', default=20)
    arg_parser.add_argument('--force', action='store_true', help='Re-scrape everything, ignoring previous results and cached HTML')
    args = arg_parser.parse_args()

    run_scraper(limit=args.limit, target_url=args.target_url, workers=args.workers, force=args.force)
