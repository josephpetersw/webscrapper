import asyncio
import gzip
import hashlib
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
from scraper import client
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
PROGRESS_JSON = os.path.join(DATA_DIR, 'progress.json')
PROGRESS_FILE = PROGRESS_JSON  # long-standing name, kept for callers
ACTIVE_SITE_FILE = os.path.join(DATA_DIR, '.active_site')

# Storage for the run currently in progress. Every store gets its own folder,
# so these are pointed at that folder by configure_run_paths() before a scrape
# starts rather than being fixed at import time. They are module-level because
# the storage helpers below are the API the rest of the file (and the tests)
# use, and threading a paths dict through all of them buys nothing.
STRUCTURED_DIR = None
HTML_CACHE_DIR = None
PRODUCTS_JSONL = None
PRODUCTS_JSON = None
CATEGORIES_JSON = None
FAILED_JSON = None
IMAGES_DIR = None


def configure_run_paths(site_dir):
    """Point the storage helpers at one store's folder."""
    global STRUCTURED_DIR, HTML_CACHE_DIR, PRODUCTS_JSONL, PRODUCTS_JSON
    global CATEGORIES_JSON, FAILED_JSON, IMAGES_DIR
    STRUCTURED_DIR = os.path.join(site_dir, 'structured')
    HTML_CACHE_DIR = os.path.join(site_dir, 'cache', 'html')
    PRODUCTS_JSONL = os.path.join(site_dir, 'products.jsonl')
    PRODUCTS_JSON = os.path.join(site_dir, 'products.json')
    CATEGORIES_JSON = os.path.join(site_dir, 'categories.json')
    FAILED_JSON = os.path.join(site_dir, 'failed_urls.json')
    IMAGES_DIR = os.path.join(site_dir, 'images')
    return site_paths(site_dir)


# ── Atomic writes ────────────────────────────────────────────────────────

def _write_json_atomic(path, payload, indent=None):
    """Write JSON via a temp file + rename.

    These files are rewritten while the dashboard polls them. Writing in place
    lets a reader observe a half-written file and fail to parse it; renaming
    swaps the file in as a single step, so a reader always sees either the old
    copy or the new one.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = f'{path}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=indent, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# Kept: the original spelling is used across this file and by callers.
write_json_atomic = _write_json_atomic


# ── Progress ─────────────────────────────────────────────────────────────

class ProgressReporter:
    """Writes data/progress.json, but not on every single completion.

    The dashboard polls this a couple of times a second; rewriting it once per
    product on an eight-worker run is a few hundred pointless writes a minute
    and buys the reader nothing it would ever see. Throttling to an interval
    keeps the bar smooth and the disk quiet. The final update always passes
    force so a finished run never appears stuck one short.
    """

    def __init__(self, total, min_interval=1.0, path=None):
        self.total = total
        self.min_interval = min_interval
        self.path = path
        self.started = time.time()
        self._last_write = 0.0

    def update(self, current, force=False):
        now = time.time()
        if not force and (now - self._last_write) < self.min_interval:
            return
        self._last_write = now
        elapsed = now - self.started
        eta = ((self.total - current) * (elapsed / current)) if current > 0 else 0
        _write_json_atomic(self.path or PROGRESS_JSON,
                           {'current': current, 'total': self.total,
                            'eta': round(eta, 2) if eta else 0})


def update_progress(current, total, eta=0):
    """One-shot progress write, for the start and end of a run."""
    _write_json_atomic(PROGRESS_JSON,
                       {'current': current, 'total': total, 'eta': eta})


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


# ── Product records ──────────────────────────────────────────────────────
# Products are appended to products.jsonl one line at a time as they are
# scraped, instead of rewriting the whole of products.json every few
# completions. On a 3,000-product run that rewrite was quadratic — hundreds of
# rewrites of a file growing towards 50MB, several GB of cumulative writes for
# data nobody read. Appending is O(1) per product, and a run killed halfway
# leaves every completed product already on disk.

def _append_product_record(record):
    """Append one product as a JSON line."""
    os.makedirs(os.path.dirname(os.path.abspath(PRODUCTS_JSONL)) or '.', exist_ok=True)
    with open(PRODUCTS_JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def _read_all_products():
    """Every product from products.jsonl, latest record per URL winning.

    A re-scrape appends rather than rewriting, so the same URL can appear more
    than once; the last line for a URL is the current truth. Records with no
    URL cannot be de-duplicated and are all kept.

    The file is appended to live, so a killed run can leave a half-written
    final line. That line is skipped rather than treated as corruption of the
    whole file — losing one product is recoverable, losing the catalogue is not.
    """
    if not PRODUCTS_JSONL or not os.path.exists(PRODUCTS_JSONL):
        return []

    by_url, anonymous, order = {}, [], []
    try:
        with open(PRODUCTS_JSONL, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # torn final line, or a single bad write
                if not isinstance(record, dict):
                    continue
                url = record.get('url')
                if not url:
                    anonymous.append(record)
                    continue
                if url not in by_url:
                    order.append(url)
                by_url[url] = record
    except OSError as e:
        logger.error(f'Could not read {PRODUCTS_JSONL}: {e}')
        return []

    return [by_url[u] for u in order] + anonymous


def _load_scraped_urls():
    """URLs already scraped, so a re-run does only what is missing.

    Prefers products.jsonl. Falls back to a legacy products.json so a catalogue
    scraped by an older version still resumes instead of starting over.
    """
    if PRODUCTS_JSONL and os.path.exists(PRODUCTS_JSONL):
        return {p['url'] for p in _read_all_products() if p.get('url')}

    if PRODUCTS_JSON and os.path.exists(PRODUCTS_JSON):
        try:
            with open(PRODUCTS_JSON, 'r', encoding='utf-8') as f:
                legacy = json.load(f)
        except (ValueError, OSError) as e:
            logger.warning(f'Could not read {PRODUCTS_JSON} ({e}); starting fresh.')
            return set()
        if isinstance(legacy, list):
            return {p['url'] for p in legacy
                    if isinstance(p, dict) and p.get('url')}
    return set()


# ── HTML cache ───────────────────────────────────────────────────────────

def _html_cache_path(url):
    digest = hashlib.sha1((url or '').encode('utf-8', 'replace')).hexdigest()
    return os.path.join(HTML_CACHE_DIR, f'{digest}.html.gz')


def _write_cached_html(url, html):
    """Store a fetched page, gzipped, keyed by URL.

    Written through a temp file: a run killed mid-write would otherwise leave a
    truncated archive that every later run would try, and fail, to read.
    """
    if not html:
        return
    os.makedirs(HTML_CACHE_DIR, exist_ok=True)
    path = _html_cache_path(url)
    tmp = path + '.tmp'
    try:
        with gzip.open(tmp, 'wt', encoding='utf-8') as f:
            f.write(html)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug(f'Could not cache {url}: {e}')
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_cached_html(url):
    """The cached page, or None. A corrupt entry counts as a miss."""
    if not HTML_CACHE_DIR:
        return None
    path = _html_cache_path(url)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


# ── Per-product output ───────────────────────────────────────────────────

def _structured_dir_for(data, url):
    """Folder for one product, nested under its category trail."""
    return paths_util.build_product_dir(
        STRUCTURED_DIR, data.get('categories'), data.get('title'), url)


def write_product_files(data, out_dir):
    """Write data.json, the descriptions, and the images folder for a product.

    Idempotent: a re-scrape overwrites in place, so re-running never leaves a
    stale description beside a fresh record.
    """
    os.makedirs(os.path.join(out_dir, 'images'), exist_ok=True)

    long_desc = data.get('long_description')
    if long_desc:
        import markdownify
        with open(os.path.join(out_dir, 'description.md'), 'w', encoding='utf-8') as f:
            f.write(markdownify.markdownify(long_desc, heading_style='ATX'))

    short_desc = data.get('short_description')
    if short_desc:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(short_desc, 'lxml').get_text(strip=True, separator='\n')
        with open(os.path.join(out_dir, 'short_description.txt'), 'w', encoding='utf-8') as f:
            f.write(text)

    _write_json_atomic(os.path.join(out_dir, 'data.json'), data, indent=2)


def discover_product_urls(client, parser, site_url):
    """Find every product URL on a store (see scraper/discovery.py)."""
    return discovery.discover_products(client, parser, site_url)['urls']


def load_existing_products(resume, paths):
    """Return (products, scraped_urls) from a previous run so it can be continued."""
    if not resume:
        return [], set()
    # products.jsonl is the live record; products.json is the legacy snapshot.
    products = _read_all_products()
    if not products and os.path.exists(paths['products']):
        try:
            with open(paths['products'], 'r', encoding='utf-8') as f:
                products = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read existing {paths['products']} ({e}) - starting fresh.")
            return [], set()
    if not isinstance(products, list):
        return [], set()
    # Only records that actually parsed count as done; empty/junk rows get re-scraped.
    valid = [p for p in products if isinstance(p, dict) and p.get('url') and p.get('title')]
    return valid, {p['url'] for p in valid}


def save_results(state):
    """Write the snapshot files the dashboard and exports read.

    The per-product record is already durable — it was appended to
    products.jsonl the moment it was scraped — so this is a convenience
    snapshot, not the source of truth. It is therefore safe to write it on a
    time interval rather than every few products.
    """
    paths = state['paths']
    try:
        os.makedirs(paths['dir'], exist_ok=True)
        _write_json_atomic(paths['products'], state['products'], indent=2)
        _write_json_atomic(paths['categories'], sorted(state['categories']), indent=2)
        _write_json_atomic(paths['failed'], state['failed'], indent=2)
    except Exception as e:
        # Losing one periodic flush must never end the run - the next one retries.
        logger.error(f"Could not save results: {e}")


# How often the products.json snapshot is refreshed while a run is in flight.
SNAPSHOT_INTERVAL = 20.0


def mark_completed(state, url, note):
    state['completed'] += 1
    state['progress'].update(state['completed'])
    logger.info(f"{note} {state['completed']}/{state['total']}: {url}")

    now = time.time()
    if now - state.get('last_snapshot', 0) >= SNAPSHOT_INTERVAL:
        state['last_snapshot'] = now
        save_results(state)


# Path safety (segment sanitising and the whole-path budget) lives in
# scraper/paths.py so the scraper and the image downloader cannot disagree
# about how much room is left. Re-exported here for backwards compatibility.
safe_path_segment = paths_util.safe_path_segment


async def recover_missing_price(data, url, async_session, parser, state):
    """Re-fetch a priced-but-priceless product with a fulfilment context.

    Some storefronts render the whole product page — title, images, description
    — but omit the price until the request states how the goods would be
    delivered, reporting the item as out of stock in the meantime. A catalogue
    scraped without that looks entirely sold out.

    The parameter that unlocks it is discovered once per host and then reused
    (see client.CONTEXT_MEMO), so this costs one extra request per host in the
    normal case, not one per product. Anything that fails leaves the original
    record untouched: a product with no price is a perfectly ordinary outcome.
    """
    if data.get('price') or data.get('price_value') is not None:
        client.CONTEXT_MEMO.settle(client._host_of(url))
        return data

    host = client._host_of(url)
    for params in client.CONTEXT_MEMO.candidates(host):
        probe_url = client.with_params(url, params)
        try:
            html, _ = await state['client'].fetch_page_async(
                async_session, probe_url, retries=1)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
        if not html:
            continue
        retried = parser.parse_product(html, url)
        if retried and (retried.get('price') or retried.get('price_value') is not None):
            client.CONTEXT_MEMO.remember(host, params)
            # This product earned its price from whichever set worked, so the
            # alternates are still pulling their weight and should not be
            # retired for being tried.
            client.CONTEXT_MEMO.credit(host)
            # Keep the original URL: the parameter is how we asked, not where
            # the product lives, and it must not end up in the exported feed.
            retried['url'] = url
            return retried
    return data


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

        data = await recover_missing_price(data, url, async_session, parser, state)

        # Category trail + product folder, budgeted so there is still room for
        # the image filenames underneath it.
        structured_dir = _structured_dir_for(data, url)
        write_product_files(data, structured_dir)

        # Download images for this product immediately
        image_dir = os.path.join(structured_dir, 'images')
        image_tasks = [
            downloader.download_image(async_session, img_url, image_dir)
            for img_url in data.get('images', [])
        ]
        if image_tasks:
            await asyncio.gather(*image_tasks)

        # Durable the moment it is scraped, so a killed run keeps everything
        # completed so far without waiting for the next snapshot.
        _append_product_record(data)

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
        'progress': ProgressReporter(total=len(product_urls)),
        'last_snapshot': time.time(),
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

    # Always force a final write: a throttled reporter would otherwise leave a
    # finished run showing one short.
    state['progress'].update(state['completed'], force=True)
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
    # Points the storage helpers at this store's folder for the whole run.
    paths = configure_run_paths(site_dir)
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
