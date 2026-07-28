"""Retry policy for page fetches.

Two rules matter. Permanent statuses (404) are not retried — burning four
requests per dead sitemap URL across ~2k products is the difference between a
10-minute run and an hour-long one. And a 429 is a rate-limit signal, not a
fingerprint problem: it is honoured with the server's own Retry-After and
retried on the same profile, rather than escalating to another browser and
immediately asking again.

Originally written against a pre-escalation client API (MAX_RETRIES,
RETRYABLE_STATUSES, a bare-body fetch_page_async). Rewritten for the current
one, which walks a fingerprint ladder and returns (html, reason) so callers can
record why a URL was missed.
"""
import asyncio

import pytest

from scraper import client as client_mod
from scraper.client import (
    BACKOFF_CAP,
    DEFAULT_RETRIES,
    FINGERPRINT_STATUSES,
    PERMANENT_STATUSES,
    RETRY_AFTER_CAP,
    ScraperClient,
    retry_delay,
)

from conftest import FakeAsyncSession, FakeResponse, FakeSession


@pytest.fixture(autouse=True)
def fresh_profile_memo(monkeypatch):
    """The profile memo is a module global: what one test learns about a host
    would otherwise change which ladder the next one walks."""
    monkeypatch.setattr(client_mod, 'PROFILE_MEMO', client_mod._ProfileMemo())


@pytest.fixture
def scraper_client(monkeypatch):
    """A ScraperClient with no real curl session behind it."""
    client = ScraperClient()
    monkeypatch.setattr(client, '_session', lambda profile: client.session)
    return client


def attach(scraper_client, script):
    session = FakeSession(script)
    scraper_client.session = session
    return session


def attach_async(scraper_client, script):
    session = FakeAsyncSession(script)
    scraper_client._async_session = lambda profile, preferred=None: session
    return session


# ── retry_delay ──────────────────────────────────────────────

def test_retry_after_header_is_honoured():
    assert retry_delay(0, retry_after='5') == 5.0


def test_retry_after_is_clamped():
    assert retry_delay(0, retry_after='3600') == RETRY_AFTER_CAP


def test_unparseable_retry_after_falls_back_to_backoff():
    delay = retry_delay(1, retry_after='Wed, 21 Oct 2015 07:28:00 GMT')
    assert 2 <= delay <= 3


def test_backoff_grows_with_attempt_and_is_capped():
    assert 1 <= retry_delay(0) <= 2
    assert 4 <= retry_delay(2) <= 5
    assert retry_delay(20) == BACKOFF_CAP


def test_backoff_is_jittered():
    """Eight workers backing off in lockstep retry in one burst; jitter is what
    spreads them out, so assert the delay is not a constant."""
    assert len({retry_delay(2) for _ in range(25)}) > 1


# ── fetch_page (sync) ────────────────────────────────────────

def test_success_returns_body_in_one_request(scraper_client):
    session = attach(scraper_client, [FakeResponse(200, text='<html/>' * 40)])
    assert scraper_client.fetch_page('https://x.test/p/').startswith('<html/>')
    assert len(session.calls) == 1


@pytest.mark.parametrize('status', sorted(PERMANENT_STATUSES))
def test_permanent_status_is_not_retried(scraper_client, no_sleep, status):
    session = attach(scraper_client, [FakeResponse(status)])
    assert scraper_client.fetch_page('https://x.test/gone/') is None
    assert len(session.calls) == 1
    assert no_sleep == []


def test_transient_status_recovers_on_a_later_attempt(scraper_client, no_sleep):
    session = attach(scraper_client, [FakeResponse(500), FakeResponse(200, text='ok' * 60)])
    assert scraper_client.fetch_page('https://x.test/p/').startswith('ok')
    assert len(session.calls) == 2


def test_persistent_transient_status_gives_up_after_retries(scraper_client, no_sleep):
    session = attach(scraper_client, [FakeResponse(500)])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    assert len(session.calls) == DEFAULT_RETRIES
    assert len(no_sleep) == DEFAULT_RETRIES - 1


def test_retry_after_header_drives_the_wait_on_429(scraper_client, no_sleep):
    attach(scraper_client, [
        FakeResponse(429, headers={'Retry-After': '7'}),
        FakeResponse(200, text='ok' * 60),
    ])
    assert scraper_client.fetch_page('https://x.test/p/').startswith('ok')
    assert no_sleep == [7.0]


def test_429_does_not_burn_the_fingerprint_ladder(scraper_client, no_sleep):
    """Rate limiting says nothing about which browser we look like. Escalating
    on a 429 would ask a throttled server the same question three more times."""
    session = attach(scraper_client, [FakeResponse(429, headers={'Retry-After': '1'})])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    assert len(session.calls) == DEFAULT_RETRIES  # one profile, not three


@pytest.mark.parametrize('status', sorted(FINGERPRINT_STATUSES))
def test_fingerprint_status_escalates_instead_of_retrying(scraper_client, no_sleep, status):
    session = attach(scraper_client, [FakeResponse(status)])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    # One attempt per profile in the ladder — not `retries` attempts each.
    assert len(session.calls) == len(client_mod.IMPERSONATE_PROFILES)


def test_network_exception_is_retried_then_succeeds(scraper_client, no_sleep):
    session = attach(scraper_client, [ConnectionError('reset'), FakeResponse(200, text='ok' * 60)])
    assert scraper_client.fetch_page('https://x.test/p/').startswith('ok')
    assert len(session.calls) == 2


def test_network_exception_eventually_returns_none(scraper_client, no_sleep):
    session = attach(scraper_client, [TimeoutError('timed out')])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    # A timeout is not a fingerprint problem, so the ladder is not walked.
    assert len(session.calls) == DEFAULT_RETRIES


def test_fetch_soup_returns_none_when_the_fetch_fails(scraper_client, no_sleep):
    attach(scraper_client, [FakeResponse(404)])
    assert scraper_client.fetch_soup('https://x.test/gone/') is None


def test_fetch_soup_parses_html(scraper_client):
    attach(scraper_client, [FakeResponse(200, text='<h1 class="t">Hi</h1>' + 'x' * 200)])
    soup = scraper_client.fetch_soup('https://x.test/p/')
    assert soup.find('h1', class_='t').text == 'Hi'


# ── fetch_page_async ─────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def test_async_success(scraper_client, no_sleep):
    session = attach_async(scraper_client, [FakeResponse(200, text='body' * 40)])
    html, reason = run(scraper_client.fetch_page_async(session, 'https://x.test/p/'))
    assert html.startswith('body')
    assert reason is None
    assert len(session.calls) == 1


def test_async_permanent_is_not_retried_and_explains_why(scraper_client, no_sleep):
    session = attach_async(scraper_client, [FakeResponse(404)])
    html, reason = run(scraper_client.fetch_page_async(session, 'https://x.test/p/'))
    assert html is None
    assert '404' in reason
    assert len(session.calls) == 1


def test_async_transient_status_retries_then_gives_up(scraper_client, no_sleep):
    session = attach_async(scraper_client, [FakeResponse(500)])
    html, reason = run(scraper_client.fetch_page_async(session, 'https://x.test/p/'))
    assert html is None
    assert len(session.calls) == DEFAULT_RETRIES


def test_async_exception_is_retried(scraper_client, no_sleep):
    session = attach_async(scraper_client,
                           [ConnectionError('reset'), FakeResponse(200, text='ok' * 60)])
    html, _ = run(scraper_client.fetch_page_async(session, 'https://x.test/p/'))
    assert html.startswith('ok')
    assert len(session.calls) == 2


def test_async_challenge_page_is_treated_as_a_fingerprint_block(scraper_client, no_sleep):
    """A bot wall that answers 200 would otherwise be parsed as a product."""
    session = attach_async(scraper_client,
                           [FakeResponse(200, text='<title>Just a moment...</title>')])
    html, reason = run(scraper_client.fetch_page_async(session, 'https://x.test/p/'))
    assert html is None
    assert len(session.calls) == len(client_mod.IMPERSONATE_PROFILES)
