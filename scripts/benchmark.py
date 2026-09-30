"""Diagnostic SQL benchmark with multiset equivalence and server stage timings.

This deliberately does not label SQL-only timings MEASURED PROCESSING TOTAL.
The Windows GUI + Linux SSH full workflow is a separate mandatory release gate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'app')]
from services.postgres_service import PostgresAdminService
from dbperf.recipes import ORDER, MODES, destinations
from local_lab import psql

def canonical(table):
    if table == 'table3':
        return "SELECT to_jsonb(t)-'id_column_1' AS value FROM public.table3 t"
    if table == 'table4':
        return 'SELECT to_jsonb(t) AS value FROM public.table4 t'
    if table == 'table1':
        return """SELECT (to_jsonb(t)-'id_column_1'-'id_column_4') ||
            jsonb_build_object('dimension',to_jsonb(d)-'id_column_1') AS value
            FROM public.table1 t LEFT JOIN public.table3 d ON d.id_column_1=t.id_column_4"""
    return f"""SELECT (to_jsonb(t)-'id_column_1'-'id_column_2') ||
        jsonb_build_object('source_raw_id',m.id_column_3) AS value
        FROM public.{table} t JOIN public.table1 m ON m.id_column_1=t.id_column_2"""

def batch(mode, snapshot=False, failure=False):
    service = PostgresAdminService()
    # Validate functions against real catalog; no relaxation of upstream guard.
    service.sql_username = 'qf_lab'
    def transport(command, **kwargs):
        import shlex
        argv = shlex.split(command)
        return psql(argv[argv.index('-c')+1], argv[argv.index('-d')+1])
    service.run_remote_command = transport
    prepared = service._prepare_cross_table_expansion('public.raw_data',['group001'],destinations(mode))
    service._validate_cross_table_expansion_functions('kaggle_challenge',prepared['destinations'])
    parts = ['BEGIN ISOLATION LEVEL REPEATABLE READ;',
        'TRUNCATE public.table2,public.table5,public.table6,public.table1,public.table3,public.table4 RESTART IDENTITY;',
        'CREATE TEMP TABLE qf_timings (stage text,seconds double precision) ON COMMIT DROP;',
        service._build_cross_table_expansion_lock_sql(prepared['destinations'])]
    statements = [('manifest',service._build_expansion_manifest_sql('public','raw_data',['group001']).split('SELECT json_build_object(')[0])]
    statements += [(d['table_name'],s) for d in prepared['destinations'] for s in d['statements']]
    for name, sql in statements:
        parts.append("DO $qf$ DECLARE started timestamptz:=clock_timestamp(); BEGIN " + sql.strip().rstrip(';') +
            "; INSERT INTO qf_timings VALUES ('"+name+"',EXTRACT(EPOCH FROM clock_timestamp()-started)); END $qf$;")
    if failure:
        parts.append("DO $fail$ BEGIN RAISE EXCEPTION 'injected precommit failure'; END $fail$;")
    for table in ORDER:
        query = canonical(table)
        if snapshot:
            parts.append(f'CREATE TABLE qf_reference.{table} AS {query};')
        else:
            parts.append(f"""DO $eq$ BEGIN IF EXISTS (
                ({query} EXCEPT ALL SELECT value FROM qf_reference.{table})
                UNION ALL (SELECT value FROM qf_reference.{table} EXCEPT ALL {query})
                ) THEN RAISE EXCEPTION 'Output mismatch: {table}'; END IF; END $eq$;""")
    parts.append("SELECT json_build_object('mode','"+mode+"','sql_stage_seconds',SUM(seconds),"
        "'stages',json_agg(json_build_object('name',stage,'seconds',seconds)))::text FROM qf_timings;")
    parts.append('COMMIT;')
    return '\n'.join(parts)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if args.repeats < 1 or args.repeats > 10: parser.error('repeats must be 1..10')
    (ROOT/'reports').mkdir(exist_ok=True)
    # Fixed reference is created once in an isolated diagnostic database.
    exists = psql("SELECT to_regclass('qf_reference.table1') IS NOT NULL;") == 't'
    if not exists:
        psql('CREATE SCHEMA IF NOT EXISTS qf_reference;')
        print('Establishing original SQL baseline before candidates...',flush=True)
        psql(batch('baseline',snapshot=True))
    records=[]
    for repeat in range(args.repeats):
        order=MODES if repeat % 2 == 0 else tuple(reversed(MODES))
        for mode in order:
            result = json.loads(psql(batch(mode)).splitlines()[-1])
            result['repeat']=repeat
            result['equivalent_multiset']=True
            records.append(result)
            (ROOT/'reports/sql-runs.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
            print(mode,round(result['sql_stage_seconds'],4),'seconds; six outputs equivalent',flush=True)
    # A server exception before COMMIT must leave all committed outputs intact.
    rollback=False
    try: psql(batch('fields_once',failure=True))
    except RuntimeError as error:
        if 'injected precommit failure' not in str(error): raise
        rollback=True
    for table in ORDER:
        query=canonical(table)
        if psql(f'SELECT count(*) FROM (({query} EXCEPT ALL SELECT value FROM qf_reference.{table}) UNION ALL (SELECT value FROM qf_reference.{table} EXCEPT ALL {query})) d;') != '0':
            raise RuntimeError('Rollback corrupted '+table)
    medians={mode:statistics.median(r['sql_stage_seconds'] for r in records if r['mode']==mode) for mode in MODES}
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'environment':psql('SELECT version();'),
        'benchmark_kind':'diagnostic_sql_only','official_parity':False,
        'excludes':['GUI preparation','full preflight','managed row counters','version registration','dump handling','SSH path'],
        'source_rows':int(psql('SELECT count(*) FROM public.raw_data;')),
        'generator_sha256':hashlib.sha256((ROOT/'app/generate_database.py').read_bytes()).hexdigest(),
        'median_seconds':medians,'speedup_vs_baseline':{m:medians['baseline']/s for m,s in medians.items()},
        'precommit_rollback_verified':rollback,'equivalence':'bidirectional EXCEPT ALL; surrogate keys resolved to source/dimension values',
        'database_bytes':int(psql("SELECT pg_database_size(current_database());")),
        'peak_server_ram_bytes':None,'peak_intermediate_bytes':None,'eligible_submission':False}
    (ROOT/'reports/sql-benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'median_seconds':medians,'rollback':rollback,'official_parity':False},indent=2))

if __name__ == '__main__': main()
