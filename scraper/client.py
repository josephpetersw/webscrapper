import asyncio
import time
from curl_cffi import requests
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Status codes where retrying cannot help — the resource is simply not there.
PERMANENT_STATUSES = (400, 401, 403, 404, 410, 451)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0

class ScraperClient:
    def __init__(self):
        # We use impersonate="chrome" to bypass Cloudflare
        self.session = requests.Session(impersonate="chrome")

    def fetch_page(self, url, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF):
        last_reason = 'unknown error'
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 200:
                    return response.text
                last_reason = f"HTTP {response.status_code}"
                if response.status_code in PERMANENT_STATUSES:
                    logger.error(f"Failed to fetch {url}: {last_reason} (not retrying)")
                    return None
            except Exception as e:
                last_reason = str(e)
            if attempt < retries:
                delay = backoff * attempt
                logger.warning(f"Attempt {attempt}/{retries} failed for {url} ({last_reason}) — retrying in {delay:.0f}s")
                time.sleep(delay)
        logger.error(f"Giving up on {url} after {retries} attempts: {last_reason}")
        return None

    def fetch_response(self, url, retries=1):
        """Like fetch_page but hands back the whole response.

        Some APIs report their total item count in a header (WooCommerce's
        Store API sends X-WP-Total), which lets us size a catalogue in one
        request instead of paging through it.
        """
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(url, timeout=20)
                return response
            except Exception as e:
                if attempt >= retries:
                    logger.debug(f"fetch_response failed for {url}: {e}")
                    return None
                time.sleep(DEFAULT_BACKOFF * attempt)
        return None

    def fetch_soup(self, url):
        html = self.fetch_page(url)
        if html:
            return BeautifulSoup(html, 'html.parser')
        return None

    async def fetch_page_async(self, async_session, url, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF):
        """Fetch a page, retrying transient failures. Returns (html, reason).

        html is None when every attempt failed; reason then explains why, so the
        caller can record which URLs were missed instead of silently skipping them.
        """
        last_reason = 'unknown error'
        for attempt in range(1, retries + 1):
            try:
                response = await async_session.get(url, timeout=30)
                if response.status_code == 200:
                    return response.text, None
                last_reason = f"HTTP {response.status_code}"
                if response.status_code in PERMANENT_STATUSES:
                    logger.error(f"Failed to fetch {url}: {last_reason} (not retrying)")
                    return None, last_reason
            except Exception as e:
                last_reason = str(e)
            if attempt < retries:
                delay = backoff * attempt
                logger.warning(f"Attempt {attempt}/{retries} failed for {url} ({last_reason}) — retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
        logger.error(f"Giving up on {url} after {retries} attempts: {last_reason}")
        return None, last_reason
