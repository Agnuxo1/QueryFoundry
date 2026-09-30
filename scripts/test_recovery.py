"""Real PostgreSQL failpoints for the experimental receipt primitive."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dbperf.receipts import INSTALL_SQL, transaction
from local_lab import BIN, environment, psql

def main():
    psql(INSTALL_SQL + 'CREATE TABLE IF NOT EXISTS qf_recovery.probe (job_id uuid PRIMARY KEY);')
    job=str(uuid.uuid4())
    digest=hashlib.sha256(b'recovery-probe-v1').hexdigest()
    work=f"INSERT INTO qf_recovery.probe VALUES ('{job}');"
    sql=transaction(job,digest,work,"jsonb_build_object('completed',true)")
    # Simulate losing the response after COMMIT, then reconnect and replay.
    psql(sql)
    replay=json.loads(psql(sql).splitlines()[-1])
    assert replay['completed'] and psql(f"SELECT count(*) FROM qf_recovery.probe WHERE job_id='{job}';")=='1'
    mismatch=False
    try: psql(transaction(job,'0'*64,work))
    except RuntimeError as error: mismatch='different inputs' in str(error)
    assert mismatch
    failed=str(uuid.uuid4())
    failed_sql=transaction(failed,digest,f"INSERT INTO qf_recovery.probe VALUES ('{failed}'); PERFORM pg_sleep(30);")
    env=environment(); env['PGAPPNAME']='qf-failpoint-'+failed
    process=subprocess.Popen([str(BIN/'psql.exe'),'-X','-qAt','-v','ON_ERROR_STOP=1','-d','kaggle_challenge','-f','-'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    process.stdin.write(failed_sql.encode()); process.stdin.close()
    victim=None
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        victim=psql("SELECT pid FROM pg_stat_activity WHERE application_name='"+env['PGAPPNAME']+"' AND wait_event='PgSleep';")
        if victim: break
        time.sleep(.1)
    try:
        assert victim, 'Could not locate sleeping failpoint backend'
        assert psql('SELECT pg_terminate_backend('+str(int(victim))+');')=='t'
        process.wait(timeout=10)
        assert process.returncode != 0
        assert psql(f"SELECT count(*) FROM qf_recovery.probe WHERE job_id='{failed}';")=='0'
        assert psql(f"SELECT count(*) FROM qf_recovery.receipts WHERE job_id='{failed}';")=='0'
        psql(transaction(failed,digest,f"INSERT INTO qf_recovery.probe VALUES ('{failed}');"))
        assert psql(f"SELECT count(*) FROM qf_recovery.probe WHERE job_id='{failed}';")=='1'
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stdout.close(); process.stderr.close()
    report={'lost_reply_replay_exactly_once':True,'changed_request_rejected':True,
        'backend_termination_rolls_back_effect_and_receipt':True,'retry_after_backend_death_succeeds':True,
        'scope':'experimental receipt primitive only','gui_integration':False,
        'cross_database_version_finalization':False,'checkpoint_chunks':False}
    (ROOT/'reports/recovery-tests.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
