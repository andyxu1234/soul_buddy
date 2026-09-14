import json

with open('raw.json', encoding='utf-8') as f:
    raw = f.read()

data = json.loads(raw)['data']
markdown = data['system']

with open('prompt.md', 'w', encoding='utf-8') as f:
    f.write(markdown)
