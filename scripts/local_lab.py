"""Isolated Windows diagnostic lab. This is NOT organizer-environment parity."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
BIN = RUNTIME / 'pgsql/bin'
DATA = RUNTIME / 'pgdata'
CONFIG = RUNTIME / 'lab.json'

def config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))

def environment():
    cfg = config()
    return {**os.environ, 'PGPASSWORD':(RUNTIME/'password.local').read_text().strip(),
            'PGPORT':str(cfg['port']), 'PGHOST':'127.0.0.1', 'PGUSER':'qf_lab', 'PGCLIENTENCODING':'UTF8'}

def psql(sql, database='kaggle_challenge', timeout=600):
    run = subprocess.run([str(BIN/'psql.exe'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-d', database, '-f', '-'],
        input=sql, text=True, encoding='utf-8', capture_output=True, env=environment(), timeout=timeout)
    if run.returncode:
        raise RuntimeError(run.stderr[-5000:])
    return run.stdout.strip()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start','stop','generate','status'])
    parser.add_argument('--rows', type=int, default=100000)
    args = parser.parse_args()
    if args.action == 'start':
        if not DATA.exists():
            password = secrets.token_urlsafe(32)
            (RUNTIME/'password.local').write_text(password, encoding='utf-8')
            subprocess.run([str(BIN/'initdb.exe'), '-D', str(DATA), '-U', 'qf_lab', '-E','UTF8',
                '--locale=C', '-A','scram-sha-256', '--pwfile', str(RUNTIME/'password.local')], check=True,
                stdout=subprocess.DEVNULL)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
            CONFIG.write_text(json.dumps({'port':port,'platform':'Windows diagnostic','official_parity':False}), encoding='utf-8')
            with (DATA/'postgresql.conf').open('a') as output:
                output.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\nshared_buffers='128MB'\nwork_mem='16MB'\nmax_connections=10\n")
        subprocess.run([str(BIN/'pg_ctl.exe'), '-D',str(DATA), '-l',str(RUNTIME/'postgres.log'), 'start','-w'],
            check=True, stdout=subprocess.DEVNULL)
        print('Isolated diagnostic PostgreSQL started on loopback.')
    elif args.action == 'stop':
        subprocess.run([str(BIN/'pg_ctl.exe'), '-D',str(DATA), 'stop','-m','fast','-w'], check=True)
    elif args.action == 'status':
        print(psql('SELECT version();', 'postgres'))
    else:
        env = environment()
        env['PG_KAGGLE_CHALLENGE_PASS'] = env.pop('PGPASSWORD')
        # Execute the exact official file. No --replace and no generator modifications.
        with (ROOT/'reports/generation.log').open('w',encoding='utf-8') as log:
            result = subprocess.run([os.sys.executable, '-X','utf8',str(ROOT/'app/generate_database.py'),
                '--rows',str(args.rows), '--host','127.0.0.1','--user','qf_lab','--postgres_port',str(config()['port']),
                '--psql',str(BIN/'psql.exe')], env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError('Generator failed; inspect reports/generation.log')
        print('Official unmodified generator completed:', args.rows)

if __name__ == '__main__':
    main()
