"""A real browser, as the last rung of the fetch ladder.

Some hosts sit behind an interactive challenge that no TLS fingerprint can
satisfy: the page is a JavaScript puzzle, and only something that actually runs
the JavaScript gets the content. ``client.py`` tries this after every curl_cffi
profile has been refused, never before — it is the slowest and heaviest thing
we can do, by a wide margin.

**The dependency is optional.** ``undetected-chromedriver`` is not in
requirements.txt, because it drags in Selenium and a real Chrome and most runs
never need it. Without it ``HAS_BROWSER`` is False, the rung is simply absent
from the ladder, and everything else works unchanged:

    pip install undetected-chromedriver

Historically this module was named ``playwright_client`` and its flag
``HAS_PLAYWRIGHT``, neither of which was true — it has always driven Chrome via
undetected-chromedriver. The old names are re-exported at the bottom so nothing
that imported them breaks.
"""

import asyncio
import logging
import threading
import time

try:
    import undetected_chromedriver as uc
    HAS_BROWSER = True
except ImportError:                                     # pragma: no cover
    uc = None
    HAS_BROWSER = False

logger = logging.getLogger(__name__)

# How long to keep waiting for a challenge page to turn into real content.
# The previous 60s was written for a human solving a CAPTCHA by hand; on an
# unattended run it simply stalled every worker for a minute on a host that was
# never going to let us in. A challenge that resolves at all resolves quickly.
CHALLENGE_TIMEOUT = 15
CHALLENGE_POLL = 0.5

_CHALLENGE_TITLES = ('just a moment', 'checking your browser',
                     'attention required', 'please wait')

# One browser for the process, not one per worker thread. A thread-local driver
# meant eight workers span eight Chromes — several GB of memory and eight
# challenge solves for the same host. Serialising through one is slower per
# call but this is already the slow path, and it is the difference between a
# fallback and a resource leak.
_driver = None
_driver_lock = threading.Lock()


def _get_driver():
    global _driver
    if _driver is None:
        options = uc.ChromeOptions()
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        # No version_main pin: undetected-chromedriver matches the installed
        # Chrome by itself, and a hardcoded major version breaks on every
        # machine that has any other one.
        _driver = uc.Chrome(options=options)
    return _driver


def close_driver():
    """Shut the browser down. Safe to call when one was never started."""
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None


class BrowserResponse:
    """Enough of a requests response for client.py's classifier."""

    def __init__(self, content, status=200):
        if isinstance(content, bytes):
            self.content = content
            self.text = content.decode('utf-8', errors='ignore')
        else:
            self.text = content or ''
            self.content = self.text.encode('utf-8')
        self.status_code = status
        self.headers = {'content-type': 'text/html; charset=utf-8'}


def fetch_browser_sync(url, timeout=30):
    """Load a URL in a real browser and return its rendered HTML."""
    if not HAS_BROWSER:
        raise RuntimeError('undetected-chromedriver is not installed')

    with _driver_lock:
        driver = _get_driver()
        try:
            driver.set_page_load_timeout(timeout)
        except Exception:
            pass
        try:
            driver.get(url)
        except Exception as e:
            # A page-load timeout does not mean an empty page: the challenge
            # frame often keeps a connection open long after the content is
            # there. Carry on and read what rendered.
            logger.debug(f'Browser load for {url} reported {type(e).__name__}; reading anyway')

        deadline = time.time() + CHALLENGE_TIMEOUT
        while time.time() < deadline:
            try:
                title = (driver.title or '').lower()
            except Exception:
                break
            if not any(marker in title for marker in _CHALLENGE_TITLES):
                break
            time.sleep(CHALLENGE_POLL)
        else:
            logger.info(f'Challenge on {url} did not clear in {CHALLENGE_TIMEOUT}s')

        try:
            return BrowserResponse(driver.page_source)
        except Exception as e:
            raise RuntimeError(f'browser returned no page source: {e}')


async def fetch_browser_async(url, timeout=30):
    return await asyncio.to_thread(fetch_browser_sync, url, timeout)


# ── Backwards-compatible names ───────────────────────────────────────────
HAS_PLAYWRIGHT = HAS_BROWSER
fetch_playwright_sync = fetch_browser_sync
fetch_playwright_async = fetch_browser_async
DummyResponse = BrowserResponse
