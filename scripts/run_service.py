#!/usr/bin/env python3
"""Load private configuration as data and launch a native service or management command."""
import os
from pathlib import Path
import sys
from configure import validate

ROOT = Path(__file__).resolve().parent.parent
DATA = Path('/var/lib/showcal')

def command(mode, args, values):
    python = str(ROOT / '.venv-server/bin/python')
    if mode == 'web':
        host = values.get('BIND_ADDRESS','127.0.0.1')
        if ':' in host:
            host = '[' + host + ']'
        return [python,'-m','gunicorn','config.wsgi:application','--bind',
                host+':'+values.get('PORT','8003'),'--workers','2','--timeout','90',
                '--error-logfile','-']
    if mode == 'worker':
        return [python,'manage.py','worker']
    if mode == 'manage':
        return [python,'manage.py',*args]
    raise ValueError('Expected web, worker or manage')

def main():
    values = validate(ROOT / '.env')
    env = {**os.environ,**values,'DATABASE_PATH':str(DATA/'showcal.sqlite3'),
           'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1'}
    argv = command(sys.argv[1],sys.argv[2:],values)
    os.chdir(ROOT)
    os.execve(argv[0],argv,env)

if __name__ == '__main__':
    main()
