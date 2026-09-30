"""Real Linux/SSH recovery checks against the disposable labelled lab only."""
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import connect, reset_destinations, sql
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import destinations, ORDER

class FaultService(DurableQueryFoundryService):
    fault=None
    def run_remote_command(self,command,**kwargs):
        context=getattr(self,'_qf_context',None)
        text=kwargs.get('stdin_text') or ''
        targeted=context and 'pgdm_expansion_timings' in text and (
            (context['kind']=='data' and 'BEGIN ISOLATION LEVEL REPEATABLE READ;' in text) or
            (context['kind']=='versions' and 'Commit atomic version batch' in text))
        if targeted and self.fault=='rollback':
            self.fault=None
            kwargs['stdin_text']=text.replace('COMMIT;', 'SELECT 1/0;\nCOMMIT;',1)
        output=super().run_remote_command(command,**kwargs)
        if targeted and self.fault=='lost_reply':
            self.fault=None
            raise ConnectionError('Injected lost SSH reply after confirmed command')
        return output

def expand(service):
    return service.execute_cross_table_expansion('kaggle_challenge','public.raw_data',['group001'],destinations('baseline'))

def counts():
    return {t:int(sql('SELECT count(*) FROM public.'+t)) for t in ORDER}

def main():
    reset_destinations()
    os.environ['QF_RECIPE_MODE']='payload_once'
    os.environ.pop('QF_JOB_ID',None)
    service=FaultService(); connect(service)
    evidence={}
    try:
        service.ensure_admin_schema()
        service.fault='rollback'
        try: expand(service)
        except Exception: pass
        else: raise AssertionError('Precommit fault was not raised')
        job=service._qf_pending_job['job']
        assert not any(counts().values())
        assert sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"'")=='0'
        evidence['precommit_failure_rolls_back_rows_and_receipt']=True
        service.fault='lost_reply'
        result=expand(service)
        committed=counts(); assert committed['table1']==100000
        assert sql("SELECT count(*) FROM qf_recovery.receipts WHERE job_id='"+job+"'")=='1'
        evidence['lost_data_reply_recovers_committed_result']=True
        retry=expand(service)
        assert retry['qf_job_id']==job and counts()==committed
        assert retry['timing_report']['total_rows_inserted']==sum(committed.values())
        evidence['same_service_retry_reuses_job_without_env_override']=True
    finally: service.close()
    # A new SSH connection represents restart in the gap before version commit.
    service=FaultService(); connect(service)
    os.environ['QF_JOB_ID']=job
    try:
        result=expand(service)
        assert counts()==committed
        evidence['restart_before_versions_does_not_repeat_data']=True
        service.fault='lost_reply'
        versions=service.register_cross_table_expansion_versions(result,'QueryFoundry recovery test','lab')
        assert len(versions)==6
        again=service.register_cross_table_expansion_versions(result,'QueryFoundry recovery test','lab')
        assert sorted(again,key=lambda v:v['table_name'])==sorted(versions,key=lambda v:v['table_name'])
        evidence['lost_version_reply_and_retry_keep_same_six_versions']=True
        changed=destinations('baseline'); changed[0]['version_title']='different request'
        try: service.execute_cross_table_expansion('kaggle_challenge','public.raw_data',['group001'],changed)
        except RuntimeError as exc:
            assert 'reused' in str(exc)
        else: raise AssertionError('Changed request accepted')
        evidence['changed_request_same_job_rejected']=True
        evidence['row_counts']=counts()
        evidence['job_id']=job
        evidence['scope']='Windows service through SSH to Linux PostgreSQL; two durable database transactions'
        (ROOT/'reports/durable-recovery.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(evidence,indent=2))
    finally:
        service.close(); os.environ.pop('QF_JOB_ID',None)

if __name__=='__main__': main()
