"""Integration test of the unchanged Windows GUI expansion worker over Linux SSH.

Only laboratory destinations are reset. Source data and generator stay intact.
The original GUI worker performs preparation, preflight, managed DML, manifests,
version registration, filter-cache invalidation and navigation refresh.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
os.environ['PDM_LOCAL_STORAGE_DIR']=str(ROOT/'.runtime/gui-storage')
import paramiko
from main import App
from filter.facet_service import FilterFacetService
from services.postgres_service import PostgresAdminService
from dbperf.service import QueryFoundryService
from dbperf.durable_service import DurableQueryFoundryService
from dbperf.recipes import destinations, ORDER, MODES
from ui.dialogs import ExpansionTimingReportDialog
from benchmark import canonical

CONTAINER=os.environ.get('QF_LAB_CONTAINER','queryfoundry-database-1')
PROJECT={'queryfoundry-database-1':'queryfoundry','queryfoundry-scale-database-1':'queryfoundry-scale'}.get(CONTAINER)
if PROJECT is None: raise RuntimeError('Unsupported laboratory container')
REPORTS=ROOT/'reports'/os.environ.get('QF_REPORT_SUBDIR','')
DURABLE=False
CANDIDATE='payload_once'
COMPARISON_MODES=('baseline','payload_once')
def docker(*args, **kw):
    return subprocess.run(['docker',*args],check=True,capture_output=True,**kw)

def sql(statement,database='kaggle_challenge'):
    return docker('exec','-i','-u','postgres',CONTAINER,'psql','-X','-qAt','-v','ON_ERROR_STOP=1',
        '-d',database,'-f','-',input=statement,text=True,encoding='utf-8').stdout.strip()

def connect(service):
    values=dict(line.split('=',1) for line in (ROOT/'.runtime/docker.env').read_text().splitlines() if '=' in line)
    client=paramiko.SSHClient(); client.load_host_keys(str(ROOT/'.runtime'/os.environ.get('QF_KNOWN_HOSTS','known_hosts')))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    port=int(os.environ.get('QF_SSH_PORT','55222'))
    client.connect('127.0.0.1',port=port,username='qf',key_filename=str(ROOT/'.runtime/ssh-key'),look_for_keys=False,allow_agent=False)
    service.ssh_client=client; service.ssh_host='127.0.0.1'; service.ssh_port=port; service.ssh_username='qf'
    service.postgres_port=5432; service.sql_username='challenge'; service.sql_password=values['QF_PG_PASSWORD']

def reset_destinations():
    label=docker('inspect','--format','{{index .Config.Labels "com.docker.compose.project"}}',CONTAINER,text=True).stdout.strip()
    if label!=PROJECT: raise RuntimeError('Refusing to reset a non-laboratory container')
    names=','.join("'"+t+"'" for t in ORDER)
    sql('BEGIN; TRUNCATE '+','.join('public.'+t for t in ORDER)+' RESTART IDENTITY; '
        "DO $qf_reset$ BEGIN IF to_regclass('public.pgdm_table_row_counts') IS NOT NULL THEN "
        'UPDATE public.pgdm_table_row_counts SET row_count=0,updated_at=clock_timestamp() '
        'WHERE schema_name=\'public\' AND table_name IN ('+names+'); '
        'END IF; END $qf_reset$; COMMIT;')

def run_once(mode,number):
    reset_destinations()
    os.environ['QF_RECIPE_MODE']=mode
    app=App()
    app.withdraw()
    # Real widget/navigation refresh executes, while avoiding unsolicited windows.
    service=PostgresAdminService() if mode=='baseline' else (DurableQueryFoundryService() if DURABLE else QueryFoundryService())
    connect(service)
    app.service=service
    app.facet_service=FilterFacetService(service)
    app.current_db='kaggle_challenge'
    app.selected_table_name='public.raw_data'
    app.begin_cross_table_expansion_cancellation_scope()
    original_complete=app.complete_cross_table_expansion_progress
    result={}
    errors=[]
    stop=threading.Event()
    samples=[]
    temporary_samples=[]
    temp_before=int(sql("SELECT temp_bytes FROM pg_stat_database WHERE datname='kaggle_challenge';"))
    def monitor():
        while not stop.is_set():
            try:
                raw=docker('exec',CONTAINER,'sh','-c',"cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.stat; echo QF_TEMP; find /var/lib/postgresql/data/base -path '*/pgsql_tmp/*' -type f -printf '%s\\n'",text=True).stdout.splitlines()
                boundary=raw.index('QF_TEMP')
                stats=dict(line.split() for line in raw[1:boundary])
                samples.append(max(0,int(raw[0])-int(stats.get('inactive_file',0))))
                temporary_samples.append(sum(int(size) for size in raw[boundary+1:]))
            except (subprocess.SubprocessError,ValueError,KeyError): pass
            stop.wait(.5)
    watcher=threading.Thread(target=monitor,daemon=True)
    watcher.start()
    def complete(message,report=None):
        result.update(report or {})
        original_complete(message,report)
        # Allow the genuine report dialog to be built by the original callback.
        app.after(1200,app.quit)
    def show_error(title,message):
        errors.append(title)
        app.after(1,app.quit)
    app.complete_cross_table_expansion_progress=complete
    app.show_error=show_error
    request={'requested_by':'QueryFoundry laboratory','raw_schemas':['group001'],'destinations':destinations('baseline')}
    worker=threading.Thread(target=app._execute_cross_table_expansion_thread,
        args=('kaggle_challenge','public.raw_data',request),daemon=True)
    app.after(20,worker.start)
    app.after(600000,app.quit)
    try:
        app.mainloop()
        worker.join(timeout=10)
        if errors or not result or worker.is_alive(): raise RuntimeError('GUI pipeline failed; no result accepted')
        result['benchmark_scope']='unchanged Windows GUI worker with Linux PostgreSQL over SSH'
        result['local_environment_parity']=True
        result['organizer_score']=None
        result['sampled_peak_container_working_set_bytes']=max(samples) if samples else None
        result['resource_sample_count']=len(samples)
        result['resource_method']='cgroup memory.current minus inactive_file; 0.5s requested interval; container includes PostgreSQL and SSH'
        result['temporary_disk_peak_bytes']=None
        result['sampled_peak_postgresql_temporary_files_bytes']=max(temporary_samples) if temporary_samples else None
        result['postgresql_temporary_bytes_written']=max(0,int(sql("SELECT temp_bytes FROM pg_stat_database WHERE datname='kaggle_challenge';"))-temp_before)
        result['lab_container']=CONTAINER
        result['git_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        result['mode']=mode; result['repeat']=number
        total=sum(float(x.get('seconds') or 0) for x in result.get('items',[]) if x.get('include_in_total',True))
        result['measured_processing_total_seconds']=total
        path=REPORTS/f'gui-{mode}-{number}'
        path.with_suffix('.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        path.with_suffix('.txt').write_text(ExpansionTimingReportDialog.build_report_text(result),encoding='utf-8')
        print(mode,number,round(total,4),'s full GUI metric',flush=True)
        return result
    finally:
        stop.set(); watcher.join(timeout=5)
        service.close()
        app.destroy()

def rendered_total_seconds(text):
    match=re.search(r'MEASURED PROCESSING TOTAL: (?:(\d+)m\s+)?([0-9.]+)\s*(us|ms|s)',text)
    if not match: raise RuntimeError('Missing original rendered total')
    return 60*int(match[1] or 0)+float(match[2])*{'us':.000001,'ms':.001,'s':1}[match[3]]

def summarize(records,repeats):
    # Item seconds are the upstream report schema; re-read its rendered total as
    # an independent check against accidental summary/schema drift.
    for record in records:
        total=sum(float(x.get('seconds') or 0) for x in record.get('items',[]) if x.get('include_in_total',True))
        rendered=ExpansionTimingReportDialog.build_report_text(record)
        if abs(rendered_total_seconds(rendered)-total)>.0006:
            raise RuntimeError('Report total and summary disagree')
        record['measured_processing_total_seconds']=total
    medians={m:statistics.median(r['measured_processing_total_seconds'] for r in records if r['mode']==m) for m in COMPARISON_MODES}
    summary={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'source_rows':int(sql('SELECT count(*) FROM public.raw_data;')),
        'repeats':repeats,'median_seconds':medians,'speedup':medians['baseline']/medians[CANDIDATE],
        'local_full_gui_path':True,'six_table_multiset_equivalence':True,'organizer_score':None,
        'container_memory_limit_bytes':2147483648,'container_cpu_limit':2,
        'sampled_peak_working_set_bytes':{m:max(r['sampled_peak_container_working_set_bytes'] or 0 for r in records if r['mode']==m) for m in medians},
        'peak_intermediate_disk_bytes':None,'recovery_gui_integrated':DURABLE,'submission_ready':False}
    summary['baseline_recovery_enabled']=False
    summary['candidate_recovery_enabled']=DURABLE
    summary['speedup_by_mode']={m:medians['baseline']/medians[m] for m in medians if m!='baseline'}
    summary['comparison_modes']=list(COMPARISON_MODES)
    for record in records:
        (REPORTS/f"gui-{record['mode']}-{record['repeat']}.json").write_text(json.dumps(record,indent=2),encoding='utf-8')
    (REPORTS/'gui-runs.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    (REPORTS/'gui-benchmark.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

def main():
    global REPORTS,DURABLE,CANDIDATE,COMPARISON_MODES
    parser=argparse.ArgumentParser(); parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--summarize-existing',action='store_true')
    parser.add_argument('--durable',action='store_true')
    parser.add_argument('--single-mode',choices=MODES)
    parser.add_argument('--candidate-mode',choices=[m for m in MODES if m!='baseline'],default='payload_once')
    parser.add_argument('--compare-mode',action='append',choices=[m for m in MODES if m!='baseline'],help='Compare multiple candidates in balanced rotating order')
    parser.add_argument('--single-number',type=int,default=0)
    args=parser.parse_args()
    DURABLE=args.durable
    CANDIDATE=args.candidate_mode
    COMPARISON_MODES=tuple(['baseline']+list(dict.fromkeys(args.compare_mode or [CANDIDATE])))
    if CANDIDATE not in COMPARISON_MODES: CANDIDATE=COMPARISON_MODES[-1]
    if DURABLE: REPORTS=REPORTS/'durable-gui'
    REPORTS.mkdir(parents=True,exist_ok=True)
    if args.single_mode:
        run_once(args.single_mode,args.single_number)
        return
    if not 1<=args.repeats<=5: parser.error('repeats must be 1..5')
    if args.summarize_existing:
        records=json.loads((REPORTS/'gui-runs.json').read_text())
        if len(records)!=len(COMPARISON_MODES)*args.repeats: raise RuntimeError('Incomplete existing runs')
        summarize(records,args.repeats)
        return
    records=[]
    reference_exists=sql("SELECT to_regclass('qf_reference.table1') IS NOT NULL;")=='t'
    for repeat in range(args.repeats):
        offset=repeat%len(COMPARISON_MODES)
        modes=COMPARISON_MODES[offset:]+COMPARISON_MODES[:offset]
        for mode in modes:
            # A fresh Tcl interpreter per run also models an ordinary GUI launch.
            command=[sys.executable,str(Path(__file__).resolve()),'--single-mode',mode,
                '--single-number',str(repeat)]
            if DURABLE: command.append('--durable')
            subprocess.run(command,check=True)
            record=json.loads((REPORTS/f'gui-{mode}-{repeat}.json').read_text())
            if not reference_exists:
                if mode!='baseline': raise RuntimeError('Original GUI baseline must run first')
                sql('CREATE SCHEMA IF NOT EXISTS qf_reference;\n'+'\n'.join('CREATE TABLE qf_reference.'+t+' AS '+canonical(t)+';' for t in ORDER))
                reference_exists=True
            for table in ORDER:
                query=canonical(table)
                if sql('SELECT count(*) FROM (('+query+' EXCEPT ALL SELECT value FROM qf_reference.'+table+') UNION ALL (SELECT value FROM qf_reference.'+table+' EXCEPT ALL '+query+')) differences;')!='0':
                    raise RuntimeError('Relational mismatch: '+table)
            record['six_table_multiset_equivalence']=True
            record['benchmark_role']='comparison'
            records.append(record)
            (REPORTS/'gui-runs.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    summarize(records,args.repeats)

if __name__=='__main__': main()
