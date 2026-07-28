"""Storage and resume logic in main.py — the parts that decide whether a killed
run picks up where it left off or silently re-scrapes 2,000 products."""
import gzip
import json
import os

import pytest

import app as app_module
import main as main_module
from scraper import paths as product_paths


# ── Path slugging ────────────────────────────────────────────
# Slugging moved into scraper/paths.py so main.py and app.py cannot drift; the
# old main._safe_component is now paths.safe_path_segment, re-exported by
# main.py as safe_path_segment.

@pytest.mark.parametrize('raw,expected', [
    ('Realme 5 (128GB + 4GB RAM)', 'Realme_5_128GB_4GB_RAM'),
    ('Realme Buds Air 2', 'Realme_Buds_Air_2'),
    ('  leading & trailing  ', 'leading_trailing'),
    ('Smartphones', 'Smartphones'),
])
def test_safe_path_segment(raw, expected):
    assert product_paths.safe_path_segment(raw) == expected


def test_segment_with_nothing_usable_is_named_not_empty():
    """An empty string would make os.path.join silently drop the level, putting
    the product in its parent category's folder; 'unnamed' keeps it separate."""
    assert product_paths.safe_path_segment('---') == 'unnamed'
    assert product_paths.safe_path_segment('') == 'unnamed'


def test_long_segments_are_capped_but_stay_distinct():
    """Two configurations of one product share a long prefix. Truncating without
    a disambiguator would land them in the same folder and overwrite each other."""
    a = product_paths.safe_path_segment('Samsung Galaxy A06 4GB RAM 128GB Black Edition', 40)
    b = product_paths.safe_path_segment('Samsung Galaxy A06 4GB RAM 128GB Green Edition', 40)
    assert len(a) <= 40 and len(b) <= 40
    assert a != b


@pytest.mark.parametrize('raw', [
    'Realme 5 (128GB + 4GB RAM)',
    'Xiaomi Redmi Note 12 Pro+ 5G',
    'A/B\\C:D*E?F"G<H>I|J',
    '  spaced  out  ',
])
def test_app_and_main_slugging_stay_identical(raw):
    """app.py serves images out of folders main.py created. If these two ever
    disagree the dashboard shows broken thumbnails for every product.

    They now share one implementation, so this asserts the sharing is real —
    that neither module has quietly grown its own copy again."""
    assert main_module.safe_path_segment(raw) == product_paths.safe_path_segment(raw)
    assert app_module.product_paths.safe_path_segment(raw) == product_paths.safe_path_segment(raw)


def test_structured_dir_nests_by_category(main_mod):
    data = {'title': 'Realme XT', 'categories': ['Smartphones', 'Realme Phones']}
    path = main_mod._structured_dir_for(data, 'https://x.test/product/realme-xt/')
    assert path == os.path.join(main_mod.STRUCTURED_DIR, 'Smartphones', 'Realme_Phones', 'Realme_XT')


def test_structured_dir_uses_uncategorized_when_categories_are_missing(main_mod):
    path = main_mod._structured_dir_for({'title': 'Orphan'}, 'https://x.test/product/orphan/')
    assert path == os.path.join(main_mod.STRUCTURED_DIR, 'Uncategorized', 'Orphan')


def test_structured_dir_falls_back_to_the_url_slug_without_a_title(main_mod):
    path = main_mod._structured_dir_for({'categories': []}, 'https://x.test/product/realme-c21/')
    assert path.endswith(os.path.join('Uncategorized', 'realme_c21'))


def test_structured_dir_skips_categories_that_slug_to_nothing(main_mod):
    data = {'title': 'Phone', 'categories': ['Smartphones', '---', 'Realme']}
    path = main_mod._structured_dir_for(data, 'https://x.test/p/')
    assert path == os.path.join(main_mod.STRUCTURED_DIR, 'Smartphones', 'Realme', 'Phone')


# ── HTML cache ───────────────────────────────────────────────

def test_html_cache_roundtrip(main_mod):
    url = 'https://x.test/product/realme-5/'
    main_mod._write_cached_html(url, '<html>cached</html>')
    assert main_mod._read_cached_html(url) == '<html>cached</html>'


def test_html_cache_key_is_per_url(main_mod):
    main_mod._write_cached_html('https://x.test/a/', 'A')
    assert main_mod._read_cached_html('https://x.test/b/') is None


def test_html_cache_miss_returns_none(main_mod):
    assert main_mod._read_cached_html('https://x.test/never-fetched/') is None


def test_corrupt_cache_entry_is_treated_as_a_miss(main_mod):
    """A run killed mid-write used to leave a truncated .gz; reading it must not
    crash the whole scrape."""
    url = 'https://x.test/p/'
    path = main_mod._html_cache_path(url)
    with open(path, 'wb') as f:
        f.write(b'not gzip at all')
    assert main_mod._read_cached_html(url) is None


def test_cache_write_leaves_no_tmp_file(main_mod):
    main_mod._write_cached_html('https://x.test/p/', 'body')
    leftovers = [f for f in os.listdir(main_mod.HTML_CACHE_DIR) if f.endswith('.tmp')]
    assert leftovers == []


def test_cached_html_is_actually_compressed(main_mod):
    url = 'https://x.test/p/'
    main_mod._write_cached_html(url, 'body' * 100)
    with gzip.open(main_mod._html_cache_path(url), 'rt', encoding='utf-8') as f:
        assert f.read() == 'body' * 100


# ── JSONL product records ────────────────────────────────────

def write_jsonl(main_mod, lines):
    with open(main_mod.PRODUCTS_JSONL, 'w', encoding='utf-8') as f:
        f.write(''.join(lines))


def test_read_all_products_without_a_file(main_mod):
    assert main_mod._read_all_products() == []


def test_append_then_read_roundtrip(main_mod):
    main_mod._append_product_record({'url': 'https://x.test/a/', 'title': 'A'})
    main_mod._append_product_record({'url': 'https://x.test/b/', 'title': 'B'})
    assert [p['title'] for p in main_mod._read_all_products()] == ['A', 'B']


def test_rescraped_url_keeps_only_the_latest_record(main_mod):
    main_mod._append_product_record({'url': 'https://x.test/a/', 'title': 'old'})
    main_mod._append_product_record({'url': 'https://x.test/a/', 'title': 'new'})
    products = main_mod._read_all_products()
    assert len(products) == 1
    assert products[0]['title'] == 'new'


def test_torn_final_line_is_skipped_not_fatal(main_mod):
    """products.jsonl is appended to live; a SIGKILL can cut the last line."""
    write_jsonl(main_mod, [
        json.dumps({'url': 'https://x.test/a/', 'title': 'A'}) + '\n',
        '{"url": "https://x.test/b/", "titl',
    ])
    assert [p['title'] for p in main_mod._read_all_products()] == ['A']


def test_records_without_a_url_are_still_returned(main_mod):
    write_jsonl(main_mod, [
        json.dumps({'title': 'no url 1'}) + '\n',
        json.dumps({'title': 'no url 2'}) + '\n',
    ])
    assert len(main_mod._read_all_products()) == 2


def test_non_ascii_survives_the_roundtrip(main_mod):
    main_mod._append_product_record({'url': 'https://x.test/a/', 'title': 'Téléphone – 5″'})
    assert main_mod._read_all_products()[0]['title'] == 'Téléphone – 5″'


# ── Resume ───────────────────────────────────────────────────

def test_scraped_urls_come_from_the_jsonl(main_mod):
    main_mod._append_product_record({'url': 'https://x.test/a/'})
    main_mod._append_product_record({'url': 'https://x.test/b/'})
    assert main_mod._load_scraped_urls() == {'https://x.test/a/', 'https://x.test/b/'}


def test_scraped_urls_fall_back_to_legacy_products_json(main_mod):
    with open(main_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        json.dump([{'url': 'https://x.test/legacy/'}], f)
    assert main_mod._load_scraped_urls() == {'https://x.test/legacy/'}


def test_jsonl_wins_over_legacy_json_when_both_exist(main_mod):
    main_mod._append_product_record({'url': 'https://x.test/fresh/'})
    with open(main_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        json.dump([{'url': 'https://x.test/legacy/'}], f)
    assert main_mod._load_scraped_urls() == {'https://x.test/fresh/'}


def test_corrupt_legacy_json_yields_no_urls_instead_of_raising(main_mod):
    with open(main_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        f.write('{{{ not json')
    assert main_mod._load_scraped_urls() == set()


def test_nothing_scraped_yet(main_mod):
    assert main_mod._load_scraped_urls() == set()


# ── Atomic JSON writes ───────────────────────────────────────

def test_write_json_atomic_replaces_and_cleans_up(main_mod, tmp_path):
    target = str(tmp_path / 'out.json')
    main_mod._write_json_atomic(target, {'a': 1})
    main_mod._write_json_atomic(target, {'a': 2}, indent=2)

    with open(target, encoding='utf-8') as f:
        assert json.load(f) == {'a': 2}
    assert not os.path.exists(target + '.tmp')


# ── Progress reporting ───────────────────────────────────────

def test_progress_throttles_writes(main_mod):
    reporter = main_mod.ProgressReporter(total=100, min_interval=3600)
    reporter.update(1, force=True)
    reporter.update(50)  # throttled away

    with open(main_mod.PROGRESS_JSON, encoding='utf-8') as f:
        assert json.load(f)['current'] == 1


def test_progress_force_bypasses_the_throttle(main_mod):
    reporter = main_mod.ProgressReporter(total=100, min_interval=3600)
    reporter.update(1, force=True)
    reporter.update(50, force=True)

    with open(main_mod.PROGRESS_JSON, encoding='utf-8') as f:
        payload = json.load(f)
    assert payload['current'] == 50
    assert payload['total'] == 100
    assert payload['eta'] >= 0


def test_progress_eta_is_zero_before_the_first_completion(main_mod):
    main_mod.ProgressReporter(total=10).update(0, force=True)
    with open(main_mod.PROGRESS_JSON, encoding='utf-8') as f:
        assert json.load(f) == {'current': 0, 'total': 10, 'eta': 0}


def test_progress_writes_are_not_throttled_once_the_interval_passes(main_mod):
    reporter = main_mod.ProgressReporter(total=10, min_interval=0)
    reporter.update(3)
    reporter.update(7)
    with open(main_mod.PROGRESS_JSON, encoding='utf-8') as f:
        assert json.load(f)['current'] == 7


# ── Per-product file output ──────────────────────────────────

def test_write_product_files_emits_markdown_text_and_json(main_mod, tmp_path):
    out = str(tmp_path / 'product')
    data = {
        'url': 'https://x.test/p/',
        'title': 'Realme 5',
        'long_description': '<h2>Overview</h2><p>Great phone</p>',
        'short_description': '<ul><li>RAM: 4 GB</li><li>Storage: 128 GB</li></ul>',
    }
    main_mod.write_product_files(data, out)

    assert os.path.isdir(os.path.join(out, 'images'))
    with open(os.path.join(out, 'description.md'), encoding='utf-8') as f:
        md = f.read()
    assert '## Overview' in md and 'Great phone' in md

    with open(os.path.join(out, 'short_description.txt'), encoding='utf-8') as f:
        txt = f.read()
    assert 'RAM: 4 GB' in txt and '<li>' not in txt

    with open(os.path.join(out, 'data.json'), encoding='utf-8') as f:
        assert json.load(f) == data


def test_write_product_files_skips_absent_descriptions(main_mod, tmp_path):
    out = str(tmp_path / 'product')
    main_mod.write_product_files({'title': 'Bare', 'url': 'https://x.test/p/'}, out)

    assert not os.path.exists(os.path.join(out, 'description.md'))
    assert not os.path.exists(os.path.join(out, 'short_description.txt'))
    assert os.path.exists(os.path.join(out, 'data.json'))


def test_write_product_files_is_idempotent(main_mod, tmp_path):
    out = str(tmp_path / 'product')
    data = {'title': 'Realme 5', 'url': 'https://x.test/p/', 'long_description': '<p>v1</p>'}
    main_mod.write_product_files(data, out)
    main_mod.write_product_files({**data, 'long_description': '<p>v2</p>'}, out)

    with open(os.path.join(out, 'description.md'), encoding='utf-8') as f:
        assert 'v2' in f.read()
