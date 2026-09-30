"""Download the vendor's portable binaries to isolated, ignored project storage."""
from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1]
runtime = root / '.runtime'
runtime.mkdir(exist_ok=True)
url = 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260569'
archive = runtime / 'postgresql.zip'
if not archive.exists():
    with urllib.request.urlopen(url, timeout=60) as response, archive.with_suffix('.part').open('wb') as output:
        final_url = response.url
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    archive.with_suffix('.part').rename(archive)
else:
    final_url = url
with zipfile.ZipFile(archive) as bundle:
    # Only the database runtime; no GUI, installer, service or global PATH change.
    for member in bundle.infolist():
        if member.filename.startswith(('pgsql/bin/', 'pgsql/lib/', 'pgsql/share/')):
            target = (runtime / member.filename).resolve()
            if not target.is_relative_to(runtime.resolve()):
                raise ValueError('Archive path outside runtime')
            bundle.extract(member, runtime)
(runtime / 'vendor.json').write_text(json.dumps({'url':url,'final_url':final_url,
    'sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()}, indent=2), encoding='utf-8')
print('Portable database runtime ready:', runtime / 'pgsql/bin')
