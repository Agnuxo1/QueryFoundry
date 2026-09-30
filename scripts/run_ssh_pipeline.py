"""Execute upstream processing and versioning over SSH, producing its report.

Use a freshly generated disposable challenge database. GUI refresh is excluded;
this artifact must not be confused with the final GUI report required by Kaggle.
Secrets are read from the process environment, never printed or written.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app')]
import paramiko
from services.postgres_service import PostgresAdminService
from dbperf.service import QueryFoundryService
from dbperf.recipes import destinations, MODES
from ui.dialogs import ExpansionTimingReportDialog

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=MODES,default='baseline')
    parser.add_argument('--known-hosts',type=Path,required=True)
    args=parser.parse_args()
    names=('QF_SSH_HOST','QF_SSH_USER','QF_PG_USER','QF_PG_PASSWORD')
    missing=[n for n in names if not os.environ.get(n)]
    if missing: parser.error('Missing environment fields: '+', '.join(missing))
    service=QueryFoundryService()
    # Honor the user's existing trusted SSH host keys and key/agent credentials.
    client=paramiko.SSHClient()
    client.load_host_keys(str(args.known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(hostname=os.environ['QF_SSH_HOST'],port=int(os.environ.get('QF_SSH_PORT','22')),
        username=os.environ['QF_SSH_USER'],password=os.environ.get('QF_SSH_PASSWORD'),
        key_filename=os.environ.get('QF_SSH_KEY'),look_for_keys=True,allow_agent=True,timeout=15)
    service.ssh_client=client
    service.ssh_host=os.environ['QF_SSH_HOST']
    service.ssh_port=int(os.environ.get('QF_SSH_PORT','22'))
    service.ssh_username=os.environ['QF_SSH_USER']
    service.postgres_port=int(os.environ.get('QF_PG_PORT','5432'))
    service.sql_username=os.environ['QF_PG_USER']
    service.sql_password=os.environ['QF_PG_PASSWORD']
    os.environ['QF_RECIPE_MODE']='baseline' # Recipes below are already explicitly compiled.
    report=PostgresAdminService.create_cross_table_expansion_timing_report(
        'kaggle_challenge','public.raw_data',['group001'],[x['table_name'] for x in destinations(args.mode)])
    try:
        with service.expansion_timing_scope(report,'prepare','Ensure control database metadata'):
            service.ensure_admin_schema()
        # Refuse to append into previous results, and never reset a remote DB.
        for dest in destinations():
            if service.get_table_row_count('kaggle_challenge',dest['table_name']) != 0:
                raise RuntimeError('Remote benchmark needs empty official destinations')
        result=service.execute_cross_table_expansion('kaggle_challenge','public.raw_data',['group001'],destinations(args.mode),timing_report=report)
        versions=service.register_cross_table_expansion_versions(result,'QueryFoundry benchmark',socket.gethostname(),timing_report=report)
        if len(versions)!=6: raise RuntimeError('Expected six destination version records')
        report['completed_at']=datetime.now().isoformat()
        report['benchmark_scope']='upstream SSH service processing + versions; GUI refresh excluded'
        report['official_gui_parity']=False
        output=ROOT/'reports'/('ssh-'+args.mode)
        output.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        output.with_suffix('.txt').write_text(ExpansionTimingReportDialog.build_report_text(report),encoding='utf-8')
        print('SSH processing complete. Six versions registered. Reports saved under reports/.')
    finally:
        service.close()

if __name__=='__main__': main()
