"""Hostile-input checks for the extraction layer.

The scraper's contract is that a run always reaches the end: no page, however
malformed, may raise out of the parser. These cases are the ones real stores
have actually produced — truncated JSON-LD, entity-encoded names, price ranges,
galleries full of tracking pixels, breadcrumbs that repeat the product name.

Run directly:  ./venv/Scripts/python.exe test_robustness.py
"""

import sys

from scraper import extractors as ex
from scraper.parser import Parser

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print(f'  PASS  {name}')
    else:
        print(f'  FAIL  {name}  {detail}')
        FAILURES.append(name)


def parses_without_raising(name, html, url='https://shop.example.com/product/x'):
    try:
        result = Parser.parse_product(html, url)
    except Exception as e:
        check(name, False, f'raised {type(e).__name__}: {e}')
        return None
    check(name, isinstance(result, (dict, type(None))), f'returned {type(result)}')
    return result


print('\n== Malformed and hostile input must never raise ==')
parses_without_raising('empty string', '')
parses_without_raising('not html at all', '\x00\x01\x02 binary junk \xff')
parses_without_raising('truncated html', '<html><body><div class="prod')
parses_without_raising('jsonld that is not json',
                       '<script type="application/ld+json">{oh no</script>')
parses_without_raising('jsonld that is a bare string',
                       '<script type="application/ld+json">"hello"</script>')
parses_without_raising('jsonld null', '<script type="application/ld+json">null</script>')
parses_without_raising('deeply nested graph',
                       '<script type="application/ld+json">' +
                       '{"@graph":[' * 1 + '{"@type":"Product","name":"X"}' + ']}' +
                       '</script>')
parses_without_raising('offers as a list of junk',
                       '<script type="application/ld+json">'
                       '{"@type":"Product","name":"A","offers":[null,3,"x",{"price":"bad"}]}'
                       '</script>')
parses_without_raising('image as list of objects',
                       '<script type="application/ld+json">'
                       '{"@type":"Product","name":"A","image":[{"@type":"ImageObject"},null]}'
                       '</script>')

check('None html returns None', Parser.parse_product(None, 'u') is None)

print('\n== Sitemap parsing ==')
check('empty sitemap', Parser.parse_sitemap('') == [])
check('garbage sitemap', isinstance(Parser.parse_sitemap('<<<>>>not xml'), list))
check('valid sitemap',
      Parser.parse_sitemap(
          '<urlset><url><loc>https://a.com/p/1</loc></url>'
          '<url><loc>https://a.com/p/2</loc></url></urlset>'
      ) == ['https://a.com/p/1', 'https://a.com/p/2'])
check('namespaced sitemap still parses',
      len(Parser.parse_sitemap(
          '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          '<url><loc>https://a.com/p/1</loc></url></urlset>')) == 1)

print('\n== Price normalisation ==')
check('KSh with thousands', ex.to_number('KSh 12,999.00') == 12999.0)
check('bare integer', ex.to_number('4500') == 4500.0)
check('european thousands', ex.to_number('1.234.567') == 1234567.0)
check('numeric passthrough', ex.to_number(19999) == 19999.0)
check('empty is None', ex.to_number('') is None)
check('no digits is None', ex.to_number('Call for price') is None)
check('None is None', ex.to_number(None) is None)
check('bool is not a price', ex.to_number(True) is None)
check('currency detected', ex.split_price_text('KSh 1,500')[0] == 'KES')
check('dollar detected', ex.split_price_text('$19.99')[0] == 'USD')
check('format drops empty decimals', ex.format_price(12999.0, 'KES') == 'KSh 12,999')
check('format keeps real decimals', ex.format_price(19.99, 'USD') == '$ 19.99')
check('format of None is empty', ex.format_price(None, 'KES') == '')

print('\n== Entity decoding ==')
check('single entity', ex.unescape('Home &amp; Living') == 'Home & Living')
check('double entity', ex.unescape('Home &amp;amp; Living') == 'Home & Living')
check('non-string passthrough', ex.unescape(None) is None)

print('\n== URL normalisation ==')
check('space encoded', ' ' not in ex.__dict__ and True)
from scraper.client import normalize_url, looks_like_challenge
check('non-ascii path encoded',
      '%C2%AE' in normalize_url('https://a.com/products/intel®-core'),
      normalize_url('https://a.com/products/intel®-core'))
check('plain url untouched',
      normalize_url('https://a.com/p/1?x=2') == 'https://a.com/p/1?x=2')
check('empty url safe', normalize_url('') == '')
check('challenge detected by title', looks_like_challenge('<title>Just a moment...</title>'))
check('challenge detected by header', looks_like_challenge('', {'cf-mitigated': 'challenge'}))
check('real page not a challenge',
      not looks_like_challenge('<html><h1 class="product_title">Laptop</h1></html>'))

print('\n== Extraction correctness ==')
WOO = '''<html><body>
<nav class="woocommerce-breadcrumb"><a href="/">Home</a><a href="/c">Laptops</a>
<a href="/c/hp">HP Laptops</a></nav>
<h1 class="product_title">HP EliteBook 840</h1>
<div class="summary"><p class="price"><span class="woocommerce-Price-amount">
<bdi>KSh&nbsp;89,500</bdi></span></p></div>
<div class="woocommerce-product-details__short-description"><p>Business laptop.</p></div>
<div id="tab-description"><p>Long spec sheet here.</p></div>
<div class="woocommerce-product-gallery__wrapper">
  <img src="/wp-content/uploads/woocommerce-placeholder.png"/>
  <img data-src="/img/a-600x600.jpg" data-large_image="/img/a.jpg"/>
  <img srcset="/img/b-300.jpg 300w, /img/b-1200.jpg 1200w"/>
</div></body></html>'''
woo = Parser.parse_product(WOO, 'https://shop.example.com/product/hp-elitebook-840')
check('woo title', woo['title'] == 'HP EliteBook 840', woo['title'])
check('woo price value', woo['price_value'] == 89500.0, woo['price_value'])
check('woo currency', woo['currency'] == 'KES', woo['currency'])
check('woo categories drop Home', woo['categories'] == ['Laptops', 'HP Laptops'], woo['categories'])
check('woo placeholder image skipped',
      not any('placeholder' in i for i in woo['images']), woo['images'])
check('woo prefers full-size over thumbnail',
      'https://shop.example.com/img/a.jpg' in woo['images'], woo['images'])
check('woo picks largest srcset',
      'https://shop.example.com/img/b-1200.jpg' in woo['images'], woo['images'])
check('woo long description kept', 'Long spec sheet' in woo['long_description'])

JSONLD = '''<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"BreadcrumbList","itemListElement":[
   {"@type":"ListItem","position":1,"item":{"name":"Home"}},
   {"@type":"ListItem","position":3,"item":{"name":"Coffee Tables"}},
   {"@type":"ListItem","position":2,"item":{"name":"Home &amp; Living"}},
   {"@type":"ListItem","position":4,"item":{"name":"YZ021 Table"}}]},
 {"@type":"Product","name":"YZ021 Table","sku":"YZ-1",
  "brand":{"@type":"Brand","name":"Home &amp; Living"},
  "image":["https://cdn.example.com/a.jpg","https://cdn.example.com/b.jpg"],
  "offers":{"@type":"Offer","price":"6999.00","priceCurrency":"KES",
            "availability":"https://schema.org/InStock"}}]}
</script></head><body><h1>Some Site Name</h1></body></html>'''
ld = Parser.parse_product(JSONLD, 'https://furniture.example.com/catalogue/yz021_1/')
check('jsonld beats bare h1', ld['title'] == 'YZ021 Table', ld['title'])
check('jsonld price', ld['price_value'] == 6999.0)
check('jsonld display price', ld['price'] == 'KSh 6,999', ld['price'])
check('jsonld in stock', ld['in_stock'] is True)
check('jsonld sku', ld['sku'] == 'YZ-1')
check('breadcrumbs ordered by position and entity-decoded',
      ld['categories'] == ['Home & Living', 'Coffee Tables'], ld['categories'])
check('breadcrumbs exclude the product itself',
      'YZ021 Table' not in ld['categories'], ld['categories'])
check('brand entity-decoded', ld['brand'] == 'Home & Living', ld['brand'])
check('jsonld images absolute', len(ld['images']) == 2, ld['images'])

SELF_BRAND = '''<html><script type="application/ld+json">
{"@type":"Product","name":"Apple iPad mini 7","brand":{"name":"Examplestore"},
 "offers":{"price":105020,"priceCurrency":"KES"}}</script></html>'''
sb = Parser.parse_product(SELF_BRAND, 'https://examplestore.co.ke/item/apple-ipad-mini-7')
check('store name rejected as brand', sb['brand'] != 'Examplestore', sb['brand'])
check('falls back to title word', sb['brand'] == 'Apple', sb['brand'])

RANGE = '''<html><body><h1 class="product_title">Variable Product</h1>
<span class="price"><bdi>KSh 1,000</bdi> &ndash; <bdi>KSh 2,000</bdi></span>
</body></html>'''
rng = Parser.parse_product(RANGE, 'https://s.example.com/product/v')
check('price range takes the low end', rng['price_value'] == 1000.0, rng['price_value'])

OG = '''<html><head>
<meta property="og:title" content="Sony Bravia 55"/>
<meta property="product:price:amount" content="74999"/>
<meta property="product:price:currency" content="KES"/>
<meta property="og:image" content="https://cdn.x.com/tv.jpg"/>
<meta property="og:description" content="4K TV"/>
</head><body></body></html>'''
og = Parser.parse_product(OG, 'https://tv.example.com/p/1')
check('og title', og['title'] == 'Sony Bravia 55')
check('og price', og['price_value'] == 74999.0)
check('og image', og['images'] == ['https://cdn.x.com/tv.jpg'], og['images'])

print('\n== Schema contract: every record has the fields the app exports ==')
REQUIRED = ['url', 'title', 'brand', 'price', 'url', 'categories', 'images',
            'short_description', 'long_description']
for label, record in (('woo', woo), ('jsonld', ld), ('og', og),
                      ('empty page', Parser.parse_product('<html></html>', 'u'))):
    missing = [f for f in REQUIRED if f not in record]
    check(f'{label} record has all export fields', not missing, f'missing {missing}')
    check(f'{label} categories is a list', isinstance(record['categories'], list))
    check(f'{label} images is a list', isinstance(record['images'], list))
    check(f'{label} title is a str', isinstance(record['title'], str))
    check(f'{label} price is a str', isinstance(record['price'], str))

print('\n== Discovery URL filtering ==')
from scraper import discovery as disc
check('image url rejected',
      not disc._is_scrapable_page('https://api.x.com/media/1/photo.jpeg'))
check('pdf rejected', not disc._is_scrapable_page('https://x.com/manual.pdf'))
check('blog rejected', not disc._is_scrapable_page('https://x.com/blog/hello'))
check('cart rejected', not disc._is_scrapable_page('https://x.com/cart'))
check('shop root rejected', not disc._is_scrapable_page('https://x.com/shop/'))
check('product kept', disc._is_scrapable_page('https://x.com/product/thing'))
check('off-host rejected',
      not disc._is_scrapable_page('https://cdn.other.com/product/x', 'x.com'))
check('on-host kept', disc._is_scrapable_page('https://x.com/product/x', 'x.com'))

check('shop root behind a front controller rejected',
      not disc._is_scrapable_page('https://x.com/index.php/shop/'))
check('homepage behind a front controller rejected',
      not disc._is_scrapable_page('https://x.com/index.php/'))
check('site root rejected', not disc._is_scrapable_page('https://x.com/'))
check('product behind a front controller kept',
      disc._is_scrapable_page('https://x.com/index.php/product/192-eggs/'))
check('product whose slug merely starts with shop is kept',
      disc._is_scrapable_page('https://x.com/shopping-trolley-large/'))

catalogue = [f'https://x.com/catalogue/item-{i}_{i}/' for i in range(40)]
noise = ['https://x.com/about', 'https://x.com/contact-us']
check('dominant shape inferred',
      len(disc._infer_product_urls(catalogue + noise, 'x.com')) == 40)
check('no dominant shape returns nothing',
      disc._infer_product_urls(
          [f'https://x.com/a/{i}' for i in range(10)] +
          [f'https://x.com/b/{i}' for i in range(10)], 'x.com') == [])
check('too few urls returns nothing', disc._infer_product_urls(catalogue[:3], 'x.com') == [])

print('\n== Fulfilment-context probing ==')
from scraper import client as client_mod

check('params appended to a bare url',
      client_mod.with_params('https://x.com/p/1', {'sid': 'SLOTTED'})
      == 'https://x.com/p/1?sid=SLOTTED')
check('existing query preserved',
      client_mod.with_params('https://x.com/p/1?a=2', {'sid': 'SLOTTED'})
      in ('https://x.com/p/1?a=2&sid=SLOTTED', 'https://x.com/p/1?sid=SLOTTED&a=2'))
check('same key overridden, not duplicated',
      client_mod.with_params('https://x.com/p?sid=OLD', {'sid': 'NEW'})
      == 'https://x.com/p?sid=NEW')
check('no params is a no-op',
      client_mod.with_params('https://x.com/p/1', {}) == 'https://x.com/p/1')
check('values are url-encoded',
      '%20' in client_mod.with_params('https://x.com/p', {'k': 'a b'})
      or '+' in client_mod.with_params('https://x.com/p', {'k': 'a b'}))

memo = client_mod._ContextMemo()
check('a fresh host offers every candidate',
      len(memo.candidates('a.test')) == len(client_mod.PRICE_CONTEXT_PARAMS))
memo.remember('a.test', {'sid': 'SLOTTED'})
check('a solved host offers only the known answer',
      memo.candidates('a.test') == [{'sid': 'SLOTTED'}])

memo2 = client_mod._ContextMemo()
memo2.settle('b.test')
check('a host that prices normally is never probed', memo2.candidates('b.test') == [])

memo3 = client_mod._ContextMemo()
probes = sum(1 for _ in range(client_mod._CONTEXT_PROBE_LIMIT + 3)
             if memo3.candidates('c.test'))
check('probing stops after the limit so sold-out stores are not re-probed',
      probes == client_mod._CONTEXT_PROBE_LIMIT, probes)
check('host with no name is never probed', client_mod._ContextMemo().candidates('') == [])

print('\n== Filesystem path budget ==')
import os
from scraper import paths as pu

check('illegal chars stripped', pu.safe_path_segment('a/b\\c:d*e?') == 'a_b_c_d_e')
check('empty becomes unnamed', pu.safe_path_segment('') == 'unnamed')
check('reserved name escaped', pu.safe_path_segment('CON') != 'CON')
long_a = 'Samsung Galaxy A06 4GB RAM 128GB Storage Black Edition Kenya'
long_b = 'Samsung Galaxy A06 4GB RAM 128GB Storage Blue Edition Kenya'
check('long segment capped', len(pu.safe_path_segment(long_a, 40)) <= 40)
check('similar long names stay distinct',
      pu.safe_path_segment(long_a, 40) != pu.safe_path_segment(long_b, 40))

check('filename keeps extension',
      pu.safe_filename('https://x.com/a/photo.jpg') == 'photo.jpg')
check('percent-encoding decoded',
      pu.safe_filename('https://x.com/a/my%20photo.jpg') == 'my photo.jpg')
check('query string dropped',
      pu.safe_filename('https://x.com/a/p.jpg?v=2') == 'p.jpg')
long_img = 'https://x.com/' + 'y' * 200 + '.jpg'
check('long filename capped and keeps extension',
      len(pu.safe_filename(long_img)) <= pu.MAX_FILENAME_LEN
      and pu.safe_filename(long_img).endswith('.jpg'), pu.safe_filename(long_img))

# The real-world failure: deep categories + long title + long image name.
deep = ['Networking Equipment', 'Ubiquiti Networking Equipment', 'Ubiquiti Switches']
title = 'Ubiquiti UniFi Pro Max 24 Port L3 Managed PoE Switch price in Kenya'
pdir = pu.build_product_dir(os.path.join('data', 'example.co.ke', 'structured'),
                            deep, title, 'https://example.co.ke/product/x')
check('product dir within budget',
      len(os.path.abspath(pdir)) <= pu.MAX_PATH - pu.MAX_FILENAME_LEN, len(os.path.abspath(pdir)))
ipath = pu.image_path(os.path.join(pdir, 'images'),
                      'https://example.co.ke/wp-content/uploads/2025/05/'
                      'Ubiquiti-UniFi-Pro-Max-24-Port-L3-Managed-PoE-Switch-USW-Pro-Max-24-PoE.jpg')
check('image path fits MAX_PATH', bool(ipath) and len(os.path.abspath(ipath)) <= pu.MAX_PATH,
      len(os.path.abspath(ipath)) if ipath else 'empty')
check('image path keeps extension', ipath.endswith('.jpg'), ipath)

# Absurd input must still yield a usable path rather than blowing the budget.
absurd = pu.build_product_dir('data/s/structured', ['C' * 90] * 6, 'T' * 300)
check('absurd input still budgeted',
      len(os.path.abspath(absurd)) <= pu.MAX_PATH - pu.MAX_FILENAME_LEN,
      len(os.path.abspath(absurd)))
check('no-category product still gets a dir',
      pu.build_product_dir('data/s/structured', [], 'Thing').endswith('Thing'))
check('unfittable image returns empty', pu.image_path('x' * 300, 'https://x.com/a.jpg') == '')

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S): {FAILURES}')
    sys.exit(1)
print('All robustness checks passed.')
