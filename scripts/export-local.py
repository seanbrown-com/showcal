#!/usr/bin/env python3
"""Export a consistent local SQLite backup together with its private configuration."""
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
from configure import read_env
from native import copy_database, verify_credentials

ROOT=Path(__file__).resolve().parent.parent

def main():
    os.umask(0o077)
    values=read_env(ROOT/'.env')
    source=Path(values.get('DATABASE_PATH',str(ROOT/'db.sqlite3')))
    if not source.is_absolute():
        source=ROOT/source
    verify_credentials(source,values['CREDENTIAL_KEY'])
    folder=ROOT/'backups'/('lxc-transfer-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    copy_database(source.resolve(),folder/'database.sqlite3')
    shutil.copyfile(ROOT/'.env',folder/'config.env')
    (folder/'config.env').chmod(0o600)
    print('Private transfer backup:',folder)

if __name__=='__main__':
    main()
