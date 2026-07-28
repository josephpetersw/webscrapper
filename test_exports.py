"""Every exporter must handle a current record, a legacy record, and a junk one.

The failure this guards against: the parser gains a field, and an exporter that
carried its own field list either drops it or — as the XML builder used to —
serialises a dict as a Python repr inside a tag. All four builders now read
scraper/schema.py, so this checks they actually agree.

Run directly:  ./venv/Scripts/python.exe test_exports.py
"""

import io
import json
import sys
import xml.etree.ElementTree as ET

import app
from scraper import schema

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print(f'  PASS  {name}')
    else:
        print(f'  FAIL  {name}  {detail}')
        FAILURES.append(name)


CURRENT = schema.normalize({
    'url': 'https://s.example.com/product/a', 'title': 'Laptop & Case',
    'brand': 'HP', 'price': 'KSh 89,500', 'price_value': 89500.0, 'currency': 'KES',
    'sku': 'HP-840', 'availability': 'InStock', 'in_stock': True,
    'categories': ['Computing', 'Laptops'],
    'images': ['https://s.example.com/a.jpg', 'https://s.example.com/b.jpg'],
    'short_description': '<p>Fast &amp; light</p>',
    'long_description': '<div>Full specs <br> here</div>',
    'extracted_by': {'title': 'jsonld', 'price': 'jsonld'},
})

# Exactly the shape products.json had before this change — no new fields at all.
LEGACY = {
    'url': 'https://s.example.com/product/b', 'title': 'Old Record',
    'brand': 'Dell', 'price': 'KSh 10,000',
    'categories': ['Computing'], 'images': ['https://s.example.com/c.jpg'],
    'short_description': 'plain text', 'long_description': '',
}

# Deliberately hostile: wrong types where lists and strings are expected.
JUNK = {'url': 'u', 'title': 'Junk', 'categories': None, 'images': None,
        'brand': None, 'price': None, 'price_value': None, 'in_stock': None,
        'short_description': None, 'long_description': None}

UNTITLED = {'url': 'u2', 'title': ''}

PRODUCTS = [CURRENT, LEGACY, JUNK, UNTITLED]

print('\n== schema.normalize ==')
n = schema.normalize(JUNK)
check('None list becomes []', n['categories'] == [] and n['images'] == [])
check('None brand becomes Unknown', n['brand'] == 'Unknown')
check('None price becomes empty string', n['price'] == '')
check('nullable stays None', n['price_value'] is None and n['in_stock'] is None)
check('every export field present', all(f in n for f in schema.EXPORT_FIELDS))
check('normalize does not mutate input', JUNK['categories'] is None)
blank = schema.blank_record('http://x')
check('blank record url set', blank['url'] == 'http://x')
check('blank lists are distinct objects',
      schema.blank_record()['images'] is not schema.blank_record()['images'])

print('\n== export_row ==')
row = schema.export_row(CURRENT)
check('categories joined', row['categories'] == 'Computing > Laptops')
check('images joined', row['images'] == 'https://s.example.com/a.jpg | https://s.example.com/b.jpg')
check('bool rendered readably', row['in_stock'] == 'yes', row['in_stock'])
check('numeric stringified', row['price_value'] == '89500.0')
check('no diagnostic fields leak',
      not any(f in row for f in schema.DIAGNOSTIC_FIELDS), list(row))
check('legacy record yields every column',
      set(schema.export_row(LEGACY)) == set(schema.EXPORT_FIELDS))
check('legacy missing fields are empty', schema.export_row(LEGACY)['sku'] == '')

print('\n== builders survive all record shapes ==')
for clean in (True, False):
    label = 'clean' if clean else 'raw'
    try:
        csv_out = app.build_csv(PRODUCTS, clean).decode('utf-8-sig')
        check(f'csv builds ({label})', True)
        header = csv_out.splitlines()[0]
        check(f'csv header matches schema ({label})',
              header.strip() == ','.join(schema.EXPORT_FIELDS), header)
        check(f'csv skips untitled rows ({label})', 'u2' not in csv_out)
        # Parsed, not line-counted: descriptions contain newlines, so a single
        # CSV record legitimately spans several physical lines.
        import csv as _csv
        parsed = list(_csv.reader(io.StringIO(csv_out)))
        check(f'csv row count ({label})', len(parsed) == 4, f'got {len(parsed)}')
        check(f'csv columns per row ({label})',
              all(len(r) == len(schema.EXPORT_FIELDS) for r in parsed),
              [len(r) for r in parsed])
    except Exception as e:
        check(f'csv builds ({label})', False, f'{type(e).__name__}: {e}')

    try:
        xml_out = app.build_xml(PRODUCTS, clean).decode('utf-8')
        root = ET.fromstring(xml_out)
        check(f'xml is well-formed ({label})', True)
        check(f'xml skips untitled rows ({label})', len(root) == 3)
        tags = [c.tag for c in root[0]]
        check(f'xml tags match schema ({label})', tags == schema.EXPORT_FIELDS, tags)
        check(f'xml has no python dict repr ({label})',
              "{'" not in xml_out, 'dict leaked into XML')
        check(f'xml escapes ampersands ({label})',
              root[0].find('title').text == 'Laptop & Case')
    except ET.ParseError as e:
        check(f'xml is well-formed ({label})', False, str(e))
    except Exception as e:
        check(f'xml builds ({label})', False, f'{type(e).__name__}: {e}')

    try:
        data = json.loads(app.build_json(PRODUCTS, clean).decode('utf-8'))
        check(f'json builds ({label})', len(data) == 4)
    except Exception as e:
        check(f'json builds ({label})', False, f'{type(e).__name__}: {e}')

    try:
        blob = app.build_excel(PRODUCTS, clean)
        check(f'excel builds ({label})', len(blob) > 1000)
    except Exception as e:
        check(f'excel builds ({label})', False, f'{type(e).__name__}: {e}')

print('\n== clean vs raw descriptions ==')
csv_clean = app.build_csv([CURRENT], True).decode('utf-8-sig')
csv_raw = app.build_csv([CURRENT], False).decode('utf-8-sig')
check('clean strips tags', '<p>' not in csv_clean)
check('clean decodes entities', 'Fast & light' in csv_clean)
check('raw keeps tags', '<p>' in csv_raw or '<div>' in csv_raw)

print('\n== app-level field list is the schema, not a copy ==')
check('PRODUCT_FIELDS is schema.EXPORT_FIELDS',
      app.PRODUCT_FIELDS == schema.EXPORT_FIELDS)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILURE(S): {FAILURES}')
    sys.exit(1)
print('All export checks passed.')
