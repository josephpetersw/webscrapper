"""Retry policy for page fetches. The rule that matters: transient statuses are
retried, permanent ones (404) are not — burning 4 requests per dead sitemap URL
across ~2k products is the difference between a 10-minute and an hour-long run.
"""
import asyncio

import pytest

from scraper import client as client_mod
from scraper.client import MAX_RETRIES, RETRYABLE_STATUSES, ScraperClient, _retry_delay

from conftest import FakeAsyncSession, FakeResponse, FakeSession


@pytest.fixture
def scraper_client(monkeypatch):
    """A ScraperClient with no real curl session behind it."""
    monkeypatch.setattr(client_mod.requests, 'Session', lambda **kw: None)
    return ScraperClient()


def attach(scraper_client, script):
    session = FakeSession(script)
    scraper_client.session = session
    return session


# ── _retry_delay ─────────────────────────────────────────────

def test_retry_after_header_is_honoured():
    assert _retry_delay(0, retry_after='5') == 5.0


def test_retry_after_is_clamped_to_a_minute():
    assert _retry_delay(0, retry_after='3600') == 60.0


def test_unparseable_retry_after_falls_back_to_backoff():
    delay = _retry_delay(1, retry_after='Wed, 21 Oct 2015 07:28:00 GMT')
    assert 2 <= delay <= 3


def test_backoff_grows_with_attempt_and_is_capped():
    assert 1 <= _retry_delay(0) <= 2
    assert 4 <= _retry_delay(2) <= 5
    assert _retry_delay(20) == 30.0


# ── fetch_page ───────────────────────────────────────────────

def test_success_returns_body_in_one_request(scraper_client):
    session = attach(scraper_client, [FakeResponse(200, text='<html/>')])
    assert scraper_client.fetch_page('https://x.test/p/') == '<html/>'
    assert len(session.calls) == 1


def test_404_is_not_retried(scraper_client, no_sleep):
    session = attach(scraper_client, [FakeResponse(404)])
    assert scraper_client.fetch_page('https://x.test/gone/') is None
    assert len(session.calls) == 1
    assert no_sleep == []


@pytest.mark.parametrize('status', sorted(RETRYABLE_STATUSES))
def test_transient_status_recovers_on_a_later_attempt(scraper_client, no_sleep, status):
    session = attach(scraper_client, [FakeResponse(status), FakeResponse(200, text='ok')])
    assert scraper_client.fetch_page('https://x.test/p/') == 'ok'
    assert len(session.calls) == 2


def test_persistent_transient_status_gives_up_after_max_retries(scraper_client, no_sleep):
    session = attach(scraper_client, [FakeResponse(503)])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    assert len(session.calls) == MAX_RETRIES + 1
    assert len(no_sleep) == MAX_RETRIES


def test_retry_after_header_drives_the_wait_on_429(scraper_client, no_sleep):
    attach(scraper_client, [
        FakeResponse(429, headers={'Retry-After': '7'}),
        FakeResponse(200, text='ok'),
    ])
    assert scraper_client.fetch_page('https://x.test/p/') == 'ok'
    assert no_sleep == [7.0]


def test_network_exception_is_retried_then_succeeds(scraper_client, no_sleep):
    session = attach(scraper_client, [ConnectionError('reset'), FakeResponse(200, text='ok')])
    assert scraper_client.fetch_page('https://x.test/p/') == 'ok'
    assert len(session.calls) == 2


def test_network_exception_eventually_returns_none(scraper_client, no_sleep):
    session = attach(scraper_client, [TimeoutError('timed out')])
    assert scraper_client.fetch_page('https://x.test/p/') is None
    assert len(session.calls) == MAX_RETRIES + 1


def test_fetch_soup_returns_none_when_the_fetch_fails(scraper_client, no_sleep):
    attach(scraper_client, [FakeResponse(404)])
    assert scraper_client.fetch_soup('https://x.test/gone/') is None


def test_fetch_soup_parses_html(scraper_client):
    attach(scraper_client, [FakeResponse(200, text='<h1 class="t">Hi</h1>')])
    soup = scraper_client.fetch_soup('https://x.test/p/')
    assert soup.find('h1', class_='t').text == 'Hi'


# ── fetch_page_async ─────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def test_async_success(no_sleep):
    session = FakeAsyncSession([FakeResponse(200, text='body')])
    assert run(ScraperClient.fetch_page_async(session, 'https://x.test/p/')) == 'body'
    assert len(session.calls) == 1


def test_async_404_is_not_retried(no_sleep):
    session = FakeAsyncSession([FakeResponse(404)])
    assert run(ScraperClient.fetch_page_async(session, 'https://x.test/p/')) is None
    assert len(session.calls) == 1


def test_async_transient_status_retries_then_gives_up(no_sleep):
    session = FakeAsyncSession([FakeResponse(500)])
    assert run(ScraperClient.fetch_page_async(session, 'https://x.test/p/')) is None
    assert len(session.calls) == MAX_RETRIES + 1


def test_async_exception_is_retried(no_sleep):
    session = FakeAsyncSession([ConnectionError('reset'), FakeResponse(200, text='ok')])
    assert run(ScraperClient.fetch_page_async(session, 'https://x.test/p/')) == 'ok'
    assert len(session.calls) == 2
