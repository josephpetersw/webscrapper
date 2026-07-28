"""Turning scraped text into paths the filesystem will actually accept.

Product titles, category names and image URLs all go straight into the
directory tree, and they are hostile input: arbitrary length, arbitrary
punctuation, arbitrary Unicode. Windows caps a full path at 260 characters by
default and rejects a set of characters outright, so unsanitised names silently
cost us images and whole products.

Sanitising each *segment* is not enough — the failure is on the **total** path.
A deeply-categorised product with a long title and a long image filename blows
the limit even when every individual piece is within its own cap:

    data/<site>/structured/<cat>/<sub>/<subsub>/<long product name>/images/<long file>.jpg

so this module budgets the whole path, shortening the parts that can afford it
and appending a short hash wherever it truncates, so two different names cannot
collapse onto one directory.

The logic lives here rather than in the scraper and the downloader separately,
because they have to agree: the downloader's idea of how much room is left
depends on the directory the scraper chose.
"""

import hashlib
import os
import re
from urllib.parse import unquote, urlparse

# Windows' default MAX_PATH. Kept as the budget on every platform: the data
# directory is routinely copied to or served from Windows machines, and a tree
# that only works on Linux is a trap rather than a feature.
MAX_PATH = 260
# Headroom for the '\\images\\' component plus a filename.
PATH_SAFETY_MARGIN = 12

MAX_SEGMENT_LEN = 60
# Shortest a folder name may be squeezed to: enough to keep its 6-char hash
# plus a little of the original, so distinct products never share a directory.
MIN_SEGMENT_LEN = 12
MAX_FILENAME_LEN = 100
MIN_FILENAME_LEN = 24

# Characters Windows refuses in a filename, plus control codes.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NON_PRINTABLE = re.compile(r'[^\x20-\x7e]')

# Names Windows reserves outright, at any extension.
_RESERVED_NAMES = {'con', 'prn', 'aux', 'nul', 'com0', 'com1', 'com2', 'com3',
                   'com4', 'com5', 'com6', 'com7', 'com8', 'com9', 'lpt0',
                   'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7',
                   'lpt8', 'lpt9'}


def _short_hash(value, length=6):
    return hashlib.sha1((value or '').encode('utf-8', 'replace')).hexdigest()[:length]


def safe_path_segment(value, max_len=MAX_SEGMENT_LEN):
    """Filesystem-safe, length-capped folder name.

    Truncation appends a hash of the original, so two long titles sharing a
    prefix ('Samsung Galaxy A06 4GB 128GB Black' / '... 8GB 256GB Blue') do not
    end up writing into the same directory and overwriting each other.
    """
    original = value or ''
    cleaned = re.sub(r'_+', '_', re.sub(r'[^a-zA-Z0-9]', '_', original)).strip('_')
    if not cleaned:
        return 'unnamed'
    if len(cleaned) > max_len:
        keep = max(1, max_len - 7)
        cleaned = f'{cleaned[:keep].strip("_")}_{_short_hash(original)}'
    if cleaned.lower() in _RESERVED_NAMES:
        cleaned += '_'
    return cleaned or 'unnamed'


def safe_filename(url, fallback='image', max_len=MAX_FILENAME_LEN):
    """Turn an image URL into a filename the filesystem will accept.

    Source URLs routinely contain percent-encoding, non-ASCII characters and
    query strings, any of which make open() fail on Windows - which silently
    cost us the image.
    """
    raw = unquote(urlparse(url or '').path.split('/')[-1])
    name = _NON_PRINTABLE.sub('_', _ILLEGAL_FILENAME_CHARS.sub('_', raw)).strip('. ')
    if not name:
        name = fallback
    name = _truncate_filename(name, max_len, seed=url)
    if name.rsplit('.', 1)[0].lower() in _RESERVED_NAMES:
        name = '_' + name
    return name


def _truncate_filename(name, max_len, seed=''):
    """Shorten a filename to max_len, keeping its extension and staying unique."""
    if max_len <= 0 or len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition('.')
    if dot and 0 < len(ext) <= 5:
        suffix = f'_{_short_hash(seed or name)}.{ext}'
        keep = max(1, max_len - len(suffix))
        return stem[:keep] + suffix
    suffix = f'_{_short_hash(seed or name)}'
    keep = max(1, max_len - len(suffix))
    return name[:keep] + suffix


def _full_len(path):
    """Length of the path as the OS will see it."""
    try:
        return len(os.path.abspath(path))
    except Exception:
        return len(path)


def build_product_dir(structured_root, categories, title, url='',
                      max_path=MAX_PATH, reserve=MAX_FILENAME_LEN):
    """Directory for one product, guaranteed to leave room for its images.

    ``reserve`` is the space kept free for the trailing ``images/<filename>``.
    When the natural path is too long the category trail is shortened from the
    deepest level first — losing 'Switches' hurts far less than losing the
    product's own identity — and only then is the product folder itself
    squeezed, always with a hash so distinct products stay distinct.
    """
    segments = [safe_path_segment(c, 40) for c in (categories or []) if c]
    name = safe_path_segment(title or url.rstrip('/').split('/')[-1])
    budget = max_path - reserve - len('images') - PATH_SAFETY_MARGIN

    while True:
        parts = [structured_root] + (segments or ['Uncategorized']) + [name]
        candidate = os.path.join(*parts)
        if _full_len(candidate) <= budget or not segments:
            break
        segments.pop()  # drop the deepest category and re-measure

    if _full_len(candidate) > budget:
        # Categories are gone; shorten the product folder to whatever is left.
        # The floor keeps the name long enough to still carry its hash, so two
        # products cannot collapse onto one directory even in the worst case.
        base = os.path.join(structured_root, 'Uncategorized')
        room = max(budget - _full_len(base) - 1, MIN_SEGMENT_LEN)
        name = safe_path_segment(title or url, max_len=room)
        candidate = os.path.join(base, name)

    return candidate


def image_path(save_dir, url, max_path=MAX_PATH):
    """Full path for one image, shortened to fit whatever room is left.

    Returns '' when even a minimal filename cannot fit, so the caller can skip
    the download rather than fail on open().
    """
    room = max_path - _full_len(save_dir) - 1 - PATH_SAFETY_MARGIN
    if room < MIN_FILENAME_LEN:
        return ''
    return os.path.join(save_dir, safe_filename(url, max_len=min(MAX_FILENAME_LEN, room)))
