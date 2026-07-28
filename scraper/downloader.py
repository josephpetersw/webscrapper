import asyncio
import os
import aiofiles
from curl_cffi.requests import AsyncSession
import logging

# Path safety lives in one module, shared with the scraper - the room left for
# an image filename depends on the directory the scraper picked for the product.
from .paths import MAX_FILENAME_LEN, image_path, safe_filename  # noqa: F401

logger = logging.getLogger(__name__)


class ImageDownloader:
    def __init__(self, base_dir, concurrency=10, client=None):
        self.base_dir = base_dir
        self.semaphore = asyncio.Semaphore(concurrency)
        # Images sit on the same host as the pages, so a host that rejects our
        # browser fingerprint rejects them too. Going through the shared client
        # means downloads use whichever fingerprint already worked for that
        # host; without it a store could scrape perfectly and yield no pictures.
        self.client = client
        # Ensure base image directory exists
        os.makedirs(self.base_dir, exist_ok=True)

    async def download_image(self, session, url, save_dir):
        os.makedirs(save_dir, exist_ok=True)

        # Shortened to whatever the path budget allows, so a long filename
        # under a deep category tree cannot push the total past the OS limit.
        file_path = image_path(save_dir, url)
        if not file_path:
            logger.warning(f"Skipping image {url}: no room left in the path budget "
                           f"under {save_dir}")
            return None

        if os.path.exists(file_path):
            return file_path  # Already downloaded

        async with self.semaphore:
            try:
                if self.client is not None:
                    content, reason = await self.client.fetch_bytes_async(session, url)
                    if content is None:
                        logger.error(f"Failed to download image {url}: {reason}")
                        return None
                else:
                    response = await session.get(url, timeout=30)
                    if response.status_code != 200:
                        logger.error(f"Failed to download image {url}: "
                                     f"HTTP {response.status_code}")
                        return None
                    content = response.content

                async with aiofiles.open(file_path, 'wb') as f:
                    await f.write(content)
                return file_path
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}")
                return None

    async def download_all(self, tasks_data):
        """
        tasks_data: list of dicts with 'url' and 'product_name'
        """
        async with AsyncSession(impersonate="chrome") as session:
            tasks = [
                self.download_image(session, item['url'], item['product_name'])
                for item in tasks_data
            ]
            results = await asyncio.gather(*tasks)
            return results
