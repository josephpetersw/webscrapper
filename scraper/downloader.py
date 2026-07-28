import asyncio
import logging
import os

import aiofiles

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


class ImageDownloader:
    def __init__(self, base_dir, concurrency=10):
        self.base_dir = base_dir
        self.semaphore = asyncio.Semaphore(concurrency)
        os.makedirs(self.base_dir, exist_ok=True)

    async def download_image(self, session, url, save_dir):
        """save_dir must already exist (the scraper creates it per product)."""
        file_name = url.split('/')[-1]
        file_path = os.path.join(save_dir, file_name)

        if os.path.exists(file_path):
            return file_path  # Already downloaded

        async with self.semaphore:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    response = await session.get(url, timeout=30)
                except Exception as e:
                    if attempt == MAX_RETRIES:
                        logger.error(f"Error downloading {url}: {e}")
                        return None
                    await asyncio.sleep(1 + attempt)
                    continue
                if response.status_code == 200:
                    # .part + rename so an interrupted run never leaves a
                    # truncated file that the exists-check above would trust
                    tmp_path = file_path + '.part'
                    async with aiofiles.open(tmp_path, 'wb') as f:
                        await f.write(response.content)
                    os.replace(tmp_path, file_path)
                    return file_path
                if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                    await asyncio.sleep(1 + attempt)
                    continue
                logger.error(f"Failed to download image {url}: HTTP {response.status_code}")
                return None
