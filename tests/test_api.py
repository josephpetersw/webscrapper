"""Flask API: the incremental products cache, filtering/pagination, and the
export formats. No network, no real scraper process — DATA_DIR is a tmp dir.
"""
import json
import os

import pytest

from conftest import product


def append(app_mod, *records):
    with open(app_mod.PRODUCTS_JSONL, 'a', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')


# ── clean_html ───────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('', ''),
    (None, ''),
    ('<p>Hello</p>', 'Hello'),
    ('a<br>b', 'a\nb'),
    ('<li>one</li><li>two</li>', 'one\ntwo'),
    ('&amp; &lt;tag&gt; &nbsp;', '& <tag>'),
    ('<p>a</p>\n\n\n<p>b</p>', 'a\nb'),
])
def test_clean_html(app_mod, raw, expected):
    assert app_mod.clean_html(raw).replace('\xa0', '').strip() == expected


def test_clean_html_strips_attributes_and_nested_markup(app_mod):
    raw = '<div class="x"><strong>RAM</strong>: 4 GB<br><em>Storage</em>: 128 GB</div>'
    assert app_mod.clean_html(raw) == 'RAM: 4 GB\nStorage: 128 GB'


def test_xml_escape(app_mod):
    assert app_mod._xml_escape('a & b <c> d') == 'a &amp; b &lt;c&gt; d'
    assert app_mod._xml_escape(42) == '42'


# ── Incremental JSONL cache ──────────────────────────────────

def test_cache_reads_the_whole_file_on_first_load(app_mod, seeded_jsonl):
    assert len(app_mod.load_products_from_cache()) == 3


def test_cache_only_parses_newly_appended_bytes(app_mod, seeded_jsonl):
    app_mod.load_products_from_cache()
    offset_after_first_read = app_mod._CACHE['products_offset']
    assert offset_after_first_read > 0

    append(app_mod, product('https://x.test/product/new/', 'New Phone'))
    titles = [p['title'] for p in app_mod.load_products_from_cache()]

    assert 'New Phone' in titles
    assert len(titles) == 4
    assert app_mod._CACHE['products_offset'] > offset_after_first_read


def test_cache_dedupes_a_rescraped_url_keeping_the_newest(app_mod, seeded_jsonl):
    app_mod.load_products_from_cache()
    append(app_mod, product('https://x.test/product/realme-5/', 'Realme 5 (updated)'))

    products = app_mod.load_products_from_cache()
    matches = [p for p in products if p['url'] == 'https://x.test/product/realme-5/']
    assert len(matches) == 1
    assert matches[0]['title'] == 'Realme 5 (updated)'


def test_cache_defers_a_torn_trailing_line_until_it_is_complete(app_mod):
    """A live scrape can be mid-write when the dashboard polls. The partial line
    must be ignored now and picked up once the newline lands."""
    append(app_mod, product('https://x.test/product/a/', 'A'))
    with open(app_mod.PRODUCTS_JSONL, 'a', encoding='utf-8') as f:
        f.write('{"url": "https://x.test/product/b/", "title": "B"')

    assert [p['title'] for p in app_mod.load_products_from_cache()] == ['A']

    with open(app_mod.PRODUCTS_JSONL, 'a', encoding='utf-8') as f:
        f.write('}\n')

    assert sorted(p['title'] for p in app_mod.load_products_from_cache()) == ['A', 'B']


def test_cache_resets_when_the_file_shrinks(app_mod, seeded_jsonl):
    """--force truncates products.jsonl; a stale byte offset would otherwise
    make the cache skip the entire new file."""
    app_mod.load_products_from_cache()

    with open(app_mod.PRODUCTS_JSONL, 'w', encoding='utf-8') as f:
        f.write(json.dumps(product('https://x.test/product/only/', 'Only')) + '\n')

    products = app_mod.load_products_from_cache()
    assert [p['title'] for p in products] == ['Only']


def test_cache_skips_unparseable_lines(app_mod):
    with open(app_mod.PRODUCTS_JSONL, 'w', encoding='utf-8') as f:
        f.write('this is not json\n')
        f.write(json.dumps(product('https://x.test/product/a/', 'A')) + '\n')
    assert [p['title'] for p in app_mod.load_products_from_cache()] == ['A']


def test_missing_jsonl_and_json_yields_no_products(app_mod):
    assert app_mod.load_products_from_cache() == []


def test_legacy_products_json_is_used_when_no_jsonl_exists(app_mod):
    with open(app_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        json.dump([product('https://x.test/product/legacy/', 'Legacy')], f)
    assert [p['title'] for p in app_mod.load_products_from_cache()] == ['Legacy']


def test_legacy_json_is_reloaded_when_its_mtime_moves(app_mod):
    with open(app_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        json.dump([product('https://x.test/product/a/', 'A')], f)
    app_mod.load_products_from_cache()

    with open(app_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        json.dump([product('https://x.test/product/a/', 'A'),
                   product('https://x.test/product/b/', 'B')], f)
    # Bump the mtime forward explicitly: two writes inside the same filesystem
    # timestamp granularity would otherwise look unchanged to the cache.
    future = os.path.getmtime(app_mod.PRODUCTS_JSON) + 10
    os.utime(app_mod.PRODUCTS_JSON, (future, future))

    assert len(app_mod.load_products_from_cache()) == 2


def test_corrupt_legacy_json_returns_empty_instead_of_500ing(app_mod):
    with open(app_mod.PRODUCTS_JSON, 'w', encoding='utf-8') as f:
        f.write('{{{')
    assert app_mod.load_products_from_cache() == []


# ── Categories cache ─────────────────────────────────────────

def test_categories_cache_reads_and_caches(app_mod):
    cat_file = os.path.join(app_mod.DATA_DIR, 'categories.json')
    with open(cat_file, 'w', encoding='utf-8') as f:
        json.dump(['Audio', 'Smartphones'], f)

    assert app_mod.load_categories_from_cache() == ['Audio', 'Smartphones']

    # Second call must not re-read: overwrite on disk without touching mtime
    mtime = os.path.getmtime(cat_file)
    with open(cat_file, 'w', encoding='utf-8') as f:
        json.dump(['Changed'], f)
    os.utime(cat_file, (mtime, mtime))
    assert app_mod.load_categories_from_cache() == ['Audio', 'Smartphones']


def test_missing_categories_file_yields_empty_list(app_mod):
    assert app_mod.load_categories_from_cache() == []


# ── /api/status, /api/progress, /api/logs ────────────────────

def test_status_reports_not_running_without_a_pid_file(client):
    body = client.get('/api/status').get_json()
    assert body == {'running': False, 'pid': None}


def test_status_ignores_a_stale_pid_file(client, app_mod):
    with open(app_mod.PID_FILE, 'w') as f:
        f.write('999999')  # almost certainly dead
    assert client.get('/api/status').get_json()['running'] is False


def test_status_ignores_a_garbage_pid_file(client, app_mod):
    with open(app_mod.PID_FILE, 'w') as f:
        f.write('not-a-pid')
    assert client.get('/api/status').get_json()['running'] is False


def test_stop_without_a_running_scraper_is_a_400(client):
    resp = client.post('/api/scrape/stop')
    assert resp.status_code == 400
    assert resp.get_json()['status'] == 'error'


def test_progress_defaults_when_no_file(client):
    assert client.get('/api/progress').get_json() == {'current': 0, 'total': 0}


def test_progress_serves_the_file(client, app_mod):
    with open(os.path.join(app_mod.DATA_DIR, 'progress.json'), 'w') as f:
        json.dump({'current': 7, 'total': 10, 'eta': 3.5}, f)
    assert client.get('/api/progress').get_json()['current'] == 7


def test_progress_survives_a_torn_progress_file(client, app_mod):
    with open(os.path.join(app_mod.DATA_DIR, 'progress.json'), 'w') as f:
        f.write('{"current": 7, "tot')
    assert client.get('/api/progress').get_json() == {'current': 0, 'total': 0}


def test_logs_missing_file(client):
    assert 'not found' in client.get('/api/logs').get_json()['logs'].lower()


def test_logs_tail_returns_the_last_n_lines(client, app_mod):
    with open(app_mod.LOG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(f'line {i}' for i in range(500)) + '\n')

    logs = client.get('/api/logs?lines=5').get_json()['logs'].splitlines()
    assert logs == [f'line {i}' for i in range(495, 500)]


def test_logs_tail_of_a_large_file_drops_the_partial_first_line(client, app_mod):
    """The endpoint seeks near EOF rather than reading the whole log, so the
    first line in the window is usually cut mid-way and must be discarded."""
    with open(app_mod.LOG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join('x' * 400 for _ in range(2000)) + '\n')

    logs = client.get('/api/logs?lines=3').get_json()['logs'].splitlines()
    assert len(logs) == 3
    assert all(line == 'x' * 400 for line in logs)


def test_logs_bad_lines_param_falls_back_to_the_default(client, app_mod):
    with open(app_mod.LOG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(f'line {i}' for i in range(300)) + '\n')
    logs = client.get('/api/logs?lines=abc').get_json()['logs'].splitlines()
    assert len(logs) == 100


# ── /api/products ────────────────────────────────────────────

def test_products_skips_untitled_records(client, seeded_jsonl):
    body = client.get('/api/products').get_json()
    assert body['total'] == 2
    assert all(p['title'] for p in body['data'])


def test_products_pagination(client, app_mod):
    append(app_mod, *[product(f'https://x.test/product/p{i}/', f'Phone {i}') for i in range(25)])

    first = client.get('/api/products?page=1&limit=10').get_json()
    assert len(first['data']) == 10
    assert first['total'] == 25
    assert first['totalPages'] == 3
    assert first['hasMore'] is True

    last = client.get('/api/products?page=3&limit=10').get_json()
    assert len(last['data']) == 5
    assert last['hasMore'] is False


def test_products_page_past_the_end_is_empty(client, seeded_jsonl):
    body = client.get('/api/products?page=99&limit=10').get_json()
    assert body['data'] == []
    assert body['hasMore'] is False


def test_products_search_is_case_insensitive_on_title(client, seeded_jsonl):
    body = client.get('/api/products?search=GALAXY').get_json()
    assert [p['title'] for p in body['data']] == ['Galaxy S23']


def test_products_search_also_matches_the_short_description(client, seeded_jsonl):
    body = client.get('/api/products?search=flagship').get_json()
    assert [p['title'] for p in body['data']] == ['Galaxy S23']


def test_products_search_tolerates_a_null_short_description(client, app_mod):
    append(app_mod, product('https://x.test/product/a/', 'Realme 5', short_description=None))
    body = client.get('/api/products?search=realme').get_json()
    assert body['total'] == 1


def test_products_category_filter_is_exact(client, seeded_jsonl):
    body = client.get('/api/products?category=Samsung Phones').get_json()
    assert [p['title'] for p in body['data']] == ['Galaxy S23']

    assert client.get('/api/products?category=Samsung').get_json()['total'] == 0


def test_products_returns_sorted_categories(client, seeded_jsonl, app_mod):
    with open(os.path.join(app_mod.DATA_DIR, 'categories.json'), 'w') as f:
        json.dump(['Smartphones', 'Audio'], f)
    assert client.get('/api/products').get_json()['categories'] == ['Audio', 'Smartphones']


# ── /api/stats ───────────────────────────────────────────────

def test_stats_counts_products_categories_brands_and_images(client, seeded_jsonl):
    body = client.get('/api/stats').get_json()
    # total_products counts every record, including the untitled one
    assert body['total_products'] == 3
    assert body['total_categories'] == 3        # Smartphones, Realme Phones, Samsung Phones
    assert body['total_brands'] == 2            # Realme, Samsung — 'Unknown' excluded
    assert body['total_images'] == 2            # untitled record is skipped


def test_stats_on_an_empty_dataset(client):
    assert client.get('/api/stats').get_json() == {
        'total_products': 0, 'total_categories': 0, 'total_brands': 0, 'total_images': 0,
    }


# ── Exports ──────────────────────────────────────────────────

@pytest.mark.parametrize('endpoint', [
    '/api/export/json',
    '/api/export/csv',
    '/api/export/excel',
    '/api/export/xml',
    '/api/export/brands_csv',
])
def test_exports_404_with_no_data(client, endpoint):
    assert client.get(endpoint).status_code == 404


def test_export_json_cleans_html_by_default(client, seeded_jsonl):
    resp = client.get('/api/export/json')
    assert resp.status_code == 200
    assert 'attachment' in resp.headers['Content-Disposition']

    records = json.loads(resp.data)
    realme = next(r for r in records if r['title'] == 'Realme 5')
    assert realme['short_description'] == 'Short desc'
    assert '<' not in realme['long_description']


def test_export_json_raw_keeps_markup(client, seeded_jsonl):
    records = json.loads(client.get('/api/export/json?clean=false').data)
    realme = next(r for r in records if r['title'] == 'Realme 5')
    assert realme['short_description'] == '<p>Short <b>desc</b></p>'


def test_export_json_does_not_mutate_the_shared_cache(client, seeded_jsonl, app_mod):
    """clean=true used to strip HTML in place, so the next dashboard request
    served de-tagged descriptions from cache."""
    client.get('/api/export/json?clean=true')

    cached = next(p for p in app_mod.load_products_from_cache() if p['title'] == 'Realme 5')
    assert cached['short_description'] == '<p>Short <b>desc</b></p>'


def test_export_csv_headers_and_row_shape(client, seeded_jsonl):
    import csv
    import io

    resp = client.get('/api/export/csv')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/csv'

    rows = list(csv.DictReader(io.StringIO(resp.data.decode('utf-8'))))
    assert len(rows) == 2  # untitled record excluded
    realme = next(r for r in rows if r['title'] == 'Realme 5')
    assert realme['categories'] == 'Smartphones > Realme Phones'
    assert realme['images'] == 'https://x.test/a.jpg'
    assert realme['short_description'] == 'Short desc'


def test_export_csv_raw_mode_keeps_tags(client, seeded_jsonl):
    import csv
    import io

    resp = client.get('/api/export/csv?clean=0')
    rows = list(csv.DictReader(io.StringIO(resp.data.decode('utf-8'))))
    realme = next(r for r in rows if r['title'] == 'Realme 5')
    assert '<b>' in realme['short_description']


def test_export_csv_joins_multiple_images(client, app_mod):
    append(app_mod, product('https://x.test/product/a/', 'A',
                            images=['https://x.test/1.jpg', 'https://x.test/2.jpg']))
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(client.get('/api/export/csv').data.decode())))
    assert rows[0]['images'] == 'https://x.test/1.jpg | https://x.test/2.jpg'


def test_export_xml_is_wellformed_and_escapes_values(client, app_mod):
    from xml.etree import ElementTree

    append(app_mod, product('https://x.test/product/a/', 'Phone & Co <Ltd>'))
    resp = client.get('/api/export/xml')
    assert resp.status_code == 200

    root = ElementTree.fromstring(resp.data)
    assert root.tag == 'products'
    products = root.findall('product')
    assert len(products) == 1
    assert products[0].findtext('title') == 'Phone & Co <Ltd>'
    assert products[0].findtext('categories') == 'Smartphones, Realme Phones'


def test_export_xml_skips_untitled_records(client, seeded_jsonl):
    from xml.etree import ElementTree
    root = ElementTree.fromstring(client.get('/api/export/xml').data)
    assert len(root.findall('product')) == 2


def test_export_xml_cleans_descriptions_by_default(client, seeded_jsonl):
    from xml.etree import ElementTree
    root = ElementTree.fromstring(client.get('/api/export/xml').data)
    titles = {p.findtext('title'): p for p in root.findall('product')}
    assert titles['Realme 5'].findtext('short_description') == 'Short desc'


def test_export_excel_returns_a_spreadsheet(client, seeded_jsonl):
    resp = client.get('/api/export/excel')
    assert resp.status_code == 200
    assert resp.data[:2] == b'PK'  # xlsx is a zip container
    assert 'spreadsheetml' in resp.mimetype


def test_export_brands_csv_is_deduped_and_sorted(client, seeded_jsonl, app_mod):
    append(app_mod, product('https://x.test/product/x/', 'X', brand=' Realme '))
    resp = client.get('/api/export/brands_csv')
    lines = resp.data.decode().strip().splitlines()
    assert lines[0] == 'Brand Name'
    assert lines[1:] == ['Realme', 'Samsung', 'Unknown']


def test_export_categories_csv_is_deduped_and_sorted(client, seeded_jsonl):
    resp = client.get('/api/export/categories_csv')
    lines = resp.data.decode().strip().splitlines()
    assert lines[0] == 'Category Name'
    assert lines[1:] == ['Realme Phones', 'Samsung Phones', 'Smartphones']


def test_export_categories_json_404s_without_the_file(client):
    assert client.get('/api/export/categories').status_code == 404


def test_export_categories_json_serves_the_file(client, app_mod):
    with open(os.path.join(app_mod.DATA_DIR, 'categories.json'), 'w') as f:
        json.dump(['Audio'], f)
    resp = client.get('/api/export/categories')
    assert resp.status_code == 200
    assert json.loads(resp.data) == ['Audio']


# ── Structured zip exports ───────────────────────────────────

@pytest.fixture
def structured_tree(app_mod):
    base = os.path.join(app_mod.DATA_DIR, 'structured', 'Smartphones', 'Realme_5')
    os.makedirs(os.path.join(base, 'images'))
    with open(os.path.join(base, 'data.json'), 'w') as f:
        json.dump({'title': 'Realme 5'}, f)
    with open(os.path.join(base, 'description.md'), 'w') as f:
        f.write('# Realme 5')
    with open(os.path.join(base, 'images', 'realme-5.jpg'), 'wb') as f:
        f.write(b'\xff\xd8jpeg')
    return base


def zip_names(resp):
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        return sorted(zf.namelist())


@pytest.mark.parametrize('endpoint', [
    '/api/export/structured',
    '/api/export/structured/data',
    '/api/export/structured/images',
    '/api/export/images',
])
def test_structured_exports_404_without_data(client, endpoint):
    assert client.get(endpoint).status_code == 404


def test_structured_export_contains_everything(client, structured_tree):
    names = zip_names(client.get('/api/export/structured'))
    assert names == [
        'Smartphones/Realme_5/data.json',
        'Smartphones/Realme_5/description.md',
        'Smartphones/Realme_5/images/realme-5.jpg',
    ]


def test_structured_data_export_excludes_images(client, structured_tree):
    names = zip_names(client.get('/api/export/structured/data'))
    assert names == ['Smartphones/Realme_5/data.json', 'Smartphones/Realme_5/description.md']


def test_structured_images_export_is_flattened_per_product(client, structured_tree):
    assert zip_names(client.get('/api/export/structured/images')) == ['Realme_5/realme-5.jpg']


def test_images_are_stored_not_deflated(client, app_mod):
    """Re-deflating JPEGs costs CPU for no size win — the zip must use STORED
    for image extensions and DEFLATE for text."""
    import zipfile
    assert app_mod._zip_compression_for('a.JPG') == zipfile.ZIP_STORED
    assert app_mod._zip_compression_for('a.webp') == zipfile.ZIP_STORED
    assert app_mod._zip_compression_for('data.json') == zipfile.ZIP_DEFLATED


def test_legacy_images_export_redirects_to_structured(client, structured_tree):
    assert zip_names(client.get('/api/export/images')) == zip_names(
        client.get('/api/export/structured'))


# ── /api/files ───────────────────────────────────────────────

def test_files_lists_directories_and_files(client, app_mod, structured_tree):
    tree = client.get('/api/files').get_json()
    names = {item['name']: item['type'] for item in tree}
    assert names['structured'] == 'directory'


def test_files_descends_into_a_subpath(client, structured_tree):
    tree = client.get('/api/files?path=Smartphones').get_json()
    assert tree == []  # 'Smartphones' lives under structured/, not at the root

    tree = client.get('/api/files?path=structured/Smartphones/Realme_5').get_json()
    names = sorted(item['name'] for item in tree)
    assert names == ['data.json', 'description.md', 'images']


@pytest.mark.parametrize('path', [
    '../',
    '../../etc',
    'structured/../../..',
])
def test_files_refuses_to_escape_the_data_dir(client, path):
    assert client.get('/api/files', query_string={'path': path}).get_json() == []


def test_files_empty_for_a_missing_subdir(client, app_mod):
    assert client.get('/api/files?path=does-not-exist').get_json() == []


# ── /api/image ───────────────────────────────────────────────

def test_image_requires_both_params(client):
    assert client.get('/api/image?title=Realme+5').status_code == 400
    assert client.get('/api/image?filename=a.jpg').status_code == 400


def test_image_missing_returns_404(client, app_mod):
    resp = client.get('/api/image?title=Realme+5&filename=nope.jpg')
    assert resp.status_code == 404


def test_image_served_from_the_legacy_flat_layout(client, app_mod):
    flat = os.path.join(app_mod.DATA_DIR, 'images', 'Realme_5')
    os.makedirs(flat)
    with open(os.path.join(flat, 'a.jpg'), 'wb') as f:
        f.write(b'\xff\xd8flat')

    resp = client.get('/api/image?title=Realme+5&filename=a.jpg')
    assert resp.status_code == 200
    assert resp.data == b'\xff\xd8flat'


def test_image_resolved_via_the_record_image_dir_index(client, app_mod):
    img_dir = os.path.join(app_mod.DATA_DIR, 'structured', 'Smartphones', 'Realme_5', 'images')
    os.makedirs(img_dir)
    with open(os.path.join(img_dir, 'a.jpg'), 'wb') as f:
        f.write(b'\xff\xd8structured')
    append(app_mod, product('https://x.test/product/realme-5/', 'Realme 5',
                            image_dir='structured/Smartphones/Realme_5/images'))

    resp = client.get('/api/image?title=Realme+5&filename=a.jpg')
    assert resp.status_code == 200
    assert resp.data == b'\xff\xd8structured'


def test_image_index_is_rebuilt_when_new_products_arrive(client, app_mod):
    """The index is keyed on product count; a new product must invalidate it."""
    append(app_mod, product('https://x.test/product/a/', 'A', image_dir='structured/A/images'))
    client.get('/api/image?title=A&filename=a.jpg')  # primes the index

    img_dir = os.path.join(app_mod.DATA_DIR, 'structured', 'B', 'images')
    os.makedirs(img_dir)
    with open(os.path.join(img_dir, 'b.jpg'), 'wb') as f:
        f.write(b'\xff\xd8b')
    append(app_mod, product('https://x.test/product/b/', 'B', image_dir='structured/B/images'))

    assert client.get('/api/image?title=B&filename=b.jpg').status_code == 200


def test_image_falls_back_to_a_filesystem_walk_for_legacy_records(client, app_mod):
    """Records scraped before the JSONL rework carry no image_dir."""
    img_dir = os.path.join(app_mod.DATA_DIR, 'structured', 'Audio', 'Buds', 'Realme_Buds', 'images')
    os.makedirs(img_dir)
    with open(os.path.join(img_dir, 'buds.jpg'), 'wb') as f:
        f.write(b'\xff\xd8buds')
    append(app_mod, product('https://x.test/product/buds/', 'Realme Buds'))
    app_mod._CACHE['image_index_key'] = -1
    app_mod._CACHE['walk_index_time'] = 0.0

    resp = client.get('/api/image?title=Realme+Buds&filename=buds.jpg')
    assert resp.status_code == 200
    assert resp.data == b'\xff\xd8buds'


# ── /api/scrape guardrails ───────────────────────────────────

def test_scrape_refuses_to_start_a_second_run(client, app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, '_scraper_pid', lambda: 4242)
    resp = client.post('/api/scrape', json={})
    assert resp.status_code == 400
    assert 'already running' in resp.get_json()['message']


def test_scrape_builds_the_expected_command_line(client, app_mod, monkeypatch):
    """No subprocess is spawned — we assert on the argv the endpoint would run."""
    captured = {}

    class FakeProc:
        pid = 1234

        def wait(self):
            return 0

    def fake_popen(cmd, cwd=None):
        captured['cmd'] = cmd
        captured['cwd'] = cwd
        return FakeProc()

    monkeypatch.setattr(app_mod.subprocess, 'Popen', fake_popen)

    resp = client.post('/api/scrape', json={
        'url': 'https://x.test/product/a/', 'limit': 5, 'workers': 8, 'force': True,
    })
    assert resp.status_code == 200
    assert resp.get_json()['pid'] == 1234

    cmd = captured['cmd']
    assert cmd[0] == app_mod.VENV_PYTHON
    assert cmd[1] == app_mod.MAIN_SCRIPT
    assert cmd[2:] == ['--target_url', 'https://x.test/product/a/',
                       '--limit', '5', '--force', '--workers', '8']

    with open(app_mod.PID_FILE) as f:
        assert f.read().strip() == '1234'


def test_scrape_defaults_to_twenty_workers_and_no_flags(client, app_mod, monkeypatch):
    captured = {}

    class FakeProc:
        pid = 7

        def wait(self):
            return 0

    monkeypatch.setattr(app_mod.subprocess, 'Popen',
                        lambda cmd, cwd=None: (captured.update(cmd=cmd), FakeProc())[1])

    client.post('/api/scrape', json={})
    assert captured['cmd'][2:] == ['--workers', '20']


def test_scrape_reports_a_launch_failure_as_500(client, app_mod, monkeypatch):
    def boom(cmd, cwd=None):
        raise OSError('no such interpreter')

    monkeypatch.setattr(app_mod.subprocess, 'Popen', boom)
    resp = client.post('/api/scrape', json={})
    assert resp.status_code == 500
    assert 'no such interpreter' in resp.get_json()['message']
