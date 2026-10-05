"""Real Paramiko transport cuts, 64-row disposable clones; no benchmark claims."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import connect, sql as lab_sql, docker, CONTAINER, PROJECT
from benchmark import canonical
from config import CONTROL_DB
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import destinations, ORDER
from research_source_mutation import source_digest, memory_floor


def sql(statement,database='kaggle_challenge'):
    # Observation/cleanup connections can encounter PostgreSQL's automatic
    # crash recovery immediately after a transport cut. Never retry service DML.
    deadline=time.monotonic()+30
    while True:
        try: return lab_sql(statement,database)
        except subprocess.CalledProcessError:
            if time.monotonic()>=deadline: raise
            time.sleep(.5)


def main():
    memory_floor(2)
    if PROJECT!='queryfoundry': raise RuntimeError('Main lab only')
    label=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',CONTAINER,text=True).stdout.strip()
    if label!=PROJECT: raise RuntimeError('Lab ownership mismatch')
    tag=uuid.uuid4().hex[:12]
    routes={'kaggle_challenge':'qf_ssh_data_'+tag,CONTROL_DB:'qf_ssh_control_'+tag}
    data,control=routes.values()
    created=[]
    previous={key:os.environ.get(key) for key in ('QF_JOB_ID','QF_RECIPE_MODE')}
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'scope':'64-row functional clones, actual Windows Paramiko transport closed; Linux PG',
            'benchmark':False,'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'cases':{},'passed':False,
            'source_generator_modified':False,'clone_databases':routes}
    output=ROOT/'reports/ssh-disconnect-final.json'
    original=source_digest('kaggle_challenge')
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

    class CloneService(DurableQueryFoundryService):
        failpoint=None
        def _command(self,database,statement):
            if database not in routes: raise RuntimeError('Unexpected receipt database')
            return super()._command(routes[database],statement)
        def run_remote_command(self,command,**kwargs):
            for original_name,clone in routes.items():
                command=re.sub(r'(?<=-d )'+re.escape(original_name)+r'(?=\s|$)',clone,command)
            if re.search(r'-d\s+[\"\']?(?:kaggle_challenge|'+re.escape(CONTROL_DB)+r')[\"\']?(?=\s|$)',command):
                raise RuntimeError('Original target refused')
            text=kwargs.get('stdin_text') or ''
            kind=(getattr(self,'_qf_context',None) or {}).get('kind')
            pause='SELECT pg_sleep(8) /* qf_real_ssh_cut */;'
            if self.failpoint=='data_before_commit' and kind=='data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text:
                text=text.replace('BEGIN ISOLATION LEVEL REPEATABLE READ;','BEGIN ISOLATION LEVEL REPEATABLE READ;\n'+pause,1)
            elif self.failpoint=='data_after_commit' and kind=='data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text:
                text=text.replace('COMMIT;','COMMIT;\n'+pause,1)
            elif self.failpoint=='versions_after_commit' and kind=='versions' and 'Commit atomic version batch' in text:
                text=text.replace('COMMIT;','COMMIT;\n'+pause,1)
            kwargs['stdin_text']=text
            return super().run_remote_command(command,**kwargs)

    def service():
        value=CloneService(); connect(value); return value
    def expand(value):
        return value.execute_cross_table_expansion('kaggle_challenge','public.raw_data',['group001'],destinations('baseline'))
    def register(value,result):
        return value.register_cross_table_expansion_versions(result,'QueryFoundry final SSH gate','isolated-lab')
    def receipt_count(database,job):
        return int(sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"';",database))
    def wait_sleep(database):
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            found=sql("SELECT count(*) FROM pg_stat_activity WHERE datname='"+database+"' AND wait_event='PgSleep';",'postgres')
            if int(found): return
            time.sleep(.2)
        raise RuntimeError('Failpoint not reached')
    def wait_no_session(database):
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if sql("SELECT count(*) FROM pg_stat_activity WHERE datname='"+database+"';",'postgres')=='0': return
            time.sleep(.2)
        raise RuntimeError('Remote session did not finish after SSH cut')
    try:
        directory=ROOT/'.runtime/ssh-disconnect'/tag; directory.mkdir(parents=True)
        for original_name,clone in routes.items():
            archive=directory/(clone+'.dump')
            args=['docker','exec','-u','postgres',CONTAINER,'pg_dump','-Fc','-d',original_name]
            if original_name=='kaggle_challenge': args+=['--schema-only','--schema=public']
            with archive.open('wb') as stream: subprocess.run(args,stdout=stream,check=True)
            docker('exec','-u','postgres',CONTAINER,'createdb','-O','challenge',clone); created.append(clone)
            sql('DROP SCHEMA public;',clone)
            with archive.open('rb') as stream:
                subprocess.run(['docker','exec','-i','-u','postgres',CONTAINER,'pg_restore','--exit-on-error','-d',clone],stdin=stream,check=True)
        raw=docker('exec','-u','postgres',CONTAINER,'psql','-X','-qAt','-d','kaggle_challenge','-c','COPY (SELECT * FROM public.raw_data ORDER BY raw_id LIMIT 64) TO STDOUT WITH (FORMAT csv);').stdout
        docker('exec','-i','-u','postgres',CONTAINER,'psql','-X','-qAt','-d',data,'-c','COPY public.raw_data FROM STDIN WITH (FORMAT csv);',input=raw)
        os.environ['QF_RECIPE_MODE']='baseline'; os.environ['QF_JOB_ID']=str(uuid.uuid4())
        value=service()
        try: expand(value)
        finally: value.close()
        sql('CREATE SCHEMA qf_ssh_reference; '+' '.join('CREATE TABLE qf_ssh_reference.'+table+' AS '+canonical(table)+';' for table in ORDER),data)
        expected={table:int(sql('SELECT count(*) FROM public.'+table,data)) for table in ORDER}
        report['original_expected_rows']=expected
        for case in ('data_before_commit','data_after_commit','versions_after_commit'):
            memory_floor()
            sql('TRUNCATE '+','.join('public.'+t for t in ORDER)+' RESTART IDENTITY; '
                "DO $reset$ BEGIN IF to_regclass('public.pgdm_table_row_counts') IS NOT NULL THEN UPDATE public.pgdm_table_row_counts SET row_count=0 WHERE table_name IN ("+
                ','.join("'"+t+"'" for t in ORDER)+'); END IF; END $reset$;',data)
            job=str(uuid.uuid4()); os.environ['QF_JOB_ID']=job; os.environ['QF_RECIPE_MODE']='payload_once'
            value=service(); value.failpoint=case
            result=None
            if case=='versions_after_commit': result=expand(value)
            before_versions=int(sql('SELECT count(*) FROM app_control.table_versions;',control))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(register,value,result) if result else pool.submit(expand,value)
                try:
                    wait_sleep(control if result else data)
                    value.ssh_client.get_transport().close()
                    try:
                        future.result(timeout=30); raised=False; error_type=None
                    except Exception as error:
                        raised=True; error_type=type(error).__name__
                finally: value.close()
            wait_no_session(control if result else data)
            committed=receipt_count(data,job)
            rows={table:int(sql('SELECT count(*) FROM public.'+table,data)) for table in ORDER}
            assert rows==(expected if committed else dict.fromkeys(ORDER,0)),(case,rows,committed)
            if case=='data_after_commit': assert committed==1
            if case=='versions_after_commit': assert receipt_count(control,job)==1
            # New service/new SSH connection, same persisted job UUID.
            resumed=service()
            try:
                recovered=expand(resumed)
                versions=register(resumed,recovered)
                again=register(resumed,recovered)
            finally: resumed.close()
            assert len(versions)==6 and versions==again
            assert receipt_count(data,job)==1 and receipt_count(control,job)==1
            assert int(sql('SELECT count(*) FROM app_control.table_versions;',control))-before_versions==6
            for table in ORDER:
                query=canonical(table)
                assert sql('SELECT count(*) FROM (('+query+' EXCEPT ALL SELECT value FROM qf_ssh_reference.'+table+') UNION ALL (SELECT value FROM qf_ssh_reference.'+table+' EXCEPT ALL '+query+')) delta;',data)=='0'
            locks=int(sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND database IN (SELECT oid FROM pg_database WHERE datname IN ('"+data+"','"+control+"'));",'postgres'))
            assert locks==0
            report['cases'][case]={'transport_closed':True,'interrupted_call_raised':raised,'error_type':error_type,
                'data_receipt_after_disconnect':committed,'rows_after_disconnect':rows,'same_job_new_connection_recovered':True,
                'six_table_multiset_equivalence':True,'data_receipts':1,'control_receipts':1,'version_records_added':6,'repeat_finalization_same_versions':True,'residual_advisory_locks':locks}
            output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            print(case,'PASS',flush=True)
        report['passed']=True
    finally:
        for clone in reversed(created): sql('DROP DATABASE IF EXISTS '+clone+' WITH (FORCE);','postgres')
        report['clones_removed']=True
        report['official_source_unchanged']=source_digest('kaggle_challenge')==original
        output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        for key,old in previous.items():
            if old is None: os.environ.pop(key,None)
            else: os.environ[key]=old
    assert report['official_source_unchanged']


if __name__=='__main__': main()
