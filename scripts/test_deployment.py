from contextlib import closing
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('configure',ROOT/'scripts/configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)
sys.path.insert(0,str(ROOT/'scripts'))
import native
import run_service

class ConfigurationTests(unittest.TestCase):
    def test_unique_keys_private_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory)/'one', Path(directory)/'two'
            configure.create(first,'https://tickets.example.com','127.0.0.1',8000)
            configure.create(second,'http://192.168.1.50:8000','0.0.0.0',8000)
            one, two = configure.validate(first), configure.validate(second)
            self.assertNotEqual(one['SECRET_KEY'],two['SECRET_KEY'])
            self.assertNotEqual(one['CREDENTIAL_KEY'],two['CREDENTIAL_KEY'])
            self.assertEqual(first.stat().st_mode & 0o777,0o600)
            self.assertEqual(two['COOKIE_SECURE'],'false')
            before = first.read_bytes()
            with self.assertRaises(FileExistsError):
                configure.create(first,'https://other.example.com','127.0.0.1',8000)
            self.assertEqual(before,first.read_bytes())

    def test_rejects_invalid_deployment_urls(self):
        for url in ['https://user:password@example.com','https://example.com/path','file:///tmp/a',
                    'https://example.com?token=secret','https://example.com:invalid','https://exa$mple.com']:
            with self.subTest(url=url),self.assertRaises(ValueError):
                configure.validate_url(url)

    def test_env_is_data_not_executable_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'.env'
            target.write_text("EXAMPLE_SECRET='$(touch /tmp/should-never-exist)'\nEXAMPLE_LABEL='Showcal <a@example.com>'\n")
            self.assertEqual(configure.read_env(target)['EXAMPLE_SECRET'],'$(touch /tmp/should-never-exist)')

class NativeDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        self.source=self.base/'source.sqlite3'
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript("CREATE TABLE auth_user (name TEXT); INSERT INTO auth_user VALUES ('existing-user'); CREATE TABLE concerts_profile (jellyfin_token TEXT); CREATE TABLE django_migrations (name TEXT);")

    def test_backup_preserves_records_and_refuses_overwrite(self):
        target=self.base/'backup.sqlite3'
        native.copy_database(self.source,target)
        with closing(sqlite3.connect(target)) as db, db:
            self.assertEqual(db.execute('SELECT name FROM auth_user').fetchall(),[('existing-user',)])
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError):
            native.copy_database(self.source,target)
        with self.assertRaises(ValueError):
            native.copy_database(self.base/'missing',self.base/'other')

    def test_rejects_non_app_database(self):
        empty=self.base/'empty.sqlite3'
        sqlite3.connect(empty).close()
        with self.assertRaises(ValueError):
            native.copy_database(empty,self.base/'destination')
        self.assertFalse((self.base/'destination').exists())

    def test_import_preserves_keys_but_replaces_local_settings(self):
        old=self.base/'old.env'; new=self.base/'new.env'
        configure.create(old,'http://localhost:8000','127.0.0.1',8000)
        previous=configure.read_env(old)
        with old.open('a') as f:
            f.write("TICKETMASTER_API_KEY='special $value # literal'\nDATABASE_PATH=/old/path\n")
        configure.create(new,'https://shows.example.com','0.0.0.0',8003,old)
        current=configure.validate(new)
        self.assertEqual(current['CREDENTIAL_KEY'],previous['CREDENTIAL_KEY'])
        self.assertEqual(current['SECRET_KEY'],previous['SECRET_KEY'])
        self.assertEqual(current['TICKETMASTER_API_KEY'],'special $value # literal')
        self.assertEqual(current['APP_URL'],'https://shows.example.com')
        self.assertEqual(current['DEBUG'],'false')
        self.assertEqual(current['PORT'],'8003')
        self.assertNotIn('DATABASE_PATH',current)

    def test_wrong_encryption_key_refused(self):
        from cryptography.fernet import Fernet
        key=Fernet.generate_key()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('INSERT INTO concerts_profile VALUES (?)',(Fernet(key).encrypt(b'private-token').decode(),))
        native.verify_credentials(self.source,key.decode())
        with self.assertRaisesRegex(ValueError,'does not match'):
            native.verify_credentials(self.source,Fernet.generate_key().decode())

    def test_occupied_port_refused(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            values={'BIND_ADDRESS':'127.0.0.1','PORT':str(sock.getsockname()[1])}
            with patch.object(native,'validate',return_value=values),self.assertRaises(OSError):
                native.port_check()

    def test_units_and_commands_use_unprivileged_service(self):
        unit=native.unit('worker',Path('/opt/showcal'))
        self.assertIn('User=showcal',unit)
        self.assertIn('ProtectSystem=strict',unit)
        self.assertIn('ReadWritePaths=/var/lib/showcal',unit)
        self.assertIn('run_service.py worker',unit)
        self.assertEqual(run_service.command('worker',[],{})[-2:],['manage.py','worker'])
        command=run_service.command('web',[],{'PORT':'8003','BIND_ADDRESS':'::1'})
        self.assertIn('[::1]:8003',command)
        with self.assertRaises(ValueError):
            native.unit('web',Path('/opt/unsafe%path'))


class DeploymentFlowTests(unittest.TestCase):
    """Exercise real Git and SQLite; replace host administration with a logged fixture."""
    def git(self,*args,cwd=None):
        return subprocess.run(['git',*args],cwd=cwd or self.repo,check=True,capture_output=True,text=True).stdout.strip()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        self.seed=self.base/'seed'; self.seed.mkdir()
        self.repo=self.seed
        self.data=self.base/'data'; self.data.mkdir()
        shutil.copytree(ROOT/'scripts',self.seed/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        for name in ['.env.example','.gitignore']:
            shutil.copy(ROOT/name,self.seed/name)
        # Relocate fixed production paths in the isolated test checkout only.
        for name in ['native.py','install-lxc.sh','update-lxc.sh']:
            p=self.seed/'scripts'/name
            p.write_text(p.read_text().replace('/var/lib/showcal',str(self.data)).replace('/etc/systemd/system',str(self.base/'units')))
        common=self.seed/'scripts/common.sh'
        common.write_text(common.read_text()+"""
# Test fixture: do not administer the developer's host.
require_tools() { mkdir -p .deploy; }
prepare_user() { :; }
load_config() { "$PYTHON_BIN" scripts/configure.py validate; }
build_environment() { printf 'dependencies\n' >> "$DEPLOY_TEST_LOG"; }
start_services() {
  printf 'migrate\n' >> "$DEPLOY_TEST_LOG"
  if [[ ${DEPLOY_TEST_FAIL_MIGRATE:-} == 1 ]]; then return 1; fi
  printf 'start\n' >> "$DEPLOY_TEST_LOG"
}
""")
        helper=self.seed/'scripts/native.py'
        helper.write_text(helper.read_text().replace("shutil.chown(DATA/'showcal.sqlite3',user='showcal',group='showcal')","pass  # No host account changes in tests"))
        self.git('init','-b','main'); self.git('config','user.email','test@example.com'); self.git('config','user.name','Test')
        self.git('add','.'); self.git('commit','-m','initial')
        self.git('init','--bare',str(self.base/'remote.git'))
        self.git('remote','add','origin',str(self.base/'remote.git')); self.git('push','origin','main')
        self.git('clone','--branch','main',str(self.base/'remote.git'),str(self.base/'app'))
        self.repo=self.base/'app'
        with patch.object(configure,'ROOT',self.repo):
            configure.create(self.repo/'.env','https://shows.example.com','127.0.0.1',8003)
        with closing(sqlite3.connect(self.data/'showcal.sqlite3')) as db, db:
            db.executescript("CREATE TABLE auth_user (name TEXT); INSERT INTO auth_user VALUES ('retained'); CREATE TABLE concerts_profile (jellyfin_token TEXT); CREATE TABLE django_migrations (name TEXT);")
        (self.repo/'.deploy').mkdir(); (self.repo/'.deploy/installed').touch()
        self.log=self.base/'calls.log'; self.log.touch()
        bin_dir=self.base/'bin'; bin_dir.mkdir()
        systemctl=bin_dir/'systemctl'
        systemctl.write_text('#!/bin/sh\nprintf "%s\n" "$*" >> "$DEPLOY_TEST_LOG"\n')
        systemctl.chmod(0o755)
        self.env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'PYTHON_BIN':sys.executable,'DEPLOY_TEST_LOG':str(self.log)}
        self.env.pop('SHOWCAL_UPDATE_SNAPSHOT',None)

    def run_update(self):
        return subprocess.run(['bash',str(self.repo/'scripts/update-lxc.sh')],cwd=self.repo,env=self.env,capture_output=True,text=True)

    def test_update_pulls_main_and_preserves_data_and_config(self):
        (self.seed/'new.txt').write_text('new')
        self.git('add','.',cwd=self.seed); self.git('commit','-m','new',cwd=self.seed); self.git('push','origin','main',cwd=self.seed)
        before=(self.repo/'.env').read_bytes()
        result=self.run_update()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.repo/'new.txt').exists())
        self.assertEqual((self.repo/'.env').read_bytes(),before)
        backup=next((self.repo/'backups').glob('*/database.sqlite3'))
        with closing(sqlite3.connect(backup)) as db, db:
            self.assertEqual(db.execute('SELECT name FROM auth_user').fetchone()[0],'retained')
        self.assertEqual((backup.parent/'config.env').read_bytes(),before)
        log=self.log.read_text()
        self.assertLess(log.index('stop'),log.index('dependencies'))
        self.assertLess(log.index('migrate'),log.index('start'))

    def test_dirty_checkout_refused_without_stopping_services(self):
        (self.repo/'local.txt').write_text('local work')
        result=self.run_update()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Working tree has changes',result.stderr)
        self.assertEqual(self.log.read_text(),'')

    def test_failed_migration_keeps_backup_and_services_stopped(self):
        self.env['DEPLOY_TEST_FAIL_MIGRATE']='1'
        result=self.run_update()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(list((self.repo/'backups').glob('*/database.sqlite3'))),1)
        self.assertNotIn('start',self.log.read_text())

    def test_existing_install_is_not_overwritten(self):
        result=subprocess.run(['bash',str(self.repo/'scripts/install-lxc.sh')],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Existing installation/data found',result.stderr)

    def test_backup_failure_prevents_source_change_and_migration(self):
        (self.data/'showcal.sqlite3').write_bytes(b'not a database')
        result=self.run_update()
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('migrate',self.log.read_text())
        self.assertIn('stop',self.log.read_text())

    def test_install_imports_existing_data_and_keeps_original_backup(self):
        transfer=self.base/'transfer'; transfer.mkdir()
        shutil.move(self.data/'showcal.sqlite3',transfer/'database.sqlite3')
        shutil.move(self.repo/'.env',transfer/'config.env')
        (self.repo/'.deploy/installed').unlink()
        # The fixture skips pip and uses this test environment for the import helper.
        (self.repo/'.venv-server/bin').mkdir(parents=True)
        launcher=self.repo/'.venv-server/bin/python'
        launcher.write_text('#!/bin/sh\nexec "$PYTHON_BIN" "$@"\n')
        launcher.chmod(0o755)
        result=subprocess.run(['bash',str(self.repo/'scripts/install-lxc.sh'),
                              '--url','http://192.168.1.50:8003','--bind','0.0.0.0',
                              '--import-db',str(transfer/'database.sqlite3'),
                              '--import-env',str(transfer/'config.env')],
                             cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        current=configure.validate(self.repo/'.env')
        original=configure.read_env(transfer/'config.env')
        self.assertEqual(current['CREDENTIAL_KEY'],original['CREDENTIAL_KEY'])
        self.assertEqual(current['APP_URL'],'http://192.168.1.50:8003')
        with closing(sqlite3.connect(self.data/'showcal.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT name FROM auth_user').fetchone()[0],'retained')
        self.assertTrue((self.repo/'backups/import-original/database.sqlite3').is_file())
        self.assertTrue((self.repo/'.deploy/installed').is_file())

    def test_import_without_matching_config_refused(self):
        (self.repo/'.deploy/installed').unlink()
        source=self.base/'incoming.sqlite3'
        shutil.move(self.data/'showcal.sqlite3',source)
        result=subprocess.run(['bash',str(self.repo/'scripts/install-lxc.sh'),'--import-db',str(source)],
                              cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('requires both',result.stderr)
        self.assertFalse((self.data/'showcal.sqlite3').exists())
