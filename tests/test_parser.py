"""Parser is the highest-risk pure code in the project: everything downstream
(structured folders, exports, the dashboard) inherits whatever it decides."""
import json

import pytest

from scraper import extractors as ex
from scraper.parser import Parser, dedupe_image_variants


def page(body):
    return f'<html><body>{body}</body></html>'


PRODUCT_HTML = page('''
  <div class="breadcrumbs-container">
    <a href="/">Home</a> / <a href="/c/smartphones/">Smartphones</a> / <a href="/c/realme/">Realme Phones</a>
  </div>
  <h1 class="product_title">Realme 5 (128GB + 4GB RAM)</h1>
  <div class="woocommerce-product-details__short-description">
    <p>Realme 5 specs</p>
  </div>
  <div class="price"><span class="woocommerce-Price-amount"><bdi>KSh&nbsp;18,999</bdi></span></div>
  <div class="woocommerce-product-gallery__wrapper">
    <img src="data:image/gif;base64,R0lGOD" data-src="https://cdn.test/realme-5.jpg?ver=2"/>
    <img src="https://cdn.test/realme-5-300x300.jpg"/>
  </div>
  <div id="tab-description"><h2>Overview</h2><p>Long copy</p></div>
''')


# ── parse_sitemap ────────────────────────────────────────────

def test_parse_sitemap_extracts_locs():
    xml = '''<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://x.test/product/a/</loc></url>
      <url><loc>https://x.test/product/b/</loc></url>
    </urlset>'''
    assert Parser.parse_sitemap(xml) == [
        'https://x.test/product/a/',
        'https://x.test/product/b/',
    ]


@pytest.mark.parametrize('empty', [None, '', b''])
def test_parse_sitemap_handles_empty_input(empty):
    assert Parser.parse_sitemap(empty) == []


# ── dedupe_image_variants ───────────────────────────────────

def test_dedupe_prefers_the_unsuffixed_original():
    urls = [
        'https://cdn.test/phone-300x300.jpg',
        'https://cdn.test/phone.jpg',
        'https://cdn.test/phone-600x600.jpg',
    ]
    assert dedupe_image_variants(urls) == ['https://cdn.test/phone.jpg']


def test_dedupe_falls_back_to_largest_variant_when_no_original():
    urls = [
        'https://cdn.test/phone-150x150.jpg',
        'https://cdn.test/phone-800x800.jpg',
        'https://cdn.test/phone-300x300.jpg',
    ]
    assert dedupe_image_variants(urls) == ['https://cdn.test/phone-800x800.jpg']


def test_dedupe_never_invents_a_url_not_on_the_page():
    """Regression guard: collapsing must pick a *seen* URL, not a synthesised
    unsuffixed one that may 404 on the CDN."""
    urls = ['https://cdn.test/phone-300x300.jpg']
    assert dedupe_image_variants(urls) == ['https://cdn.test/phone-300x300.jpg']


def test_dedupe_keeps_genuinely_distinct_images():
    urls = ['https://cdn.test/front.jpg', 'https://cdn.test/back.jpg']
    assert sorted(dedupe_image_variants(urls)) == sorted(urls)


def test_dedupe_ignores_dimension_like_text_outside_the_suffix_position():
    urls = ['https://cdn.test/1920x1080-wallpaper.png']
    assert dedupe_image_variants(urls) == urls


# ── jsonld_product ─────────────────────────────────

def ld_page(payload):
    return page(f'<script type="application/ld+json">{json.dumps(payload)}</script>')


def soup_of(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'lxml')


def test_json_ld_found_in_graph():
    html = ld_page({'@graph': [{'@type': 'WebPage'}, {'@type': 'Product', 'sku': 'SKU1'}]})
    assert ex.jsonld_product(ex.jsonld_nodes(soup_of(html)))['sku'] == 'SKU1'


def test_json_ld_found_in_top_level_list():
    html = ld_page([{'@type': 'Organization'}, {'@type': 'Product', 'sku': 'SKU2'}])
    assert ex.jsonld_product(ex.jsonld_nodes(soup_of(html)))['sku'] == 'SKU2'


def test_json_ld_type_may_be_a_list():
    html = ld_page({'@type': ['Product', 'Thing'], 'sku': 'SKU3'})
    assert ex.jsonld_product(ex.jsonld_nodes(soup_of(html)))['sku'] == 'SKU3'


def test_json_ld_malformed_block_does_not_hide_a_later_valid_one():
    html = page(
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">{"@type": "Product", "sku": "SKU4"}</script>'
    )
    assert ex.jsonld_product(ex.jsonld_nodes(soup_of(html)))['sku'] == 'SKU4'


def test_json_ld_absent_returns_none():
    assert ex.jsonld_product(ex.jsonld_nodes(soup_of(page('<p>nothing</p>')))) is None


# ── parse_product ────────────────────────────────────────────

def test_parse_product_returns_none_for_empty_html():
    assert Parser.parse_product('', 'https://x.test/p/') is None
    assert Parser.parse_product(None, 'https://x.test/p/') is None


def test_parse_product_core_fields():
    data = Parser.parse_product(PRODUCT_HTML, 'https://x.test/product/realme-5/')

    assert data['url'] == 'https://x.test/product/realme-5/'
    assert data['title'] == 'Realme 5 (128GB + 4GB RAM)'
    assert 'Realme 5 specs' in data['short_description']
    assert 'Long copy' in data['long_description']
    assert '18,999' in data['price']


def test_parse_product_drops_home_from_breadcrumbs():
    data = Parser.parse_product(PRODUCT_HTML, 'https://x.test/p/')
    assert data['categories'] == ['Smartphones', 'Realme Phones']


def test_parse_product_brand_heuristic_strips_category_noise():
    data = Parser.parse_product(PRODUCT_HTML, 'https://x.test/p/')
    assert data['brand'] == 'Realme'


def test_parse_product_brand_falls_back_to_first_title_word():
    html = page('<h1 class="product_title">Nokia 3310 in Kenya</h1>')
    data = Parser.parse_product(html, 'https://x.test/p/')
    assert data['categories'] == []
    assert data['brand'] == 'Nokia'


def test_parse_product_brand_is_unknown_without_title_or_categories():
    data = Parser.parse_product(page('<p>empty page</p>'), 'https://x.test/p/')
    assert data['brand'] == 'Unknown'


def test_parse_product_prefers_lazy_src_and_strips_query_params():
    data = Parser.parse_product(PRODUCT_HTML, 'https://x.test/p/')
    # data-src wins over the data: placeholder, ?ver=2 is stripped, and the
    # -300x300 resize collapses onto the original.
    assert data['images'] == ['https://cdn.test/realme-5.jpg']


def test_parse_product_skips_inline_data_uris_entirely():
    html = page('''<div class="woocommerce-product-gallery__wrapper">
        <img src="data:image/gif;base64,R0lGOD"/>
    </div>''')
    assert Parser.parse_product(html, 'https://x.test/p/')['images'] == []


def test_parse_product_json_ld_overrides_brand_and_adds_numeric_price():
    html = PRODUCT_HTML + json_ld_block({
        '@type': 'Product',
        'name': 'Realme 5',
        'sku': 'RM5-128',
        'brand': {'@type': 'Brand', 'name': 'realme'},
        'offers': {'@type': 'Offer', 'price': '18999', 'priceCurrency': 'KES'},
    })
    data = Parser.parse_product(html, 'https://x.test/p/')

    assert data['brand'] == 'realme'          # authoritative, beats the heuristic
    assert data['sku'] == 'RM5-128'
    assert data['price_value'] == 18999.0
    assert data['currency'] == 'KES'
    # Rendered price comes from the markup, with the non-breaking space the
    # theme emitted normalised to a plain one.
    assert data['price'] == 'KSh 18,999'
    assert data['title'] == 'Realme 5 (128GB + 4GB RAM)'  # h1 wins over ld name


def test_parse_product_json_ld_offer_list_and_low_price():
    html = PRODUCT_HTML + json_ld_block({
        '@type': 'Product',
        'offers': [{'@type': 'AggregateOffer', 'lowPrice': 15500, 'priceCurrency': 'KES'}],
    })
    data = Parser.parse_product(html, 'https://x.test/p/')
    assert data['price_value'] == 15500.0
    assert data['currency'] == 'KES'


def test_parse_product_json_ld_supplies_title_when_markup_has_none():
    html = page('<p>no h1 here</p>') + json_ld_block({
        '@type': 'Product', 'name': '  Realme C21  ',
    })
    assert Parser.parse_product(html, 'https://x.test/p/')['title'] == 'Realme C21'


def test_parse_product_string_brand_in_json_ld():
    html = PRODUCT_HTML + json_ld_block({'@type': 'Product', 'brand': 'Xiaomi'})
    assert Parser.parse_product(html, 'https://x.test/p/')['brand'] == 'Xiaomi'


def test_optional_keys_are_present_but_empty_when_json_ld_is_absent():
    """Originally asserted these keys were absent. scraper/schema.py now
    guarantees every record carries every field, which is what lets a
    products.json written by an older version still export — so the contract
    is 'present and empty', not 'missing'."""
    data = Parser.parse_product(PRODUCT_HTML, 'https://x.test/p/')
    assert data['sku'] == ''
    assert data['availability'] == ''
    assert data['in_stock'] is None
    # Currency and amount are still recovered, from the rendered price rather
    # than from structured data — the layers are independent per field.
    assert data['currency'] == 'KES'
    assert data['price_value'] == 18999.0


def json_ld_block(payload):
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


# ── regressions found on a live WooCommerce store ────────────
# All three came from one page: an SEO plugin publishing the page title as the
# JSON-LD product name, a two-entry JSON-LD breadcrumb, and a category list
# being read as a brand.

SEO_HTML = page('''
  <nav class="woocommerce-breadcrumb">
    <a href="/">Home</a> / <a href="/c/poultry/">Poultry</a>
    / <a href="/c/incubators/">Incubators</a> / 192 Eggs AC/DC Poultry Incubator
  </nav>
  <h1 class="product_title">192 Eggs AC/DC Poultry Incubator</h1>
  <div class="product_meta">
    <span class="posted_in">Categories: <a href="/c/incubators/">Incubators</a>,
      <a href="/c/poultry/">Poultry</a></span>
  </div>
''') + json_ld_block({
    '@type': 'Product',
    'name': '192 Eggs AC/DC Poultry Incubator - Steps Farmcare Centre in Nairobi, Kenya.',
}) + json_ld_block({
    '@type': 'BreadcrumbList',
    'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'item': {'name': 'Home'}},
        {'@type': 'ListItem', 'position': 2,
         'item': {'name': '192 Eggs AC/DC Poultry Incubator'}},
    ],
})


def test_theme_product_title_beats_an_seo_json_ld_name():
    """WordPress SEO plugins publish '<product> - <store> in <city>' as the
    JSON-LD name. h1.product_title is the product name and nothing else."""
    data = Parser.parse_product(SEO_HTML, 'https://stepsfarmcare.com/product/x/')
    assert data['title'] == '192 Eggs AC/DC Poultry Incubator'
    assert data['extracted_by']['title'] == 'css:h1.product_title'


def test_json_ld_trail_of_only_home_and_product_falls_through_to_markup():
    """'Home > <product>' is non-empty but carries no category, so taking it at
    face value left every product on the store uncategorised."""
    data = Parser.parse_product(SEO_HTML, 'https://stepsfarmcare.com/product/x/')
    assert data['categories'] == ['Poultry', 'Incubators']
    assert data['extracted_by']['categories'].startswith('css:')


def test_woocommerce_posted_in_is_not_a_brand():
    """'.product_meta .posted_in a' is the category list. Reading it as a brand
    gave 136 of one store's 137 products a brand like 'Incubators'."""
    data = Parser.parse_product(SEO_HTML, 'https://stepsfarmcare.com/product/x/')
    assert data['brand'] != 'Incubators'


def test_product_type_category_is_not_promoted_to_a_brand():
    """The heuristic only fires when a product-type word was actually stripped:
    'Realme Phones' -> 'Realme'. 'Incubators' survives unchanged, so it is the
    product type, not a maker."""
    data = Parser.parse_product(SEO_HTML, 'https://stepsfarmcare.com/product/x/')
    # Title leads with a spec ('192'), so there is no honest brand here.
    assert data['brand'] == 'Unknown'


def test_spec_leading_title_does_not_become_a_numeric_brand():
    html = page('<h1 class="product_title">20000 mAh Villaon Powerbank VP391</h1>')
    assert Parser.parse_product(html, 'https://x.test/p/')['brand'] == 'Unknown'
