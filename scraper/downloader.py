import asyncio
import os
import re
import aiofiles
from urllib.parse import unquote, urlparse
from curl_cffi.requests import AsyncSession
import logging

logger = logging.getLogger(__name__)

# Characters Windows refuses in a filename, plus control codes.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NON_PRINTABLE = re.compile(r'[^\x20-\x7e]')
MAX_FILENAME_LEN = 100


def safe_filename(url, fallback='image'):
    """Turn an image URL into a filename the filesystem will actually accept.

    Source URLs routinely contain percent-encoding, non-ASCII characters and
    query strings, any of which make open() fail on Windows - which silently
    cost us the image.
    """
    raw = unquote(urlparse(url).path.split('/')[-1])
    name = _NON_PRINTABLE.sub('_', _ILLEGAL_FILENAME_CHARS.sub('_', raw)).strip('. ')
    if not name:
        return fallback
    if len(name) > MAX_FILENAME_LEN:
        stem, dot, ext = name.rpartition('.')
        if dot and len(ext) <= 5:
            name = stem[:MAX_FILENAME_LEN - len(ext) - 1] + '.' + ext
        else:
            name = name[:MAX_FILENAME_LEN]
    return name


class ImageDownloader:
    def __init__(self, base_dir, concurrency=10):
        self.base_dir = base_dir
        self.semaphore = asyncio.Semaphore(concurrency)
        # Ensure base image directory exists
        os.makedirs(self.base_dir, exist_ok=True)

    async def download_image(self, session, url, save_dir):
        os.makedirs(save_dir, exist_ok=True)

        file_name = safe_filename(url)
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
