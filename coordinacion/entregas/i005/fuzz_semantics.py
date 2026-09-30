"""Differential fuzz: table2 baseline vs candidate on synthetic mutated rows.

Compares status, exact PostgreSQL error message (incl. offending value) and the
SHA-256 of the sorted output multiset. Read-only on the official data: rows are
copied into a session TEMP table and mutated there. Usage: python fuzz_semantics.py MODE TRIALS SEED
MODE is payload_once or lazy_keys. Requires a PostgreSQL with the official 100k database and populated table1.
"""
import hashlib, json, random, re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
from dbperf.recipes import rewrite
from lazy_keys import lazy_keys
PSQL = [str(ROOT / '.runtime/pgsql/bin/psql.exe'), '-h', '127.0.0.1', '-p', '55433', '-U', 'qfc', '-X', '-qAt',
        '-v', 'ON_ERROR_STOP=1', '-d', 'kaggle_challenge', '-f', '-']
RECIPE = (ROOT / 'app/SQL files/06_table2.sql').read_text(encoding='utf-8')

def select_of(sql):
    s = sql[sql.index('\nSELECT\n') + 1:].strip().rstrip(';')
    return s.replace('{{source}}', 'fz')

from experiments.rejected_sparse_positions import rewrite as sparse_rewrite
VARIANTS = {'sparse_control': sparse_rewrite(RECIPE, 'sparse_positions'), 'baseline': RECIPE, 'payload_once': rewrite(RECIPE, 'payload_once'), 'lazy_keys': lazy_keys(RECIPE)}
POOL = ['bad', '', ' ', '-1', '40000', '32768', 'INVALID', 'x', 'y', 'z', ' Y ', '1e3', '0.5', '1.', '.5', ' 3 ', '0', '00', '15', '14', '99999999999', 'NaN', 'Infinity', '-0', None, 1, 2.5, True]

def jlit(v):
    if v is None: return "'null'::jsonb"
    if isinstance(v, bool): return "'true'::jsonb"
    if isinstance(v, str): return 'to_jsonb($q$' + v + '$q$::text)'
    return "'" + json.dumps(v) + "'::jsonb"

def run(sql):
    p = subprocess.run(PSQL, input=sql, text=True, capture_output=True, encoding='utf-8', errors='replace')
    if p.returncode:
        m = re.search(r'ERROR:\s*(.*)', p.stderr)
        return 'error: ' + (m[1].strip() if m else p.stderr[-200:])
    rows = sorted(p.stdout.splitlines())
    return 'ok:%d:%s' % (len(rows), hashlib.sha256('\n'.join(rows).encode()).hexdigest()[:16])

def main(mode, trials, seed):
    rng = random.Random(seed)
    fields = [f'integer_column_{i}' for i in range(2, 16)] + [f'text_column_{i}' for i in range(4, 18)] + [f'numeric_column_{i}' for i in range(3, 31)]
    seen = {}
    for t in range(trials):
        n = 40
        start = rng.randrange(1, 90000)
        muts = [(rng.randrange(n), rng.choice(fields), rng.choice(POOL)) for _ in range(rng.choice([3, 6, 12, 20] if __import__('os').environ.get('FUZZ_HEAVY') else [0, 1, 1, 2, 3, 6]))]
        mut_sql = ''.join(f"UPDATE fz SET raw=jsonb_set(raw,'{{values,{f}}}',{jlit(v)}) WHERE raw_id=(SELECT raw_id FROM fz ORDER BY raw_id OFFSET {i} LIMIT 1);" + chr(10) for i, f, v in muts)
        prefix = f"CREATE TEMP TABLE fz AS SELECT raw_id, raw FROM public.raw_data WHERE raw_id >= {start} ORDER BY raw_id LIMIT {n};\n" + mut_sql
        a = run(prefix + 'SELECT to_jsonb(q)::text FROM (' + select_of(VARIANTS['baseline']) + ') q;')
        b = run(prefix + 'SELECT to_jsonb(q)::text FROM (' + select_of(VARIANTS[mode]) + ') q;')
        key = (a.split(':')[0])
        seen[key] = seen.get(key, 0) + 1
        if a != b:
            print('MISMATCH trial', t, 'seed', seed, muts, '\n  baseline:', a, '\n  ', mode + ':', b)
            return 1
    print(mode, 'trials', trials, 'seed', seed, 'outcomes', seen, 'ALL IDENTICAL')
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
