"""Separate official-input scale lab; never replace the preserved 100k volume."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
CONTAINER='queryfoundry-scale-database-1'
def compose(*args,**kw):
    return subprocess.run(['docker','compose','--env-file',str(RUNTIME/'docker.env'),
        '-f',str(ROOT/'compose.scale.yaml'),*args],cwd=ROOT,check=True,**kw)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['start','generate','benchmark','stop'])
    parser.add_argument('--rows',type=int,default=1000000); parser.add_argument('--repeats',type=int,default=1)
    args=parser.parse_args()
    if not 100000<=args.rows<=1000000: parser.error('Initial research gate supports 100k..1M only')
    if args.action=='stop': compose('stop'); return
    if args.action=='start':
        running=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
        if 'queryfoundry-database-1' in running: raise RuntimeError('Release the 100k laboratory reservation first')
        compose('up','-d','--wait','--wait-timeout','120')
        pub=compose('exec','-T','database','cat','/etc/ssh/ssh_host_ed25519_key.pub',capture_output=True,text=True).stdout.split()
        (RUNTIME/'scale-known_hosts').write_text('[127.0.0.1]:55223 '+pub[0]+' '+pub[1]+'\n',encoding='utf-8')
        (RUNTIME/'scale-ssh_config').write_text('Host qf-scale\n  HostName 127.0.0.1\n  Port 55223\n  User qf\n  IdentityFile '+str(RUNTIME/'ssh-key').replace('\\','/')+'\n  UserKnownHostsFile '+str(RUNTIME/'scale-known_hosts').replace('\\','/')+'\n  StrictHostKeyChecking yes\n',encoding='utf-8')
        (RUNTIME/'scale-ssh.cmd').write_text('@echo off\r\n"C:\\Windows\\System32\\OpenSSH\\ssh.exe" -F "'+str(RUNTIME/'scale-ssh_config')+'" %*\r\n',encoding='utf-8')
        print('Separate scale volume ready; 100k laboratory preserved.',flush=True); return
    values=dict(line.split('=',1) for line in (RUNTIME/'docker.env').read_text().splitlines() if '=' in line)
    env={**os.environ,'PG_KAGGLE_CHALLENGE_PASS':values['QF_PG_PASSWORD'],
        'QF_LAB_CONTAINER':CONTAINER,'QF_KNOWN_HOSTS':'scale-known_hosts','QF_SSH_PORT':'55223',
        'QF_REPORT_SUBDIR':'scale-'+str(args.rows)}
    if args.action=='generate':
        started=time.monotonic()
        with (RUNTIME/'scale-generation.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,'-X','utf8',str(ROOT/'app/generate_database.py'),
                '--rows',str(args.rows),'--host','qf-scale','--ssh_port','55223','--ssh_user','qf',
                '--user','challenge','--psql',str(RUNTIME/'pgsql/bin/psql.exe'),
                '--ssh_executable',str(RUNTIME/'scale-ssh.cmd')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        report=ROOT/'reports'/env['QF_REPORT_SUBDIR']; report.mkdir(parents=True,exist_ok=True)
        (report/'generation.json').write_text(json.dumps({'rows':args.rows,'unchanged_official_generator':True,
            'elapsed_wall_seconds':time.monotonic()-started,'container':CONTAINER,'preserved_100k_volume':True},indent=2),encoding='utf-8')
        print('Official scale input generated:',args.rows,flush=True)
    else:
        subprocess.run([sys.executable,str(ROOT/'scripts/benchmark_gui.py'),'--durable','--repeats',str(args.repeats)],
            cwd=ROOT,env=env,check=True)

if __name__=='__main__': main()
