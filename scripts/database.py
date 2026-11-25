"""Manage only this project's isolated PostgreSQL cluster on loopback:55432."""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('DENTAL_PG_BIN', 'D:/PostgreSQL/18/bin'))
LOCAL = ROOT/'.local'
DATA = LOCAL/'postgres'
DEFAULT_PORT = int(os.environ.get('DENTAL_DB_PORT', '55433'))
env = dict(os.environ, TEMP=str(ROOT/'.tmp'), TMP=str(ROOT/'.tmp'), PYTHONUTF8='1')
def run(name, *args):
    return subprocess.run([str(BIN/name), *map(str,args)], env=env, cwd=ROOT,
                          check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def main():
    (ROOT/'.tmp').mkdir(exist_ok=True)
    LOCAL.mkdir(exist_ok=True)
    (LOCAL/'logs').mkdir(exist_ok=True)
    action = sys.argv[1] if len(sys.argv)>1 else 'status'
    if action in ('stop','status'):
        run('pg_ctl.exe', '-D', DATA, action, *(['-m','fast','-w'] if action=='stop' else []))
        return
    config_path = LOCAL/'config.json'
    if not config_path.exists():
        if DATA.exists():
            raise SystemExit('Existing data without project configuration: stop for inspection.')
        config_path.write_text(json.dumps({'secret_key':secrets.token_urlsafe(48),
          'db_password':secrets.token_urlsafe(32), 'bootstrap_password':secrets.token_urlsafe(32),
          'port':DEFAULT_PORT}, indent=2), encoding='utf-8')
    cfg=json.loads(config_path.read_text(encoding='utf-8'))
    port = cfg['port']
    if not (DATA/'PG_VERSION').exists():
        password_file=LOCAL/'bootstrap-password.txt'
        password_file.write_text(cfg['bootstrap_password'],encoding='utf-8')
        run('initdb.exe','-D',DATA,'-U','postgres','--encoding=UTF8','--locale=C',
            '--auth=scram-sha-256',f'--pwfile={password_file}')
        with (DATA/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write(f"\nlisten_addresses = '127.0.0.1'\nport = {port}\n")
    status=subprocess.run([str(BIN/'pg_ctl.exe'),'-D',str(DATA),'status'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    if status.returncode:
        run('pg_ctl.exe','-D',DATA,'-l',LOCAL/'logs/postgres.log','-w','start')
    if action=='start':
        return
    import psycopg
    from psycopg import sql
    with psycopg.connect(host='127.0.0.1',port=port,user='postgres',password=cfg['bootstrap_password'],
                        dbname='postgres',autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='dental_app'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE dental_app LOGIN CREATEDB PASSWORD {}').format(sql.Literal(cfg['db_password'])))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='dental_dev'").fetchone():
            conn.execute('CREATE DATABASE dental_dev OWNER dental_app')
    print('Isolated development DB ready on the configured loopback port; credentials retained only in .local.')

if __name__=='__main__': main()
