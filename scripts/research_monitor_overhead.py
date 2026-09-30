"""Four paired full-GUI trials, balanced sampler on/off; official 100k lab only."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app'),str(ROOT/'scripts')]
from benchmark_gui import sql, CONTAINER
from benchmark import canonical
from dbperf.recipes import ORDER


def main():
    if CONTAINER!='queryfoundry-database-1': raise RuntimeError('100k laboratory required')
    if sql('SELECT count(*) FROM public.raw_data;')!='100000': raise RuntimeError('Unexpected official source scale')
    if sql("SELECT to_regclass('qf_reference.table1') IS NOT NULL;")!='t': raise RuntimeError('Original reference missing')
    report=ROOT/'reports/research-disk-100k/durable-gui'
    report.mkdir(parents=True,exist_ok=True)
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'mode':'lazy_keys',
              'scope':'full unchanged Windows GUI worker, Linux SSH, 100000 official rows, recovery enabled',
              'paired_rounds':4,'records':[],'passed':False,'official_source_modified':False,
              'global_intermediate_disk_peak_bytes':None,
              'base_git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'tested_source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('scripts/benchmark_gui.py','scripts/resource_sampling.py','scripts/research_monitor_overhead.py')}}
    output=report.parent/'monitor-overhead.json'
    env={**os.environ,'QF_REPORT_SUBDIR':'research-disk-100k'}
    for pair in range(4):
        for enabled in ((True,False) if pair%2==0 else (False,True)):
            number=pair*2+(0 if enabled else 1)
            command=[sys.executable,str(ROOT/'scripts/benchmark_gui.py'),'--single-mode','lazy_keys','--single-number',str(number),'--durable']
            if not enabled: command.append('--no-resource-monitor')
            subprocess.run(command,cwd=ROOT,env=env,check=True)
            record=json.loads((report/f'gui-lazy_keys-{number}.json').read_text())
            for table in ORDER:
                query=canonical(table)
                count=sql('SELECT count(*) FROM (('+query+' EXCEPT ALL SELECT value FROM qf_reference.'+table+') UNION ALL (SELECT value FROM qf_reference.'+table+' EXCEPT ALL '+query+')) differences;')
                if count!='0': raise RuntimeError('Output mismatch: '+table)
            record['six_table_multiset_equivalence']=True
            record['overhead_pair']=pair
            (report/f'gui-lazy_keys-{number}.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
            evidence['records'].append({'pair':pair,'monitor_enabled':enabled,'seconds':record['measured_processing_total_seconds'],'report':f'durable-gui/gui-lazy_keys-{number}.json','six_table_multiset_equivalence':True,'disk_sampling':record['disk_sampling']})
            output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    by_mode={state:[r['seconds'] for r in evidence['records'] if r['monitor_enabled']==state] for state in (True,False)}
    deltas=[]
    for pair in range(4):
        times={r['monitor_enabled']:r['seconds'] for r in evidence['records'] if r['pair']==pair}
        deltas.append(100*(times[True]/times[False]-1))
    evidence.update(passed=True,median_seconds={'monitor_on':statistics.median(by_mode[True]),'monitor_off':statistics.median(by_mode[False])},paired_time_change_percent=deltas,median_paired_time_change_percent=statistics.median(deltas),limitations=['Four paired rounds on one machine, exploratory; not a significance test.','Measures the whole resource monitor, including memory sampling and Docker calls.','Sampled ephemeral space excludes persistent metadata, client caches/staging and WAL.'])
    output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:evidence[key] for key in ('passed','median_seconds','paired_time_change_percent','median_paired_time_change_percent')}),flush=True)


if __name__=='__main__': main()
