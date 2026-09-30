"""Create an isolated Linux/SSH lab; Docker's existing data disk remains on E:."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
sys.path.insert(0,str(ROOT/'app'))
import paramiko

def run(args, **kw):
    return subprocess.run(args,cwd=ROOT,check=True,**kw)

def compose(*args, **kw):
    return run(['docker','compose','--env-file',str(RUNTIME/'docker.env'),*args],**kw)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','build','start','generate','pipeline','stop'])
    parser.add_argument('--mode',choices=['baseline','payload_once','fields_once'],default='baseline')
    parser.add_argument('--rows',type=int,default=100000)
    args=parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.action=='prepare':
        if not (RUNTIME/'docker.env').exists():
            (RUNTIME/'docker.env').write_text('QF_ADMIN_PASSWORD='+secrets.token_urlsafe(32)+'\nQF_PG_PASSWORD='+secrets.token_urlsafe(32)+'\n',encoding='utf-8')
        if not (RUNTIME/'ssh-key').exists():
            key=paramiko.RSAKey.generate(3072)
            key.write_private_key_file(str(RUNTIME/'ssh-key'))
            (RUNTIME/'ssh-key.pub').write_text(key.get_name()+' '+key.get_base64()+' qf-local-lab\n',encoding='utf-8')
        if os.name=='nt':
            run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-File',str(ROOT/'scripts/restrict_lab_secrets.ps1')])
        print('Local secrets and SSH key prepared under ignored .runtime/.')
        return
    if args.action=='build': compose('build'); return
    if args.action=='start':
        compose('up','-d','--wait', '--wait-timeout','120')
        pub=compose('exec','-T','database','cat','/etc/ssh/ssh_host_ed25519_key.pub',capture_output=True,text=True).stdout.strip().split()
        (RUNTIME/'known_hosts').write_text('[127.0.0.1]:55222 '+pub[0]+' '+pub[1]+'\n',encoding='utf-8')
        (RUNTIME/'ssh_config').write_text('Host qf-challenge\n  HostName 127.0.0.1\n  Port 55222\n  User qf\n  IdentityFile '+str(RUNTIME/'ssh-key').replace('\\','/')+'\n  UserKnownHostsFile '+str(RUNTIME/'known_hosts').replace('\\','/')+'\n  StrictHostKeyChecking yes\n',encoding='utf-8')
        (RUNTIME/'ssh.cmd').write_text('@echo off\r\n"C:\\Windows\\System32\\OpenSSH\\ssh.exe" -F "'+str(RUNTIME/'ssh_config')+'" %*\r\n',encoding='utf-8')
        print('Linux PostgreSQL + trusted SSH ready on loopback. Data lives in Docker volume.')
        return
    if args.action=='stop': compose('stop'); return
    values=dict(line.split('=',1) for line in (RUNTIME/'docker.env').read_text().splitlines() if '=' in line)
    env={**os.environ,'PG_KAGGLE_CHALLENGE_PASS':values['QF_PG_PASSWORD'],
        'QF_PG_PASSWORD':values['QF_PG_PASSWORD'],'QF_PG_USER':'challenge','QF_SSH_HOST':'127.0.0.1',
        'QF_SSH_PORT':'55222','QF_SSH_USER':'qf','QF_SSH_KEY':str(RUNTIME/'ssh-key')}
    if args.action=='generate':
        run([sys.executable,'-X','utf8',str(ROOT/'app/generate_database.py'),'--rows',str(args.rows),
            '--host','qf-challenge','--ssh_port','55222','--ssh_user','qf','--user','challenge',
            '--psql',str(RUNTIME/'pgsql/bin/psql.exe'),'--ssh_executable',str(RUNTIME/'ssh.cmd')],env=env)
    else:
        run([sys.executable,'-X','utf8',str(ROOT/'scripts/run_ssh_pipeline.py'),
            '--mode',args.mode,'--known-hosts',str(RUNTIME/'known_hosts')],env=env)

if __name__=='__main__': main()
