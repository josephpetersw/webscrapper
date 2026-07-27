import asyncio
from curl_cffi import requests
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ScraperClient:
    def __init__(self):
        # We use impersonate="chrome" to bypass Cloudflare
        self.session = requests.Session(impersonate="chrome")

    def fetch_page(self, url):
        try:
            response = self.session.get(url, timeout=30)
            if response.status_code == 200:
                return response.text
            else:
                logger.error(f"Failed to fetch {url}, status code: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def fetch_soup(self, url):
        html = self.fetch_page(url)
        if html:
            return BeautifulSoup(html, 'html.parser')
        return None

    async def fetch_page_async(self, async_session, url):
        try:
            response = await async_session.get(url, timeout=30)
            if response.status_code == 200:
                return response.text
            else:
                logger.error(f"Failed to fetch {url}, status code: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
