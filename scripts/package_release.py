"""Package the committed public source, excluding runtime secrets and datasets."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
def main():
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    if git('status','--porcelain'): raise RuntimeError('Commit all release changes first')
    commit=git('rev-parse','HEAD')
    names=git('ls-files').splitlines()
    if any(n.split('/')[0] in {'.runtime','.venv','upstream','.cognition'} for n in names):
        raise RuntimeError('Runtime material must not be tracked')
    output=ROOT/'.runtime/releases'; output.mkdir(parents=True,exist_ok=True)
    archive=output/('QueryFoundry-'+commit[:12]+'.zip')
    subprocess.run(['git','archive','--format=zip','--prefix=QueryFoundry/',
        '--output',str(archive),'HEAD'],cwd=ROOT,check=True)
    manifest=json.loads((ROOT/'docs/upstream-manifest.json').read_text())
    with zipfile.ZipFile(archive) as package:
        for path,expected in manifest['files'].items():
            assert hashlib.sha256(package.read('QueryFoundry/app/'+path)).hexdigest()==expected,path
    receipt={'commit':commit,'archive':str(archive),'bytes':archive.stat().st_size,
        'sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),
        'official_files_verified_in_archive':len(manifest['files']),
        'published':False,'kaggle_submitted':False}
    (output/'release.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__': main()
