"""The one definition of what a product record is.

Extraction gained several fields (price as a number, currency, SKU, stock
state) plus a diagnostic map of which strategy produced each value. Those are
useful, but only if every consumer agrees on them — and the exporters used to
each carry their own field list, with the XML one simply serialising whatever
keys happened to be on the record. Add a field to the parser and the XML export
would silently start emitting a Python dict repr inside a tag.

So the schema lives here, once, and both the scraper and the exporters import
it. Adding a field is a one-line change in this file; forgetting to update an
exporter is no longer possible.
"""

# Fields that have always been exported. Order is the column order in CSV and
# Excel, so it is deliberately stable — existing downstream sheets depend on it.
CORE_FIELDS = ['title', 'brand', 'price', 'url', 'categories', 'images',
               'short_description', 'long_description']

# Added alongside structured-data extraction. Appended after the core fields so
# an existing CSV consumer reading by position is unaffected.
DETAIL_FIELDS = ['price_value', 'currency', 'sku', 'availability', 'in_stock']

# Kept in products.json for debugging but never exported: this records which
# extraction layer produced each field, which is noise in a product feed.
DIAGNOSTIC_FIELDS = ['extracted_by']

EXPORT_FIELDS = CORE_FIELDS + DETAIL_FIELDS
ALL_FIELDS = EXPORT_FIELDS + DIAGNOSTIC_FIELDS

# What an absent value looks like, per field. Lists must be fresh objects per
# record, hence the factory rather than a shared literal.
_LIST_FIELDS = {'categories', 'images'}
_NULLABLE_FIELDS = {'price_value', 'in_stock'}


def blank_record(url=''):
    """An empty record with every field present and correctly typed."""
    record = {field: '' for field in EXPORT_FIELDS}
    record['url'] = url
    for field in _LIST_FIELDS:
        record[field] = []
    for field in _NULLABLE_FIELDS:
        record[field] = None
    record['brand'] = 'Unknown'
    record['extracted_by'] = {}
    return record


def normalize(record):
    """Fill in anything missing and coerce types.

    Applied to every parsed record, so downstream code can rely on
    ``record['categories']`` being a list and ``record['title']`` being a
    string without guarding. Also lets records written by an older version of
    the scraper flow through the current exporters unchanged.
    """
    out = blank_record()
    out.update(record or {})

    for field in _LIST_FIELDS:
        value = out.get(field)
        if value is None:
            out[field] = []
        elif not isinstance(value, list):
            out[field] = [value]

    for field in EXPORT_FIELDS:
        if field in _LIST_FIELDS or field in _NULLABLE_FIELDS:
            continue
        value = out.get(field)
        out[field] = '' if value is None else value if isinstance(value, str) else str(value)

    if not isinstance(out.get('extracted_by'), dict):
        out['extracted_by'] = {}

    out['brand'] = out.get('brand') or 'Unknown'
    return out


def export_row(product, short_description=None, long_description=None):
    """A flat, string-valued row for CSV/Excel/XML.

    Lists are joined, ``None`` becomes an empty cell, and diagnostic fields are
    dropped. Descriptions can be passed in pre-cleaned, since whether HTML is
    stripped is the caller's choice.
    """
    row = {}
    for field in EXPORT_FIELDS:
        value = product.get(field)
        if field == 'categories':
            row[field] = ' > '.join(str(v) for v in (value or []))
        elif field == 'images':
            row[field] = ' | '.join(str(v) for v in (value or []))
        elif value is None:
            row[field] = ''
        elif isinstance(value, bool):
            row[field] = 'yes' if value else 'no'
        else:
            row[field] = str(value)
    if short_description is not None:
        row['short_description'] = short_description
    if long_description is not None:
        row['long_description'] = long_description
    return row
