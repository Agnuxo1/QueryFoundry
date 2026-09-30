from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import urllib.request
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'upstream'
app = ROOT / 'app'
for path in source.rglob('*'):
    relative = path.relative_to(source)
    if '.git' in relative.parts or '__pycache__' in relative.parts:
        continue
    if path.is_file():
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
manifest = {str(p.relative_to(app)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in app.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
(ROOT / 'docs').mkdir(exist_ok=True)
(ROOT / 'docs/upstream-manifest.json').write_text(json.dumps({'repository': 'https://github.com/igorsiecz/kaggle_postgre_challenge',
    'commit': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
    'files': manifest}, indent=2), encoding='utf-8')

class Links(HTMLParser):
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            url = dict(attrs).get('href', '')
            if 'getfile' in url or ('windows' in url and 'binaries' in url):
                print(url)

if __name__ == '__main__':
    Links().feed(urllib.request.urlopen('https://www.enterprisedb.com/download-postgresql-binaries').read().decode())
