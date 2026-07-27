import json
with open('data/products.json') as f:
    data = json.load(f)
if data:
    print(data[0].keys())
