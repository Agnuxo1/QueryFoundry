"""Read-only inventory and official WSL MSI download; no credential inspection."""
from pathlib import Path
import hashlib
import json
import urllib.request

root=Path(__file__).resolve().parents[1]/'.runtime/wsl-repair'
root.mkdir(parents=True,exist_ok=True)
release=json.load(urllib.request.urlopen('https://api.github.com/repos/microsoft/WSL/releases/latest'))
assets=[a for a in release['assets'] if a['name'].endswith('.x64.msi')]
if len(assets)!=1 or release['prerelease']: raise RuntimeError('Expected one stable Microsoft x64 MSI')
asset=assets[0]
target=root/asset['name']
if not target.exists():
    with urllib.request.urlopen(asset['browser_download_url'],timeout=60) as response, target.with_suffix('.part').open('wb') as output:
        while data:=response.read(1024*1024): output.write(data)
    target.with_suffix('.part').rename(target)
digest=hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
if asset.get('digest') and asset['digest']!='sha256:'+digest: raise RuntimeError('Publisher digest mismatch')
(root/'download.json').write_text(json.dumps({'release':release['tag_name'],'file':str(target),
    'source':asset['browser_download_url'],'sha256':digest,'bytes':target.stat().st_size},indent=2),encoding='utf-8')
print(json.dumps({'release':release['tag_name'],'file':str(target),'megabytes':round(target.stat().st_size/2**20,1)}))
