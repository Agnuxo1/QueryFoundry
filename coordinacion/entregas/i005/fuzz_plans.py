"""Differential fuzz of table2 recipes under forced plan/config changes (Claude, 2026-09-30).

For each trial: mutated copy of source rows in a regular (non-TEMP) table, one config applied to the session, then
baseline vs candidate SELECT. Comparison classes:
  * 'single-row' trials (all mutated cells in ONE source row): status + exact error message must match;
  * 'multi-row'  trials: status and output hash must match; if both error the messages may legitimately differ
    (which bad row a plan meets first is plan-dependent even for the original) and are reported separately.
Usage: python fuzz_plans.py MODE TRIALS SEED   (MODE: lazy_keys | payload_once | sparse_control)
"""
import hashlib, json, os, random, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'app'), str(Path(__file__).parent)]
from dbperf.recipes import rewrite
from lazy_keys import lazy_keys
from experiments.rejected_sparse_positions import rewrite as sparse_rewrite

PSQL = [str(ROOT / '.runtime/pgsql/bin/psql.exe'), '-h', '127.0.0.1', '-p', '55433', '-U', 'qfc', '-X', '-qAt',
        '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose', '-d', 'kaggle_challenge', '-f', '-']
RECIPE = (ROOT / 'app/SQL files/06_table2.sql').read_text(encoding='utf-8')
VARIANTS = {'baseline': RECIPE, 'payload_once': rewrite(RECIPE, 'payload_once'), 'lazy_keys': lazy_keys(RECIPE),
            'sparse_control': sparse_rewrite(RECIPE, 'sparse_positions')}

CONFIGS = {
    'default': '',
    'work_mem_64kB': 'SET work_mem=\'64kB\';',
    'work_mem_1GB': 'SET work_mem=\'1GB\';',
    'parallel_forced': 'SET debug_parallel_query=on; SET max_parallel_workers_per_gather=4; SET parallel_setup_cost=0; SET parallel_tuple_cost=0; SET min_parallel_table_scan_size=0;',
    'no_parallel': 'SET max_parallel_workers_per_gather=0;',
    'jit_forced': 'SET jit=on; SET jit_above_cost=0; SET jit_inline_above_cost=0; SET jit_optimize_above_cost=0;',
    'jit_off': 'SET jit=off;',
    'no_nestloop': 'SET enable_nestloop=off;',
    'no_hashjoin': 'SET enable_hashjoin=off;',
    'no_mergejoin': 'SET enable_mergejoin=off;',
    'merge_only': 'SET enable_nestloop=off; SET enable_hashjoin=off;',
    'no_material': 'SET enable_material=off;',
    'random_page_cost_1': 'SET random_page_cost=1; SET enable_seqscan=off;',
    'cpu_cost_skew': 'SET cpu_operator_cost=1; SET cpu_tuple_cost=0.0001;',
    'stats_target_1': '@ANALYZE1',
    'stats_target_10000': '@ANALYZE10000',
}
POOL = ['bad', '', ' ', '-1', '40000', '32768', '-32769', '99999999999', 'NaN', 'Infinity', '1e400', '0x10', '1_0', '٣', 'INVALID',
        'x', ' z ', 'Y', 'y', '0', '00', '15', '14', '1.5', ' 7 ', '+3', '1e3', '0.0000001', '-0', '9' * 40]
TYPED = ['null', 'num_int', 'num_float', 'bool', 'array', 'object', 'missing']
FIELDS = ([f'integer_column_{i}' for i in range(2, 16)] + [f'text_column_{i}' for i in range(4, 18)] +
          [f'numeric_column_{i}' for i in range(3, 31)])

def select_of(sql):
    return sql[sql.index('\nSELECT\n') + 1:].strip().rstrip(';').replace('{{source}}', 'fzp')

def mutate_sql(field, op, rid):
    base = "raw"
    path = '{values,%s}' % field
    if op == 'missing': expr = "raw #- '%s'" % path
    elif op == 'null': expr = "jsonb_set(raw,'%s','null'::jsonb)" % path
    elif op == 'num_int': expr = "jsonb_set(raw,'%s','3'::jsonb)" % path
    elif op == 'num_float': expr = "jsonb_set(raw,'%s','12.5'::jsonb)" % path
    elif op == 'bool': expr = "jsonb_set(raw,'%s','true'::jsonb)" % path
    elif op == 'array': expr = "jsonb_set(raw,'%s','[1,2]'::jsonb)" % path
    elif op == 'object': expr = "jsonb_set(raw,'%s','{\"a\":1}'::jsonb)" % path
    elif op == 'values_missing': expr = "raw #- '{values}'"
    elif op == 'values_not_object': expr = "jsonb_set(raw,'{values}','\"oops\"'::jsonb)"
    else: expr = "jsonb_set(raw,'%s',to_jsonb($q$%s$q$::text))" % (path, op)
    return "UPDATE fzp SET raw=%s WHERE raw_id=%d;\n" % (expr, rid)

TBL = 'fzp_%d' % os.getpid()

def run(sql):
    sql = sql.replace('fzp', TBL)
    p = subprocess.run(PSQL, input=sql, text=True, capture_output=True, encoding='utf-8', errors='replace')
    if p.returncode:
        m = re.search(r'ERROR:\s*([0-9A-Z]{5}):\s*(.*)', p.stderr)
        return ('error', (m[1] + ' ' + m[2].strip()) if m else p.stderr[-200:])
    rows = sorted(p.stdout.splitlines())
    return ('ok', '%d:%s' % (len(rows), hashlib.sha256('\n'.join(rows).encode()).hexdigest()[:16]))

def config_sql(cfg):
    c = CONFIGS[cfg]
    if c.startswith('@ANALYZE'):
        return 'SET default_statistics_target=%s; ANALYZE fzp; ANALYZE public.table1;' % c[8:]
    return c

def main(mode, trials, seed, n_rows=400):
    rng = random.Random(seed)
    stats = {'cell': 0, 'row_or_multi': 0, 'errors': 0, 'multi_source_message_differs_both_error': 0, 'multi_source_sqlstate_differs_both_error': 0}
    by_cfg = {k: 0 for k in CONFIGS}
    for t in range(trials):
        start = rng.randrange(1, 19000)
        cfg = rng.choice(list(CONFIGS))
        by_cfg[cfg] += 1
        kind = rng.choice(['cell', 'cell', 'cell', 'row', 'multi'])   # cell: ONE mutated cell => one possible error source
        single = kind == 'cell'
        rid0 = start + rng.randrange(n_rows)
        muts = []
        for _ in range({'cell': 1, 'row': rng.choice([2, 4, 8, 14]), 'multi': rng.choice([2, 3, 6, 10])}[kind]):
            rid = rid0 if kind != 'multi' else start + rng.randrange(n_rows)
            r = rng.random()
            if r < 0.04: op = rng.choice(['values_missing', 'values_not_object'])
            elif r < 0.40: op = rng.choice(TYPED)
            else: op = rng.choice(POOL)
            muts.append((rid, rng.choice(FIELDS), op))
        prefix = ("DROP TABLE IF EXISTS fzp; CREATE UNLOGGED TABLE fzp AS SELECT raw_id, raw FROM public.raw_data "
                  "WHERE raw_id BETWEEN %d AND %d;\n" % (start, start + n_rows - 1) + ''.join(
                      mutate_sql(f, op, rid) for rid, f, op in muts) + "ANALYZE fzp;\n" + config_sql(cfg) + "\n")
        def query(v): return prefix + 'SELECT to_jsonb(q)::text FROM (' + select_of(VARIANTS[v]) + ') q;'
        a = run(query('baseline')); b = run(query(mode))
        stats['cell' if single else 'row_or_multi'] += 1
        stats['errors'] += a[0] == 'error'
        same = a == b
        if not same and not single and a[0] == 'error' and b[0] == 'error':
            # several possible error sources: WHICH error surfaces is plan-dependent even for the original
            stats['multi_source_message_differs_both_error'] += 1
            if a[1][:5] != b[1][:5]: stats['multi_source_sqlstate_differs_both_error'] += 1
            same = True
        if not same:
            print('MISMATCH trial', t, 'seed', seed, 'cfg', cfg, kind, '\n  muts', muts,
                  '\n  baseline:', a, '\n  %s:' % mode, b)
            return 1
    print(json.dumps({'mode': mode, 'trials': trials, 'seed': seed, **stats, 'trials_per_config': by_cfg}))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
