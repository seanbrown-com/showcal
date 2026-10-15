#!/usr/bin/env python3
"""Native deployment helpers; configuration values are never evaluated by a shell."""
from contextlib import closing
import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
from configure import validate

ROOT = Path(__file__).resolve().parent.parent
DATA = Path('/var/lib/showcal')

def copy_database(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise ValueError('Source database does not exist')
    if destination.exists():
        raise ValueError('Destination already exists; no database overwritten')
    partial = destination.with_name(destination.name+'.partial')
    if partial.exists():
        raise ValueError('Partial backup already exists')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, src:
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source database failed integrity check')
        tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'auth_user','concerts_profile','django_migrations'} <= tables:
            raise ValueError('This is not a Showcal database')
        with closing(sqlite3.connect(partial)) as dst, dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup failed integrity check')
    partial.chmod(0o600)
    partial.rename(destination)

def verify_credentials(source, key):
    from cryptography.fernet import Fernet, InvalidToken
    cipher = Fernet(key.encode())
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as db, db:
        for (token,) in db.execute("SELECT jellyfin_token FROM concerts_profile WHERE jellyfin_token != ''"):
            try:
                cipher.decrypt(token.encode())
            except InvalidToken:
                raise ValueError('CREDENTIAL_KEY does not match the imported Jellyfin connections') from None

def unit(role, root=ROOT):
    if not re.fullmatch(r'[/A-Za-z0-9_.-]+',str(root)):
        raise ValueError('Install path must contain only letters, digits, /, _, . and -')
    return f"""[Unit]
Description=Showcal {role}
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
User=showcal
Group=showcal
WorkingDirectory={root}
ExecStart={root}/.venv-server/bin/python {root}/scripts/run_service.py {role}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
TimeoutStopSec=100
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/showcal

[Install]
WantedBy=multi-user.target
"""

def port_check():
    values=validate(ROOT/'.env')
    host=values.get('BIND_ADDRESS','127.0.0.1')
    with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind((host,int(values.get('PORT','8003'))))

def health():
    values=validate(ROOT/'.env')
    host=values.get('BIND_ADDRESS','127.0.0.1')
    host={'0.0.0.0':'127.0.0.1','::':'::1'}.get(host,host)
    if ':' in host:
        host='['+host+']'
    url=f"http://{host}:{values.get('PORT','8003')}/health/"
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # A new worker must acquire the lease and write a fresh heartbeat.
    for _ in range(45):
        try:
            with opener.open(urllib.request.Request(url,headers={'Host':'localhost'}),timeout=2) as response:
                if response.status != 200:
                    raise ValueError('Web health check failed')
            subprocess.run(['systemctl','is-active','--quiet','showcal','showcal-worker'],check=True)
            subprocess.run(['runuser','-u','showcal','--',str(ROOT/'.venv-server/bin/python'),
                            str(ROOT/'scripts/run_service.py'),'manage','worker_health'],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return
        except (OSError,ValueError,subprocess.CalledProcessError):
            time.sleep(2)
    raise ValueError('Services did not become healthy; inspect journalctl -u showcal -u showcal-worker')

def clear_lease():
    # Call only while both services are stopped (also clears imported worker state).
    with closing(sqlite3.connect(DATA/'showcal.sqlite3')) as db, db:
        db.execute('DELETE FROM concerts_workerstate')

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','import','units','port','health','clear-lease'])
    parser.add_argument('path',nargs='?',type=Path)
    args=parser.parse_args()
    if args.action=='backup':
        copy_database(DATA/'showcal.sqlite3',args.path.resolve())
    elif args.action=='import':
        verify_credentials(args.path,validate(ROOT/'.env')['CREDENTIAL_KEY'])
        copy_database(args.path.resolve(),DATA/'showcal.sqlite3')
        shutil.chown(DATA/'showcal.sqlite3',user='showcal',group='showcal')
    elif args.action=='units':
        for role,name in [('web','showcal'),('worker','showcal-worker')]:
            target=Path('/etc/systemd/system')/(name+'.service')
            target.write_text(unit(role))
            target.chmod(0o644)
    elif args.action=='port':
        port_check()
    elif args.action=='health':
        health()
    else:
        clear_lease()

if __name__=='__main__':
    main()
