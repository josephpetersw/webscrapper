import asyncio
import logging
import time

try:
    import undetected_chromedriver as uc
    HAS_PLAYWRIGHT = True  # kept for compatibility with client.py
except ImportError:
    HAS_PLAYWRIGHT = False

logger = logging.getLogger(__name__)

import threading
_driver_local = threading.local()

def get_driver():
    if not hasattr(_driver_local, 'driver'):
        options = uc.ChromeOptions()
        _driver_local.driver = uc.Chrome(options=options, version_main=150)
    return _driver_local.driver

class DummyResponse:
    def __init__(self, content, status):
        self.text = content
        if isinstance(content, str):
            self.content = content.encode('utf-8')
        else:
            self.content = content
            self.text = content.decode('utf-8', errors='ignore')
        self.status_code = status
        self.headers = {'content-type': 'text/html; charset=utf-8'}

def fetch_playwright_sync(url, timeout=30):
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("undetected-chromedriver not installed")
        
    driver = get_driver()
    
    try:
        driver.set_page_load_timeout(timeout)
        try:
            driver.get(url)
        except Exception:
            pass # Timeout might occur, but page might still have loaded
            
        logger.info("Waiting for manual CAPTCHA solving or automatic bypass...")
        for _ in range(60):
            title = driver.title
            if "Just a moment" not in title and "Checking your browser" not in title:
                break
            time.sleep(1)
            
        content = driver.page_source
        return DummyResponse(content, 200)
    except Exception as e:
        logger.error(f"Driver error: {e}")
        raise

async def fetch_playwright_async(url, timeout=30):
    return await asyncio.to_thread(fetch_playwright_sync, url, timeout)
