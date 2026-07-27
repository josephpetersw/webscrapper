import re
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
text = "hello\x1eworld"
print(repr(text))
print(repr(re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', text)))
print(repr(ILLEGAL_CHARACTERS_RE.sub('', text)))
