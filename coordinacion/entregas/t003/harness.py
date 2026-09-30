"""T-003 independent harness (Claude). Runs the REAL DurableQueryFoundryService against a private
PostgreSQL, replacing only the SSH transport by a local psql call (same SQL text, same receipts patch).
"""
import json, os, re, shlex, subprocess, sys, threading, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'app'), str(ROOT / 'scripts')]
BIN = ROOT / '.runtime/pgsql/bin'
PORT = 55433
DATA = Path('D:/PROJECTS/.cognition/claude-qf-scratch/pgdata')
LOG = Path('D:/PROJECTS/.cognition/claude-qf-scratch/pg.log')
DB, CTL = 'kaggle_challenge', 'postgres_data_manager'
os.environ['QF_RECIPE_MODE'] = 'lazy_keys'

PROCS = {}

def psql_args(database):
    return [str(BIN / 'psql.exe'), '-h', '127.0.0.1', '-p', str(PORT), '-U', 'qfc', '-d', database]

def sql(statement, database=DB, check=True):
    p = subprocess.run(psql_args(database) + ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-f', '-'], input=statement,
                       text=True, capture_output=True, encoding='utf-8', errors='replace')
    if check and p.returncode:
        raise RuntimeError(p.stderr.strip()[-600:])
    return p.stdout.strip() if check else (p.returncode, p.stdout.strip(), p.stderr.strip())

def local_run(self, command, stdin_text=None, cancel_event=None, stdin_progress_callback=None, **kw):
    tokens = shlex.split(command, posix=True)
    if tokens[0] != 'psql':
        raise RuntimeError('unsupported command in harness: ' + tokens[0])
    args = [str(BIN / 'psql.exe')]
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t == '-h': args += ['-h', '127.0.0.1']; i += 2; continue
        if t == '-U': args += ['-U', 'qfc']; i += 2; continue
        args.append(t); i += 1
    env = {**os.environ, 'PGPORT': str(PORT), 'PGAPPNAME': 'qf-' + threading.current_thread().name, 'PGCLIENTENCODING': 'UTF8'}
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace', env=env)
    PROCS[threading.current_thread().name] = proc
    out, err = proc.communicate(stdin_text if isinstance(stdin_text, str) else None)
    class p: pass
    p.returncode, p.stdout, p.stderr = proc.returncode, out, err
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'psql failed %d' % p.returncode)
    if p.stderr.strip() and not p.stdout.strip():
        raise RuntimeError(p.stderr.strip())
    return p.stdout.strip()

from services.postgres_service import PostgresAdminService
PostgresAdminService.run_remote_command = local_run
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import ORDER, destinations
import dbperf.durable_service as ds

def make_service(cls=DurableQueryFoundryService):
    s = cls()
    s.ssh_client = object(); s.ssh_host = '127.0.0.1'; s.ssh_port = 0; s.ssh_username = 'qf'
    s.postgres_port = PORT; s.sql_username = 'qfc'; s.sql_password = 'x'
    s.close = lambda: None
    return s

def recreate(db, suffix='tpl'):
    sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='%s' AND pid<>pg_backend_pid()" % db, 'postgres')
    sql('DROP DATABASE IF EXISTS "%s"' % db, 'postgres')
    sql('CREATE DATABASE "%s" TEMPLATE "%s_%s"' % (db, db, suffix), 'postgres')

def reset(suffix='tpl'):
    for d in (DB, CTL): recreate(d, suffix)

def make_templates(suffix='tpl'):
    for d in (DB, CTL):
        sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='%s' AND pid<>pg_backend_pid()" % d, 'postgres')
        sql('DROP DATABASE IF EXISTS "%s_%s"' % (d, suffix), 'postgres')
        sql('CREATE DATABASE "%s_%s" TEMPLATE "%s"' % (d, suffix, d), 'postgres')

def make_warm_templates():
    """'fresh' = as generated; 'tpl' = after one full job whose effects were then wiped (schemas/metadata installed)."""
    for d in (DB, CTL):   # 'tpl' still holds the pristine generated state at this point
        sql('DROP DATABASE IF EXISTS "%s_fresh"' % d, 'postgres')
        sql('CREATE DATABASE "%s_fresh" TEMPLATE "%s_tpl"' % (d, d), 'postgres')
    reset('fresh')
    os.environ.pop('QF_JOB_ID', None)
    s = make_service(); finalize(s, expand(s))
    sql("TRUNCATE public.table3,public.table4,public.table1,public.table5,public.table6,public.table2 RESTART IDENTITY; UPDATE public.pgdm_table_row_counts SET row_count=0 WHERE schema_name='public' AND table_name IN ('table1','table2','table3','table4','table5','table6'); DELETE FROM qf_recovery.receipts")
    sql("DELETE FROM qf_recovery.receipts; DELETE FROM app_control.table_versions WHERE version_code<>'0000' AND table_name IN ('table1','table2','table3','table4','table5','table6')", CTL)
    make_templates('tpl')

def counts():
    return {t: int(sql('SELECT count(*) FROM public.' + t)) for t in ORDER}

def receipts(db):
    try: return int(sql('SELECT count(*) FROM qf_recovery.receipts', db))
    except RuntimeError: return 0

def versions():
    return sql("SELECT table_name||':'||version_code FROM app_control.table_versions WHERE operation_kind<>'' AND version_code<>'0000' ORDER BY 1", CTL).splitlines()

def expand(service, name=DB):
    return service.execute_cross_table_expansion(name, 'public.raw_data', ['group001'], destinations('baseline'))

def finalize(service, result, who='T003'):
    return service.register_cross_table_expansion_versions(result, who, 'lab')

def expected_rows():
    # independent oracle: expected destination counts from running the recipes once on a pristine copy
    return None
