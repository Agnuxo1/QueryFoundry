"""Concurrent receipt replay, restart and two-database dump/restore experiments."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import connect, reset_destinations, sql, docker, CONTAINER
from config import CONTROL_DB
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import destinations, ORDER

def fingerprint(database):
    names=sql("SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1;",database).splitlines()
    result={}
    for name in names:
        if not all(part.replace('_','').isalnum() for part in name.split('.')):
            raise RuntimeError('Unsupported fingerprint identifier')
        value=sql("SELECT json_build_object('rows',count(*),'hash',md5(COALESCE(string_agg(h,'' ORDER BY h),''))) FROM (SELECT md5(to_jsonb(t)::text) h FROM "+name+' t) rows;',database)
        result[name]=json.loads(value)
    return result

def service():
    value=DurableQueryFoundryService(); connect(value); return value

def expand(barrier=None):
    class ObservedService(DurableQueryFoundryService):
        guard_short_circuit=False
        def run_remote_command(self,command,**kwargs):
            text=kwargs.get('stdin_text') or ''
            if barrier and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text:
                barrier.wait(timeout=30)
            try: return super().run_remote_command(command,**kwargs)
            except RuntimeError as exc:
                if 'QueryFoundry job already committed' in str(exc): self.guard_short_circuit=True
                raise
    value=ObservedService(); connect(value)
    try:
        result=value.execute_cross_table_expansion('kaggle_challenge','public.raw_data',['group001'],destinations('baseline'))
        result['qf_duplicate_guard_short_circuit']=value.guard_short_circuit
        return result
    finally: value.close()

def register(result):
    value=service()
    try:
        return value.register_cross_table_expansion_versions(result,'QueryFoundry concurrency research','lab')
    finally: value.close()

def main():
    # This reset verifies the compose project label and preserves official inputs.
    reset_destinations()
    os.environ['QF_RECIPE_MODE']='payload_once'
    job=str(uuid.uuid4()); os.environ['QF_JOB_ID']=job
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'job_id':job,
        'scope':'real Windows SSH services; Linux PG; isolated lab', 'stages':{}}
    output=ROOT/'reports/research-consistency.json'
    def save(): output.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    value=service()
    try: value.ensure_admin_schema()
    finally: value.close()
    restored=[]
    try:
        started=time.monotonic()
        barrier=threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(expand,barrier) for _ in range(2)]
            outcomes=[]; errors=[]
            for future in futures:
                try: outcomes.append(future.result())
                except Exception as exc: errors.append({'type':type(exc).__name__,'message':str(exc)[:800]})
        evidence['stages']['concurrent_data']={'successful_clients':len(outcomes),'errors':errors,
            'seconds':time.monotonic()-started,'receipts':int(sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"'")),
            'rows':{t:int(sql('SELECT count(*) FROM public.'+t)) for t in ORDER}}
        save(); print('Concurrent data results:',len(outcomes),'successes',len(errors),'errors',flush=True)
        assert len(outcomes)==2 and not errors,'Concurrent clients did not both recover successfully'
        evidence['stages']['concurrent_data']['guard_short_circuit_observed']=any(r['qf_duplicate_guard_short_circuit'] for r in outcomes)
        assert evidence['stages']['concurrent_data']['guard_short_circuit_observed'],'Did not observe safe duplicate guard'
        assert evidence['stages']['concurrent_data']['receipts']==1
        assert evidence['stages']['concurrent_data']['rows']['table1']==100000
        before=fingerprint('kaggle_challenge')
        docker('restart',CONTAINER)
        for _ in range(60):
            try:
                if sql('SELECT 1;')=='1': break
            except subprocess.SubprocessError: time.sleep(.5)
        else: raise RuntimeError('Own lab did not restart')
        replay=expand()
        assert fingerprint('kaggle_challenge')==before
        evidence['stages']['restart_before_versions']={'data_fingerprint_unchanged':True,'resumed_job':replay['qf_job_id']}; save()
        with ThreadPoolExecutor(max_workers=2) as pool:
            versions=list(pool.map(register,[replay,replay]))
        assert versions[0]==versions[1] and len(versions[0])==6
        evidence['stages']['concurrent_versions']={'same_six_versions':True,
            'receipts':int(sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"'",CONTROL_DB))}; save()
        directory=ROOT/'.runtime/research-dumps'/job; directory.mkdir(parents=True)
        dumps=[]
        for label,database in [('data','kaggle_challenge'),('control',CONTROL_DB)]:
            source_fingerprint=fingerprint(database)
            archive=directory/(label+'.dump')
            with archive.open('wb') as stream:
                subprocess.run(['docker','exec','-u','postgres',CONTAINER,'pg_dump','-Fc','-d',database],stdout=stream,check=True)
            restored_db='qf_restore_'+label+'_'+job.replace('-','')[:10]
            owner=sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='"+database+"';",'postgres')
            if not owner.replace('_','').isalnum(): raise RuntimeError('Unsupported database owner')
            docker('exec','-u','postgres',CONTAINER,'createdb','-O',owner,restored_db); restored.append(restored_db)
            with archive.open('rb') as stream:
                subprocess.run(['docker','exec','-i','-u','postgres',CONTAINER,'pg_restore','--exit-on-error','-d',restored_db],stdin=stream,check=True)
            assert fingerprint(restored_db)==source_fingerprint
            dumps.append({'database':database,'restored_database':restored_db,'bytes':archive.stat().st_size,
                'sha256':hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),
                'all_user_tables_counts_and_row_hashes_equal':True,'tables':len(source_fingerprint),'database_owner':owner})
            print('Dump restore verified:',label,flush=True)
        evidence['stages']['dump_restore']=dumps; save()
        # Temporarily swap only lab databases: replay must work under original names.
        swaps=[]
        try:
            for label,database in [('data','kaggle_challenge'),('control',CONTROL_DB)]:
                clone=next(item['restored_database'] for item in dumps if item['database']==database)
                saved='qf_saved_'+label+'_'+job.replace('-','')[:10]
                sql('ALTER DATABASE '+database+' RENAME TO '+saved+';', 'postgres')
                swaps.append((database,clone,saved,False))
                sql('ALTER DATABASE '+clone+' RENAME TO '+database+';', 'postgres')
                swaps[-1]=(database,clone,saved,True)
            replay_restored=expand()
            assert register(replay_restored)==versions[0]
            evidence['stages']['restored_identity_replay']={'same_job_and_six_versions':True,
                'rows_unchanged':fingerprint('kaggle_challenge')==before}; save()
            # Research boundary: receipt replay after manual deletion of one output.
            count_before=int(sql('SELECT count(*) FROM public.table2;'))
            sql('DELETE FROM public.table2 WHERE ctid=(SELECT ctid FROM public.table2 LIMIT 1);')
            try:
                expand(); detected=False
            except RuntimeError: detected=True
            count_after=int(sql('SELECT count(*) FROM public.table2;'))
            evidence['stages']['out_of_band_output_deletion']={'mutation':'delete one table2 row in restored clone',
                'replay_detected_mutation':detected,'rows_before':count_before,'rows_after':count_after,
                'original_data_untouched':True}; save()
            assert detected,'Replay falsely accepted externally deleted output'
        finally:
            for database,clone,saved,switched in reversed(swaps):
                if switched: sql('ALTER DATABASE '+database+' RENAME TO '+clone+';', 'postgres')
                sql('ALTER DATABASE '+saved+' RENAME TO '+database+';', 'postgres')
        evidence['passed']=True; save(); print('Consistency round passed',flush=True)
    except Exception as exc:
        evidence['passed']=False; evidence['failure_type']=type(exc).__name__; evidence['failure']=str(exc)[:1000]; save()
        raise
    finally:
        for name in restored: docker('exec','-u','postgres',CONTAINER,'dropdb',name)
        os.environ.pop('QF_JOB_ID',None)

if __name__=='__main__': main()
