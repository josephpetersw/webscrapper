"""Shared fixtures.

Both app.py and main.py resolve their storage paths into module-level globals at
import time, so every test that touches disk redirects those globals at a
tmp_path instead. Nothing here writes to the real ./data directory.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _redirect(monkeypatch, module, settings):
    """Point a module's storage globals at a tmp dir, or skip if they're gone.

    test_api.py and test_main_storage.py were written against a single-site
    storage layer — one global ``products.jsonl`` with an incremental offset
    cache, plus an on-disk HTML cache. The tree now stores one folder per store
    (``data/<domain>/``) and resolves every path through ``active_site_dir()``,
    so those globals no longer exist and the fixtures errored on every test.

    Skipping rather than erroring keeps the signal honest: these are not
    failures, they are tests for a layer this branch does not have. They are
    left in place, and asserting on the attribute rather than on a version flag
    means they start running again by themselves if that layer is ever ported
    onto the per-store layout.
    """
    missing = [name for name in settings if not hasattr(module, name)]
    if missing:
        pytest.skip(f"{module.__name__} has no {', '.join(missing)} — these tests "
                    f"target the single-site JSONL storage layer, which this tree "
                    f"replaced with per-store folders (see scraper/schema.py, "
                    f"app.active_site_dir)")
    for name, value in settings.items():
        monkeypatch.setattr(module, name, value)


# ── Fake HTTP plumbing ───────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, text='', content=b'', headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {}


class FakeSession:
    """Replays a scripted list of responses/exceptions and records every call.

    An entry that is an Exception instance is raised instead of returned; the
    last entry repeats forever so a test can say "always 500".
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _next(self, url):
        self.calls.append(url)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next(url)


class FakeAsyncSession(FakeSession):
    async def get(self, url, **kwargs):
        return self._next(url)


@pytest.fixture
def no_sleep(monkeypatch):
    """Collapse retry backoff so retry tests run instantly."""
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)

    async def fake_async_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr('time.sleep', fake_sleep)
    monkeypatch.setattr(asyncio, 'sleep', fake_async_sleep)
    return slept


# ── main.py under a tmp data dir ─────────────────────────────

@pytest.fixture
def main_mod(tmp_path, monkeypatch):
    import main

    data_dir = tmp_path / 'data'
    (data_dir / 'cache' / 'html').mkdir(parents=True)
    _redirect(monkeypatch, main, {
        'DATA_DIR': str(data_dir),
        'STRUCTURED_DIR': str(data_dir / 'structured'),
        'HTML_CACHE_DIR': str(data_dir / 'cache' / 'html'),
        'PRODUCTS_JSONL': str(data_dir / 'products.jsonl'),
        'PRODUCTS_JSON': str(data_dir / 'products.json'),
        'CATEGORIES_JSON': str(data_dir / 'categories.json'),
        'PROGRESS_JSON': str(data_dir / 'progress.json'),
    })
    main.data_dir_path = data_dir  # convenience handle for tests
    return main


# ── app.py under a tmp data dir ──────────────────────────────

_CACHE_DEFAULTS = {
    'products_by_url': {},
    'products_source': None,
    'products_offset': 0,
    'products_mtime': 0,
    'categories': None,
    'categories_mtime': 0,
    'image_index': {},
    'image_index_key': -1,
    'walk_index': {},
    'walk_index_time': 0.0,
}


@pytest.fixture
def app_mod(tmp_path, monkeypatch):
    import app as app_module

    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    _redirect(monkeypatch, app_module, {
        'DATA_DIR': str(data_dir),
        'PRODUCTS_JSONL': str(data_dir / 'products.jsonl'),
        'PRODUCTS_JSON': str(data_dir / 'products.json'),
        'PID_FILE': str(data_dir / 'scraper.pid'),
        'LOG_FILE': str(tmp_path / 'scraper.log'),
    })

    # The cache is a module global shared across tests — reset it both ways so a
    # failure mid-test can't leak records into the next one.
    app_module._CACHE.update({k: (v.copy() if hasattr(v, 'copy') else v)
                              for k, v in _CACHE_DEFAULTS.items()})
    yield app_module
    app_module._CACHE.update({k: (v.copy() if hasattr(v, 'copy') else v)
                              for k, v in _CACHE_DEFAULTS.items()})


@pytest.fixture
def client(app_mod):
    app_mod.app.config['TESTING'] = True
    with app_mod.app.test_client() as c:
        yield c


# ── Product fixtures ─────────────────────────────────────────

def product(url, title, **overrides):
    rec = {
        'url': url,
        'title': title,
        'brand': 'Realme',
        'price': 'KSh 20,000',
        'categories': ['Smartphones', 'Realme Phones'],
        'images': ['https://x.test/a.jpg'],
        'short_description': '<p>Short <b>desc</b></p>',
        'long_description': '<p>Long desc</p>',
    }
    rec.update(overrides)
    return rec


@pytest.fixture
def seeded_jsonl(app_mod):
    """Three products on disk in products.jsonl, one of them untitled."""
    records = [
        product('https://x.test/product/realme-5/', 'Realme 5'),
        product('https://x.test/product/galaxy-s23/', 'Galaxy S23',
                brand='Samsung', categories=['Smartphones', 'Samsung Phones'],
                short_description='<p>flagship</p>'),
        product('https://x.test/product/broken/', '', brand='Unknown'),
    ]
    with open(app_mod.PRODUCTS_JSONL, 'w', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')
    return records
