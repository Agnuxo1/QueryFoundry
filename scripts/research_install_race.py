"""Claude D-1 replication: fresh installation by six real SSH clients on Linux.

Only one uniquely named disposable database is created/dropped. Official source
and destination databases are never modified by this experiment.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import connect, sql, docker, CONTAINER, PROJECT
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.receipts import INSTALL_SQL

LEGACY_INSTALL='''CREATE SCHEMA IF NOT EXISTS qf_recovery;
CREATE TABLE IF NOT EXISTS qf_recovery.receipts (
job_id uuid PRIMARY KEY,
request_sha256 text NOT NULL CHECK (length(request_sha256)=64),
result jsonb NOT NULL,
committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);'''


def main():
    label=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',CONTAINER,text=True).stdout.strip()
    if CONTAINER!='queryfoundry-database-1' or label!=PROJECT:
        raise RuntimeError('Unsupported lab')
    database='qf_install_probe_'+uuid.uuid4().hex
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Linux PG installation only, Windows Paramiko SSH clients; no expansion or version-finalization coverage',
            'postgresql_version':sql('SELECT version();','postgres'),
            'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'source_files_modified_during_run':True,
            'tested_source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                for name in ('dbperf/receipts.py','dbperf/durable_service.py','scripts/research_install_race.py')},
            'official_database_writes':False,'clients_per_round':6,'rounds_per_variant':10,'variants':{},'passed':False}
    output=ROOT/'reports/install-race-linux-ssh.json'
    sql('CREATE DATABASE '+database+' OWNER challenge;','postgres')
    try:
        for name,installation in [('legacy',LEGACY_INSTALL),('transaction_locked',INSTALL_SQL)]:
            rounds=[]
            for number in range(10):
                sql('DROP SCHEMA IF EXISTS qf_recovery CASCADE;',database)
                barrier=threading.Barrier(6)
                def install():
                    service=DurableQueryFoundryService()
                    try:
                        connect(service)
                        barrier.wait(timeout=30)
                        service._command(database,installation)
                        return {'ok':True}
                    except Exception as error:
                        return {'ok':False,'error_type':type(error).__name__,
                                'namespace_conflict':'pg_namespace_nspname_index' in str(error),
                                'catalog_conflict':'duplicate key' in str(error) or 'already exists' in str(error)}
                    finally:
                        service.close()
                started=time.monotonic()
                with ThreadPoolExecutor(max_workers=6) as pool:
                    results=list(pool.map(lambda _:install(),range(6)))
                # A retry must work even after a fresh-install conflict.
                service=DurableQueryFoundryService()
                try:
                    connect(service); service._command(database,installation)
                finally: service.close()
                state=json.loads(sql("SELECT json_build_object('receipt_rows',(SELECT count(*) FROM qf_recovery.receipts),'tables',(SELECT count(*) FROM pg_tables WHERE schemaname='qf_recovery'),'advisory_locks',(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND database=(SELECT oid FROM pg_database WHERE datname=current_database())));",database))
                assert state=={'receipt_rows':0,'tables':1,'advisory_locks':0},state
                rounds.append({'round':number,'clients':results,'state':state,'retry_succeeded':True,'seconds':time.monotonic()-started})
                report['variants'][name]={'rounds':rounds,'failed_clients':sum(not r['ok'] for row in rounds for r in row['clients'])}
                output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                print(name,number,'failed',sum(not r['ok'] for r in results),flush=True)
            if name=='transaction_locked':
                assert report['variants'][name]['failed_clients']==0,'Locked installation had failures'
        report['passed']=True
    finally:
        # The only drop target was created above, with a fixed prefix and UUID.
        assert database.startswith('qf_install_probe_') and database[17:].isalnum()
        sql('DROP DATABASE '+database+' WITH (FORCE);','postgres')
        report['disposable_database_removed']=True
        output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
