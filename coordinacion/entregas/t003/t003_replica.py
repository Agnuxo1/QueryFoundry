"""T-003 scenarios (Claude). Usage: python t003_replica.py [scenario-prefix ...]  -> t003-results.json"""
import json, os, subprocess, sys, threading, time, traceback, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import harness as h

RESULTS = {}
EXPECT = {'table3': 4, 'table4': 24, 'table1': 20000, 'table5': 20000, 'table6': 20000, 'table2': 46383}
TMP = Path('D:/PROJECTS/.cognition/claude-qf-scratch/dumps')
TMP.mkdir(exist_ok=True, parents=True)


def threaded(fn, name):
    box = {}

    def run():
        try:
            box['value'] = fn()
        except BaseException as e:
            box['error'] = '%s: %s' % (type(e).__name__, str(e)[:300])
    t = threading.Thread(target=run, name=name)
    t.start()
    return t, box


def set_job(job=None):
    if job:
        os.environ['QF_JOB_ID'] = job
    else:
        os.environ.pop('QF_JOB_ID', None)


def wait_active(tag, min_age=0.6, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        pid = h.sql("SELECT pid FROM pg_stat_activity WHERE application_name='qf-%s' AND xact_start < now() - interval '%s seconds' AND state='active' LIMIT 1" % (tag, min_age), 'postgres')
        if pid:
            return int(pid)
        time.sleep(0.1)
    raise RuntimeError('backend not found for ' + tag)


def locks_left():
    return int(h.sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory'", 'postgres'))


def scenario(fn):
    def wrapper():
        name = fn.__name__
        t0 = time.time()
        h.reset('fresh' if name.startswith('s00') else 'tpl')
        try:
            info = fn() or {}
            RESULTS[name] = {'status': 'PASS', **info, 'seconds': round(time.time() - t0, 1)}
        except AssertionError as e:
            RESULTS[name] = {'status': 'FAIL', 'detail': str(e)[:500], 'trace': traceback.format_exc()[-600:]}
        except Exception as e:
            RESULTS[name] = {'status': 'ERROR', 'detail': '%s: %s' % (type(e).__name__, str(e)[:400])}
        finally:
            set_job(None)
        print(name, {k: v for k, v in RESULTS[name].items() if k != 'trace'}, flush=True)
    wrapper.__name__ = fn.__name__
    return wrapper


def full(job=None):
    set_job(job)
    s = h.make_service()
    r = h.expand(s)
    v = h.finalize(s, r)
    return s, r, v


def crash_restart():
    subprocess.run([str(h.BIN / 'pg_ctl.exe'), '-D', str(h.DATA), 'restart', '-m', 'immediate', '-w', '-l', str(h.LOG)], check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def dump(db, path):
    subprocess.run([str(h.BIN / 'pg_dump.exe'), '-h', '127.0.0.1', '-p', str(h.PORT), '-U', 'qfc', '-Fc', '-f', str(path), db], check=True)


def restore(db, path, extra=()):
    h.sql('DROP DATABASE IF EXISTS "%s"' % db, 'postgres')
    h.sql('CREATE DATABASE "%s"' % db, 'postgres')
    subprocess.run([str(h.BIN / 'pg_restore.exe'), '-h', '127.0.0.1', '-p', str(h.PORT), '-U', 'qfc', '-d', db, '--no-owner', *extra, str(path)], check=True, capture_output=True)


@scenario
def s00_fresh_install_race_four_clients_same_uuid():
    job = str(uuid.uuid4())
    set_job(job)

    def client():
        s = h.make_service()
        return len(h.finalize(s, h.expand(s)))
    ts = [threaded(client, 'f%d' % i) for i in range(4)]
    for t, _ in ts:
        t.join(180)
    outs = [b for _, b in ts]
    errors = [o['error'][:160] for o in outs if 'error' in o]
    # integrity regardless of client errors: no duplicates, at most one set of versions, and a retry completes
    c = h.counts()
    assert all(c[t] in (0, EXPECT[t]) for t in c) and (sum(c.values()) in (0, sum(EXPECT.values()))), c
    s = h.make_service()
    h.finalize(s, h.expand(s))
    assert h.counts() == EXPECT and len(h.versions()) == 6 and h.receipts(h.DB) == 1
    return {'clients_failed_on_fresh_install': len(errors), 'sample_errors': errors[:2]}


@scenario
def s01_same_uuid_race():
    job = str(uuid.uuid4())
    set_job(job)

    def client():
        s = h.make_service()
        r = h.expand(s)
        v = h.finalize(s, r)
        return {'recovered': bool(r['timing_report'].get('resumed_committed_job')), 'versions': len(v)}
    ts = [threaded(client, 'c%d' % i) for i in range(4)]
    for t, _ in ts:
        t.join(180)
    outs = [b for _, b in ts]
    assert all('value' in o for o in outs), outs
    executed = sum(1 for o in outs if not o['value']['recovered'])
    assert h.counts() == EXPECT, h.counts()
    assert h.receipts(h.DB) == 1 and h.receipts(h.CTL) == 1 and len(h.versions()) == 6, (h.receipts(h.DB), h.receipts(h.CTL), h.versions())
    assert locks_left() == 0
    return {'clients': 4, 'clients_that_ran_dml': executed}


@scenario
def s02_distinct_uuid_race():
    set_job(None)

    def client():
        s = h.make_service()
        r = h.expand(s)
        return len(h.finalize(s, r))
    ts = [threaded(client, 'd%d' % i) for i in range(2)]
    for t, _ in ts:
        t.join(180)
    ok = [b for _, b in ts if 'value' in b]
    bad = [b for _, b in ts if 'error' in b]
    assert len(ok) == 1 and len(bad) == 1, [b for _, b in ts]
    assert h.counts() == EXPECT, h.counts()
    assert h.receipts(h.DB) == 1 and len(h.versions()) == 6
    return {'loser_error': bad[0]['error'][:200]}


@scenario
def s03_kill_backend_mid_expansion_then_retry():
    set_job(str(uuid.uuid4()))
    t, box = threaded(lambda: h.expand(h.make_service()), 'victim')
    pid = wait_active('victim')
    h.sql('SELECT pg_terminate_backend(%d)' % pid, 'postgres')
    t.join(60)
    assert 'error' in box, box
    assert sum(h.counts().values()) == 0 and h.receipts(h.DB) == 0 and locks_left() == 0, (h.counts(), h.receipts(h.DB), locks_left())
    s = h.make_service()
    r = h.expand(s)
    h.finalize(s, r)
    assert h.counts() == EXPECT and h.receipts(h.DB) == 1 and len(h.versions()) == 6
    return {'client_error': box['error'][:160]}


@scenario
def s04a_server_crash_mid_expansion_then_retry():
    set_job(str(uuid.uuid4()))
    t, box = threaded(lambda: h.expand(h.make_service()), 'victim')
    wait_active('victim')
    crash_restart()
    t.join(60)
    assert 'error' in box, box
    assert sum(h.counts().values()) == 0 and h.receipts(h.DB) == 0
    s = h.make_service()
    r = h.expand(s)
    h.finalize(s, r)
    assert h.counts() == EXPECT and len(h.versions()) == 6
    return {'client_error': box['error'][:160]}


@scenario
def s04b_server_crash_between_data_commit_and_versions():
    job = str(uuid.uuid4())
    set_job(job)
    s = h.make_service()
    h.expand(s)
    assert h.receipts(h.DB) == 1 and len(h.versions()) == 0
    crash_restart()
    s2 = h.make_service()
    r2 = h.expand(s2)
    assert r2['timing_report'].get('resumed_committed_job') == job
    assert h.counts() == EXPECT
    v = h.finalize(s2, r2)
    assert len(v) == 6 and len(h.versions()) == 6 and h.receipts(h.CTL) == 1
    return {}


class LostReply(h.DurableQueryFoundryService):
    lose = None

    def run_remote_command(self, command, **kw):
        text = kw.get('stdin_text') or ''
        ctx = getattr(self, '_qf_context', None)
        target = bool(ctx) and 'pgdm_expansion_timings' in text and (
            (ctx['kind'] == 'data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text) or
            (ctx['kind'] == 'versions' and 'Commit atomic version batch' in text))
        kind = ctx['kind'] if ctx else None
        out = super().run_remote_command(command, **kw)
        if target and self.lose == kind:
            self.lose = None
            raise ConnectionError('injected lost reply after commit')
        return out


@scenario
def s05_lost_replies_data_and_versions():
    set_job(str(uuid.uuid4()))
    s = h.make_service(LostReply)
    s.lose = 'data'
    r = h.expand(s)
    assert h.counts() == EXPECT and h.receipts(h.DB) == 1
    s.lose = 'versions'
    v1 = h.finalize(s, r)
    v2 = h.finalize(s, r)
    s3 = h.make_service()
    v3 = h.finalize(s3, h.expand(s3))

    def key(v):
        return sorted(x['version_code'] + x['table_name'] for x in v)
    assert key(v1) == key(v2) == key(v3) and len(h.versions()) == 6 and h.receipts(h.CTL) == 1, (key(v1), h.versions())
    return {'versions': key(v1)}


@scenario
def s06_raw_version_changes_between_attempt_and_retry():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    r = h.expand(s)  # data committed, finalization pending
    h.sql("INSERT INTO app_control.table_versions(database_name,schema_name,table_name,version_code,version_title,created_by,workstation_name,sql_recipe,restored_from_version,version_history_log,version_history_format,raw_dump_path,raw_hash,raw_ingested_at,raw_schema,operation_kind) "
          "SELECT database_name,schema_name,table_name,'0001','new raw batch','t003','lab',sql_recipe,NULL,version_history_log,version_history_format,raw_dump_path,raw_hash,raw_ingested_at,raw_schema,operation_kind FROM app_control.table_versions WHERE table_name='raw_data' AND version_code='0000'", h.CTL)
    try:
        h.expand(h.make_service())
        outcome = 'replayed'
    except RuntimeError as e:
        outcome = 'refused: ' + str(e)[:160]
    ok = len(h.finalize(h.make_service(), r)) == 6
    return {'retry_outcome_after_new_raw_version': outcome, 'finalize_with_pre_restart_result_ok': ok}


@scenario
def s07a_full_dump_restore_same_names_then_resume():
    job = str(uuid.uuid4())
    set_job(job)
    s = h.make_service()
    r = h.expand(s)
    dump(h.DB, TMP / 'a.dump')
    h.finalize(s, r)
    dump(h.CTL, TMP / 'c.dump')
    restore(h.DB, TMP / 'a.dump')
    restore(h.CTL, TMP / 'c.dump')
    s2 = h.make_service()
    r2 = h.expand(s2)
    v2 = h.finalize(s2, r2)
    assert r2['timing_report'].get('resumed_committed_job') == job and len(v2) == 6 and len(h.versions()) == 6
    assert h.counts() == EXPECT
    return {'replayed_after_restore': True}


@scenario
def s07b_restore_under_other_database_name():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    r = h.expand(s)
    h.finalize(s, r)
    dump(h.DB, TMP / 'a.dump')
    restore('kaggle_challenge_copy', TMP / 'a.dump')
    try:
        h.expand(h.make_service(), 'kaggle_challenge_copy')
        outcome = 'replayed'
    except RuntimeError as e:
        outcome = 'refused: ' + str(e)[:200]
    return {'outcome_same_job_on_renamed_copy': outcome}


@scenario
def s07c_restore_without_receipts_schema():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    h.finalize(s, h.expand(s))
    dump(h.DB, TMP / 'a.dump')
    restore(h.DB, TMP / 'a.dump', ('-N', 'qf_recovery'))
    assert h.receipts(h.DB) == 0
    before = h.counts()
    try:
        h.expand(h.make_service())
        outcome = 'reran'
    except RuntimeError as e:
        outcome = 'refused: ' + str(e)[:200]
    assert h.counts() == before
    return {'outcome': outcome, 'counts_unchanged': True}


@scenario
def s07d_control_restored_to_older_dump():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    r = h.expand(s)
    dump(h.CTL, TMP / 'c_before.dump')
    h.finalize(s, r)
    restore(h.CTL, TMP / 'c_before.dump')
    assert len(h.versions()) == 0
    s2 = h.make_service()
    v = h.finalize(s2, h.expand(s2))
    assert len(v) == 6 and len(h.versions()) == 6 and h.receipts(h.CTL) == 1
    return {'versions': h.versions()}


@scenario
def s08_destination_tampering_after_commit():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    h.expand(s)
    out = {}
    h.sql("UPDATE public.table2 SET integer_column_2=integer_column_2+1 WHERE id_column_1=(SELECT min(id_column_1) FROM public.table2)")
    try:
        h.expand(h.make_service())
        out['same_count_content_change'] = 'replayed (NOT detected)'
    except RuntimeError:
        out['same_count_content_change'] = 'refused'
    h.sql("UPDATE public.table2 SET integer_column_2=integer_column_2-1 WHERE id_column_1=(SELECT min(id_column_1) FROM public.table2)")
    h.sql("INSERT INTO public.table3(text_column_1,text_column_2,numeric_column_1,text_column_3,text_column_4) VALUES ('legit edit','x',NULL,'A','B')")
    try:
        h.expand(h.make_service())
        out['legit_extra_row'] = 'replayed'
    except RuntimeError:
        out['legit_extra_row'] = 'refused (versions stay pending)'
    h.sql("DELETE FROM public.table3 WHERE text_column_1='legit edit'")
    s3 = h.make_service()
    out['after_revert_finalize'] = len(h.finalize(s3, h.expand(s3)))
    return out


@scenario
def s09_source_payload_changed_after_commit():
    set_job(str(uuid.uuid4()))
    s = h.make_service()
    h.expand(s)
    h.sql("UPDATE public.raw_data SET raw=jsonb_set(raw,'{values,text_column_1}','\"mutated\"') WHERE raw_id=1")
    try:
        h.expand(h.make_service())
        out = 'replayed (source payload edits after commit are not checked)'
    except RuntimeError as e:
        out = 'refused: ' + str(e)[:160]
    return {'outcome': out}


@scenario
def s10_second_job_after_wipe_versions_monotonic():
    full(str(uuid.uuid4()))
    h.sql("TRUNCATE public.table3,public.table4,public.table1,public.table5,public.table6,public.table2 RESTART IDENTITY; UPDATE public.pgdm_table_row_counts SET row_count=0 WHERE schema_name='public' AND table_name IN ('table1','table2','table3','table4','table5','table6')")
    s, r, v = full(str(uuid.uuid4()))
    codes = sorted({x['version_code'] for x in v})
    allv = h.versions()
    assert codes == ['0002'] and len(allv) == 12, (codes, allv)
    return {'second_job_version_codes': codes}


@scenario
def s11_client_killed_mid_expansion():
    set_job(str(uuid.uuid4()))
    t, box = threaded(lambda: h.expand(h.make_service()), 'victim')
    wait_active('victim')
    h.PROCS['victim'].kill()
    t.join(60)
    time.sleep(3)
    left = int(h.sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='qf-victim'", 'postgres'))
    assert sum(h.counts().values()) == 0 and h.receipts(h.DB) == 0, (h.counts(), h.receipts(h.DB))
    s = h.make_service()
    h.finalize(s, h.expand(s))
    return {'backend_left_after_3s': left, 'locks_left': locks_left()}


ALL = [v for k, v in sorted(globals().items()) if k.startswith('s') and k[1:3].isdigit() and callable(v)]

if __name__ == '__main__':
    wanted = sys.argv[1:]
    for f in ALL:
        if not wanted or any(f.__name__.startswith(w) for w in wanted):
            f()
    out = Path(__file__).parent / 't003-results.json'
    old = json.loads(out.read_text()) if out.exists() else {}
    old.update(RESULTS)
    out.write_text(json.dumps(old, indent=1))
    print('PASS', sum(r['status'] == 'PASS' for r in RESULTS.values()), 'of', len(RESULTS))
