import asyncio
import os
import aiofiles
from curl_cffi.requests import AsyncSession
import logging

logger = logging.getLogger(__name__)

class ImageDownloader:
    def __init__(self, base_dir, concurrency=10):
        self.base_dir = base_dir
        self.semaphore = asyncio.Semaphore(concurrency)
        # Ensure base image directory exists
        os.makedirs(self.base_dir, exist_ok=True)

    async def download_image(self, session, url, save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
        file_name = url.split('/')[-1]
        file_path = os.path.join(save_dir, file_name)

        if os.path.exists(file_path):
            return file_path  # Already downloaded

        async with self.semaphore:
            try:
                response = await session.get(url, timeout=30)
                if response.status_code == 200:
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(response.content)
                    return file_path
                else:
                    logger.error(f"Failed to download image {url}: HTTP {response.status_code}")
                    return None
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
