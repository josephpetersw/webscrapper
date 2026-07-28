import asyncio
import logging
import random
import time

from curl_cffi import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Transient statuses worth retrying; 404s and other client errors are permanent.
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def _retry_delay(attempt, retry_after=None):
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return min(2 ** attempt + random.uniform(0, 1), 30.0)


class ScraperClient:
    def __init__(self):
        # We use impersonate="chrome" to bypass Cloudflare
        self.session = requests.Session(impersonate="chrome")

    def fetch_page(self, url):
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.get(url, timeout=30)
            except Exception as e:
                if attempt == MAX_RETRIES:
                    logger.error(f"Error fetching {url} after {attempt + 1} attempts: {e}")
                    return None
                delay = _retry_delay(attempt)
                logger.warning(f"Error fetching {url}: {e} — retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            if response.status_code == 200:
                return response.text
            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                delay = _retry_delay(attempt, response.headers.get('Retry-After'))
                logger.warning(f"HTTP {response.status_code} for {url} — retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            logger.error(f"Failed to fetch {url}, status code: {response.status_code}")
            return None

    def fetch_soup(self, url):
        html = self.fetch_page(url)
        if html:
            return BeautifulSoup(html, 'html.parser')
        return None

    @staticmethod
    async def fetch_page_async(async_session, url):
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await async_session.get(url, timeout=30)
            except Exception as e:
                if attempt == MAX_RETRIES:
                    logger.error(f"Error fetching {url} after {attempt + 1} attempts: {e}")
                    return None
                delay = _retry_delay(attempt)
                logger.warning(f"Error fetching {url}: {e} — retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
                continue
            if response.status_code == 200:
                return response.text
            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                delay = _retry_delay(attempt, response.headers.get('Retry-After'))
                logger.warning(f"HTTP {response.status_code} for {url} — retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
                continue
            logger.error(f"Failed to fetch {url}, status code: {response.status_code}")
            return None
