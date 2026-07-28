import asyncio
import os
import aiofiles
from curl_cffi.requests import AsyncSession
import logging

# Path safety lives in one module, shared with the scraper - the room left for
# an image filename depends on the directory the scraper picked for the product.
from .paths import MAX_FILENAME_LEN, image_path, safe_filename  # noqa: F401
# Retry policy is shared with page fetching so the two cannot disagree about
# which statuses are worth a second attempt.
from .client import DEFAULT_BACKOFF, PERMANENT_STATUSES

logger = logging.getLogger(__name__)

# Attempts *after* the first, for a status or error that may be transient.
MAX_RETRIES = 3

# Suffix for a download still in progress. See _write_atomic.
PART_SUFFIX = '.part'


def _discard(path):
    """Remove a partial file, ignoring the case where it was never created."""
    try:
        os.remove(path)
    except OSError:
        pass


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
            content = await self._fetch_bytes(session, url)
            if content is None:
                return None
            return await self._write_atomic(file_path, content)

    async def _fetch_bytes(self, session, url):
        """Image bytes, or None. Never raises, except on cancellation.

        A transient failure - a 503, a connection reset - costs a product one of
        its pictures if taken at face value, so anything not known to be
        permanent is retried. A 404 is not retried: re-asking cannot change the
        answer, and across a catalogue of thousands of images that would be
        thousands of pointless round trips.
        """
        if self.client is not None:
            # The shared client already retries and escalates fingerprints.
            content, reason = await self.client.fetch_bytes_async(session, url)
            if content is None:
                logger.error(f"Failed to download image {url}: {reason}")
            return content

        last = 'unknown error'
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await session.get(url, timeout=30)
                status = getattr(response, 'status_code', 200)
                if status == 200:
                    return response.content
                if status in PERMANENT_STATUSES:
                    logger.error(f"Failed to download image {url}: HTTP {status}")
                    return None
                last = f'HTTP {status}'
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last = f'{type(e).__name__}: {e}'
            if attempt < MAX_RETRIES:
                await asyncio.sleep(DEFAULT_BACKOFF * (attempt + 1))

        logger.error(f"Failed to download image {url} after "
                     f"{MAX_RETRIES + 1} attempts: {last}")
        return None

    async def _write_atomic(self, file_path, content):
        """Write to a .part file, then rename it into place.

        The rename is what makes the skip-if-exists check above trustworthy.
        Writing straight to the final name means a run interrupted mid-write -
        the dashboard's Stop button terminates the scraper, and Ctrl+C does the
        same - leaves a truncated file that every later run treats as already
        downloaded, so that image stays silently corrupt for good.
        """
        part_path = file_path + PART_SUFFIX
        try:
            async with aiofiles.open(part_path, 'wb') as f:
                await f.write(content)
            os.replace(part_path, file_path)
            return file_path
        except asyncio.CancelledError:
            _discard(part_path)
            raise
        except Exception as e:
            logger.error(f"Error writing {file_path}: {e}")
            _discard(part_path)
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
