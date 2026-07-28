"""HTTP fetching, with automatic browser-fingerprint escalation.

``curl_cffi`` impersonates a real browser's TLS/HTTP2 fingerprint, which is what
gets us past most bot protection. But a *single* hardcoded profile is not
enough: several hosts (anything behind the ``hcdn`` edge, for one) reject the
Chrome fingerprint with a 403 "Checking your browser" interstitial while
serving the identical request happily under Safari. That is a fingerprint
mismatch, not a permanent refusal — treating it as one silently loses whole
stores.

So every fetch walks a short ladder of profiles, and the first profile that
works for a host is remembered and reused for every later request to it. The
cost of discovery is paid once per host, not once per URL.

Nothing here raises. ``fetch_page`` returns ``None`` and ``fetch_page_async``
returns ``(None, reason)`` so callers can record *why* a URL was missed.
"""

import asyncio
import logging
import random
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests

from .playwright_client import fetch_playwright_async, fetch_playwright_sync, HAS_PLAYWRIGHT

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Fingerprints to try, in order. Chrome first because it works on the large
# majority of stores; Safari rescues the hosts that specifically block Chrome's
# JA3. Keep this list short — every entry is a potential extra round trip on a
# genuinely dead URL.
if HAS_PLAYWRIGHT:
    IMPERSONATE_PROFILES = ('chrome', 'safari', 'firefox', 'playwright')
else:
    IMPERSONATE_PROFILES = ('chrome', 'safari', 'firefox')
DEFAULT_PROFILE = IMPERSONATE_PROFILES[0]

# Statuses where retrying — with any fingerprint — cannot help.
# 403 is deliberately NOT here: it is the signature of a fingerprint block far
# more often than of a genuinely forbidden resource, so it escalates instead.
PERMANENT_STATUSES = (400, 401, 404, 410, 451)

# Statuses that mean "try a different browser fingerprint".
FINGERPRINT_STATUSES = (403, 406, 503)

# Statuses that mean "you are going too fast" — wait, then retry as we were.
THROTTLE_STATUSES = (429,)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0
DEFAULT_TIMEOUT = 30

# Query parameters that make a storefront commit to a fulfilment context.
#
# Some stores serve a product page whose price is simply absent until the
# request says how the goods would be delivered — the page renders, the title
# and images are all there, and schema.org reports the item as out of stock with
# no offer price. Left alone that yields a catalogue where every product looks
# unavailable, which is indistinguishable from a genuinely sold-out store.
#
# Each entry is tried once per host, only after a product has come back priced
# but incomplete, and the one that works is remembered for the rest of the run.
# These are ordinary public parameters the storefront's own links carry.
PRICE_CONTEXT_PARAMS = (
    {'sid': 'SLOTTED'},     # scheduled-delivery slot
    {'sid': 'EXPRESS'},     # immediate delivery
    {'fulfillment': 'delivery'},
    {'deliveryMode': 'delivery'},
)

# Give up probing a host after this many products fail to gain a price, so a
# store whose stock really is exhausted does not double its request count.
_CONTEXT_PROBE_LIMIT = 4


BACKOFF_CAP = 30.0
RETRY_AFTER_CAP = 60.0


def retry_delay(attempt, retry_after=None):
    """Seconds to wait before retry number ``attempt`` (0-based).

    A server that sends ``Retry-After`` has told us exactly how long to wait, so
    that wins over any schedule of ours. Otherwise the delay doubles per attempt
    with a random fraction added: without that jitter, eight workers that hit
    the same failing host all back off in lockstep and retry in one burst,
    which looks far more like an attack than the traffic it replaces.
    """
    if retry_after:
        try:
            return min(float(retry_after), RETRY_AFTER_CAP)
        except (TypeError, ValueError):
            # The HTTP-date form of Retry-After. Rare, and not worth parsing —
            # fall through to our own backoff rather than guessing.
            pass
    delay = 2.0 ** min(attempt, 10)
    return BACKOFF_CAP if delay >= BACKOFF_CAP else delay + random.random()

# After this many URLs on a host have failed the whole ladder, stop walking it.
# Escalation is worth paying for once per host; paying for it on every URL of a
# host that is simply blocking us turns a 3,000-product run into 27,000 requests.
_EXHAUSTED_AFTER = 3

# Bot walls that answer 200 with an interstitial instead of the page. Checked
# against the first few KB only — these pages are always tiny and front-loaded.
_CHALLENGE_MARKERS = (
    'just a moment...',
    'checking your browser',
    'enable javascript and cookies to continue',
    'cf-browser-verification',
    'challenge-platform',
    '_incapsula_resource',
    'ddos-guard',
)
_CHALLENGE_SNIFF_BYTES = 4000
# Below this, a "successful" HTML response is almost certainly an error or
# interstitial rather than a real page.
_MIN_CREDIBLE_HTML = 200


def looks_like_challenge(text, headers=None):
    """True if this response is a bot-wall interstitial rather than content."""
    if headers:
        try:
            if str(headers.get('cf-mitigated', '')).lower() == 'challenge':
                return True
        except Exception:
            pass
    if not text:
        return False
    head = text[:_CHALLENGE_SNIFF_BYTES].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def normalize_url(url):
    """Percent-encode a URL so curl accepts it.

    Product URLs regularly contain non-ASCII characters (registered-trademark
    signs in Shopify handles, for instance) and raw spaces. Left alone these
    either raise or fetch the wrong resource.
    """
    if not url:
        return url
    try:
        parts = urlparse(url.strip())
        return urlunparse((
            parts.scheme,
            parts.netloc.encode('idna').decode('ascii') if _non_ascii(parts.netloc) else parts.netloc,
            quote(parts.path, safe="/%:@&=+$,~!*'()"),
            quote(parts.params, safe="/%:@&=+$,~"),
            quote(parts.query, safe="/%:@&=+$,~?"),
            parts.fragment,
        ))
    except Exception:
        return url


def _non_ascii(value):
    return any(ord(c) > 127 for c in value or '')


def _host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ''


class _ProfileMemo:
    """Remembers which fingerprint works for each host, and which are hopeless.

    Shared across every ScraperClient in the process so the discovery pass and
    the scrape pass don't each pay for the same escalation.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._good = {}
        self._failures = {}
        self._announced = set()

    def get(self, host):
        with self._lock:
            return self._good.get(host)

    def set(self, host, profile):
        if not host:
            return
        with self._lock:
            self._failures.pop(host, None)
            if self._good.get(host) != profile:
                self._good[host] = profile
                if profile != DEFAULT_PROFILE:
                    logger.info(f"Using '{profile}' browser fingerprint for {host}")

    def record_failure(self, host, blocked=False):
        """Note that every profile failed for a URL on this host.

        Only a *fingerprint* rejection counts towards giving up on escalation.
        A timeout or a flaky 500 says nothing about which browser we look like,
        and must not train us out of trying alternates.
        """
        if not host or not blocked:
            return
        with self._lock:
            count = self._failures.get(host, 0) + 1
            self._failures[host] = count
            if count == _EXHAUSTED_AFTER and host not in self._announced:
                self._announced.add(host)
                logger.warning(
                    f"{host} rejected every browser fingerprint on {count} URLs - "
                    f"no longer retrying alternates for it. The host is most likely behind "
                    f"an interactive bot challenge.")

    def is_exhausted(self, host):
        with self._lock:
            return self._failures.get(host, 0) >= _EXHAUSTED_AFTER

    def ladder(self, host):
        """Profiles to try for this host, best-known first."""
        known = self.get(host)
        if known:
            return [known] + [p for p in IMPERSONATE_PROFILES if p != known]
        if self.is_exhausted(host):
            # Still try — the block may lift — but at the cost of one profile,
            # not the whole ladder.
            return [DEFAULT_PROFILE]
        return list(IMPERSONATE_PROFILES)


PROFILE_MEMO = _ProfileMemo()


class _ContextMemo:
    """Remembers which fulfilment parameters a host needs before it will price.

    Same shape as the fingerprint memo, and for the same reason: the answer is a
    property of the host, so it is worth discovering once and then applying to
    every remaining URL rather than re-deriving it per product.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._known = {}      # host -> params that produced a price
        self._probes = {}     # host -> how many products we have probed
        self._settled = set()  # hosts needing nothing, or proven hopeless
        self._fallback_misses = {}  # host -> products the alternates also failed

    def known(self, host):
        with self._lock:
            return self._known.get(host)

    def remember(self, host, params):
        if not host:
            return
        with self._lock:
            # First writer wins. Workers run concurrently, so several products
            # can be probing at once and finish out of order; letting the last
            # one overwrite makes the recorded answer a matter of timing, and
            # the log claim two different answers for one host.
            if host in self._known:
                return
            self._known[host] = params
            self._settled.add(host)
        logger.info(f"{host} prices products with {params} — "
                    f"trying that first for the rest of this run")

    def candidates(self, host):
        """Parameter sets still worth trying for this host, most likely first."""
        if not host:
            return []
        with self._lock:
            known = self._known.get(host)
            if known:
                # The known answer first, but not *only* it. One storefront can
                # stock the same catalogue under several fulfilment modes, so a
                # product absent from the remembered one may still be priced
                # under another; returning just the known set silently loses
                # those. The alternates are dropped once they have failed to
                # earn their keep across several products.
                others = [p for p in PRICE_CONTEXT_PARAMS if p != known]
                if self._fallback_misses.get(host, 0) >= _CONTEXT_PROBE_LIMIT:
                    return [known]
                self._fallback_misses[host] = self._fallback_misses.get(host, 0) + 1
                return [known] + others
            if host in self._settled:
                return []
            if self._probes.get(host, 0) >= _CONTEXT_PROBE_LIMIT:
                self._settled.add(host)
                logger.debug(f"{host}: no fulfilment parameter restored prices; "
                             f"treating missing prices as genuine")
                return []
            self._probes[host] = self._probes.get(host, 0) + 1
        return list(PRICE_CONTEXT_PARAMS)

    def credit(self, host):
        """A fallback attempt paid off — stop counting against the alternates."""
        if host:
            with self._lock:
                self._fallback_misses.pop(host, None)

    def settle(self, host):
        """Record that this host needs nothing — prices arrive unprompted."""
        if host:
            with self._lock:
                self._settled.add(host)


CONTEXT_MEMO = _ContextMemo()


def with_params(url, params):
    """Same URL with extra query parameters, leaving existing ones intact."""
    if not params:
        return url
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parts._replace(query=urlencode(query)))


class ScraperClient:
    def __init__(self):
        # One session per fingerprint, created on first use. Sessions are
        # cheap to hold and expensive to recreate per request (connection
        # reuse is most of the speed on a few-thousand-URL run).
        self._sessions = {}
        self._async_sessions = {}
        self._lock = threading.Lock()

    # ── session management ───────────────────────────────────────────────

    def _session(self, profile):
        with self._lock:
            session = self._sessions.get(profile)
            if session is None:
                session = requests.Session(impersonate=profile)
                self._sessions[profile] = session
            return session

    def _async_session(self, profile, preferred=None):
        """Async session for a profile.

        ``preferred`` is the session the caller already opened (main.py owns
        one for the default profile and shares it with the image downloader);
        reusing it avoids opening a second connection pool for the common case.
        """
        if preferred is not None and profile == DEFAULT_PROFILE:
            return preferred
        session = self._async_sessions.get(profile)
        if session is None:
            session = requests.AsyncSession(impersonate=profile)
            self._async_sessions[profile] = session
        return session

    async def aclose(self):
        """Close any async sessions this client opened itself."""
        for session in list(self._async_sessions.values()):
            try:
                await session.close()
            except Exception:
                pass
        self._async_sessions.clear()

    def close(self):
        for session in list(self._sessions.values()):
            try:
                session.close()
            except Exception:
                pass
        self._sessions.clear()

    # ── response classification ──────────────────────────────────────────

    @staticmethod
    def _classify(response, binary=False):
        """(outcome, reason) for a response.

        outcome is 'ok' | 'permanent' | 'fingerprint' | 'throttled' | 'transient'.
        ``binary`` skips the text-body checks — decoding an image as text to
        look for a challenge marker is both wasteful and meaningless.
        """
        status = response.status_code
        if status == 200:
            if binary:
                return ('ok', None) if response.content else ('transient', 'empty response body')
            text = response.text
            if looks_like_challenge(text, response.headers):
                return 'fingerprint', 'bot challenge page'
            if not text:
                return 'transient', 'empty response body'
            # A short body is only suspicious for HTML. Plenty of API and
            # sitemap endpoints legitimately answer with a few bytes, and
            # retrying those wastes nine requests to learn nothing.
            content_type = str(response.headers.get('content-type', '')).lower()
            if 'html' in content_type and len(text) < _MIN_CREDIBLE_HTML:
                return 'transient', f'suspiciously short HTML ({len(text)} bytes)'
            return 'ok', None
        reason = f'HTTP {status}'
        if status in PERMANENT_STATUSES:
            return 'permanent', reason
        if status in THROTTLE_STATUSES:
            return 'throttled', reason
        if status in FINGERPRINT_STATUSES:
            return 'fingerprint', reason
        return 'transient', reason

    # ── sync ─────────────────────────────────────────────────────────────

    def fetch(self, url, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF,
              timeout=DEFAULT_TIMEOUT):
        """Fetch a URL, escalating fingerprints as needed. Returns (response, reason)."""
        url = normalize_url(url)
        host = _host_of(url)
        last_reason = 'unknown error'
        blocked = False

        for profile in PROFILE_MEMO.ladder(host):
            rejected_fingerprint = False
            for attempt in range(1, retries + 1):
                retry_after = None
                try:
                    if profile == 'playwright':
                        response = fetch_playwright_sync(url, timeout=timeout)
                    else:
                        response = self._session(profile).get(url, timeout=timeout)
                except Exception as e:
                    last_reason = f'{type(e).__name__}: {e}'
                    outcome = 'transient'
                else:
                    outcome, reason = self._classify(response)
                    if outcome == 'ok':
                        PROFILE_MEMO.set(host, profile)
                        return response, None
                    last_reason = reason or last_reason
                    if outcome == 'permanent':
                        logger.debug(f"Not retrying {url}: {last_reason}")
                        return None, last_reason
                    if outcome == 'fingerprint':
                        rejected_fingerprint = blocked = True
                        break  # a different browser profile is the only thing that helps
                    if outcome == 'throttled':
                        retry_after = response.headers.get('Retry-After')

                if attempt < retries:
                    delay = retry_delay(attempt - 1, retry_after)
                    logger.debug(f"Attempt {attempt}/{retries} failed for {url} "
                                 f"({last_reason}) — retrying in {delay:.1f}s")
                    time.sleep(delay)

            if not rejected_fingerprint:
                # Timeouts, DNS and connection errors say nothing about which
                # browser we look like. Walking the rest of the ladder would
                # triple the wait on an unresponsive host for no chance of
                # success.
                break

        PROFILE_MEMO.record_failure(host, blocked=blocked)
        logger.debug(f"Giving up on {url}: {last_reason}")
        return None, last_reason

    def fetch_page(self, url, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF):
        """Page body, or None if every fingerprint and retry failed."""
        response, reason = self.fetch(url, retries=retries, backoff=backoff)
        if response is None:
            logger.debug(f"fetch_page failed for {url}: {reason}")
            return None
        return response.text

    def fetch_response(self, url, retries=1):
        """Like fetch_page but hands back the whole response.

        Some APIs report their total item count in a header (WooCommerce's
        Store API sends X-WP-Total), which lets us size a catalogue in one
        request instead of paging through it.
        """
        response, _ = self.fetch(url, retries=retries, timeout=20)
        return response

    def fetch_soup(self, url):
        html = self.fetch_page(url)
        return BeautifulSoup(html, 'lxml') if html else None

    def probe(self, url):
        """Reachability report for a host, for the pre-scrape analysis.

        Distinguishes 'we got in' from 'we were challenged', because an
        interactive Cloudflare challenge is something the user needs told
        about rather than something to keep retrying.
        """
        url = normalize_url(url)
        host = _host_of(url)
        blocked_reason = None
        for profile in PROFILE_MEMO.ladder(host):
            try:
                if profile == 'playwright':
                    response = fetch_playwright_sync(url, timeout=20)
                else:
                    response = self._session(profile).get(url, timeout=20)
            except Exception as e:
                # A network-level failure is not a fingerprint problem; trying
                # the rest of the ladder just triples the wait.
                return {'ok': False, 'html': None, 'profile': None, 'status': None,
                        'reason': f'{type(e).__name__}: {e}'}
            outcome, reason = self._classify(response)
            if outcome == 'ok':
                PROFILE_MEMO.set(host, profile)
                return {'ok': True, 'html': response.text, 'profile': profile,
                        'status': response.status_code, 'reason': None}
            blocked_reason = reason
            if outcome == 'permanent':
                break
        return {'ok': False, 'html': None, 'profile': None,
                'status': None, 'reason': blocked_reason or 'unreachable'}

    # ── async ────────────────────────────────────────────────────────────

    async def fetch_page_async(self, async_session, url, retries=DEFAULT_RETRIES,
                               backoff=DEFAULT_BACKOFF):
        """Fetch a page, escalating fingerprints then retrying. Returns (html, reason).

        html is None when every attempt failed; reason then explains why, so the
        caller can record which URLs were missed instead of silently skipping them.
        """
        response, reason = await self._fetch_async(async_session, url, retries, backoff)
        return (response.text if response is not None else None), reason

    async def fetch_bytes_async(self, async_session, url, retries=2,
                                backoff=DEFAULT_BACKOFF, timeout=DEFAULT_TIMEOUT):
        """Fetch binary content (images) through the same escalation ladder.

        Images live on the same host as the pages, so a host that rejects our
        fingerprint rejects them too. Downloading them over a separate,
        hardcoded-profile session meant a store we had successfully scraped
        still came back with none of its pictures.
        """
        response, reason = await self._fetch_async(async_session, url, retries, backoff,
                                                   timeout=timeout, binary=True)
        return (response.content if response is not None else None), reason

    async def _fetch_async(self, async_session, url, retries=DEFAULT_RETRIES,
                           backoff=DEFAULT_BACKOFF, timeout=DEFAULT_TIMEOUT,
                           binary=False):
        """Shared async fetch loop. Returns (response, reason)."""
        url = normalize_url(url)
        host = _host_of(url)
        last_reason = 'unknown error'
        blocked = False

        for profile in PROFILE_MEMO.ladder(host):
            session = self._async_session(profile, preferred=async_session)
            rejected_fingerprint = False
            for attempt in range(1, retries + 1):
                retry_after = None
                try:
                    if profile == 'playwright':
                        response = await fetch_playwright_async(url, timeout=timeout)
                    else:
                        response = await session.get(url, timeout=timeout)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_reason = f'{type(e).__name__}: {e}'
                    outcome = 'transient'
                else:
                    outcome, reason = self._classify(response, binary=binary)
                    if outcome == 'ok':
                        PROFILE_MEMO.set(host, profile)
                        return response, None
                    last_reason = reason or last_reason
                    if outcome == 'permanent':
                        logger.error(f"Failed to fetch {url}: {last_reason} (not retrying)")
                        return None, last_reason
                    if outcome == 'fingerprint':
                        rejected_fingerprint = blocked = True
                        break
                    if outcome == 'throttled':
                        retry_after = response.headers.get('Retry-After')

                if attempt < retries:
                    delay = retry_delay(attempt - 1, retry_after)
                    logger.warning(f"Attempt {attempt}/{retries} failed for {url} "
                                   f"({last_reason}) — retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)

            if not rejected_fingerprint:
                # See fetch(): a network-level failure is not a fingerprint
                # problem, so the rest of the ladder cannot help.
                break

        PROFILE_MEMO.record_failure(host, blocked=blocked)
        logger.error(f"Giving up on {url}: {last_reason}")
        return None, last_reason
