"""Verify that every vendored organizer file, including the generator, is intact."""
from pathlib import Path
import hashlib
import json

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'docs/upstream-manifest.json').read_text())
changed=[name for name,digest in manifest['files'].items()
    if not (root/'app'/name).is_file() or hashlib.sha256((root/'app'/name).read_bytes()).hexdigest()!=digest]
if changed: raise SystemExit('Changed organizer files: '+', '.join(changed))
print('Verified',len(manifest['files']),'unchanged organizer files at',manifest['commit'])
