import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('configure',ROOT/'scripts/configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)

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

class DeploymentFlowTests(unittest.TestCase):
    def git(self,*args,cwd=None):
        return subprocess.run(['git',*args],cwd=cwd or self.repo,check=True,capture_output=True,text=True).stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.seed = self.base/'seed'; self.seed.mkdir()
        self.repo = self.seed
        shutil.copytree(ROOT/'scripts',self.seed/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        for name in ['.env.example','.gitignore','compose.yaml']:
            shutil.copy(ROOT/name,self.seed/name)
        self.git('init','-b','main')
        self.git('config','user.email','test@example.com'); self.git('config','user.name','Test')
        self.git('add','.'); self.git('commit','-m','initial')
        self.git('init','--bare',str(self.base/'remote.git'))
        self.git('remote','add','origin',str(self.base/'remote.git')); self.git('push','origin','main')
        self.git('clone','--branch','main',str(self.base/'remote.git'),str(self.base/'app'))
        self.repo = self.base/'app'
        with patch.object(configure,'ROOT',self.repo):
            configure.create(self.repo/'.env','https://tickets.example.com','127.0.0.1',8000)
        self.log = self.base/'calls.log'
        bin_dir = self.base/'bin'; bin_dir.mkdir()
        for name, body in {
            'uname':'#!/bin/sh\nprintf "Linux\\n"\n',
            'flock':'#!/bin/sh\nexit 0\n',
            'docker':'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'],'a') as stream: stream.write(repr(args)+'\\n')
if args[:2]==['volume','inspect']: sys.exit(1)
if 'migrate' in args and os.environ.get('DEPLOY_TEST_FAIL_MIGRATE'): sys.exit(1)
if '-c' in args: sys.stdout.buffer.write(b'SQLite test backup')
''',
        }.items():
            (bin_dir/name).write_text(body); (bin_dir/name).chmod(0o755)
        self.env = {**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'DEPLOY_TEST_LOG':str(self.log)}
        self.env.pop('SHOWCAL_UPDATE_SNAPSHOT',None)

    def run_script(self,script,*args):
        return subprocess.run(['bash',str(self.repo/'scripts'/script),*args],cwd=self.repo,env=self.env,capture_output=True,text=True)

    def test_update_pulls_main_preserves_config_and_orders_backup_before_migration(self):
        (self.seed/'new-file.txt').write_text('new revision')
        self.git('add','.',cwd=self.seed); self.git('commit','-m','new',cwd=self.seed); self.git('push','origin','main',cwd=self.seed)
        before = (self.repo/'.env').read_bytes()
        result = self.run_script('update-lxc.sh')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.repo/'new-file.txt').exists())
        self.assertEqual(before,(self.repo/'.env').read_bytes())
        backups = list((self.repo/'backups').glob('*/database.sqlite3'))
        self.assertEqual(len(backups),1)
        self.assertEqual((backups[0].parent/'config.env').read_bytes(),before)
        log = self.log.read_text()
        self.assertLess(log.index("'stop'"),log.index("'python', '-c'"))
        self.assertLess(log.index("'python', '-c'"),log.index("'migrate'"))
        self.assertLess(log.index("'migrate'"),log.index("'up'"))

    def test_dirty_checkout_refused_without_stopping_services(self):
        (self.repo/'unexpected.txt').write_text('local work')
        result = self.run_script('update-lxc.sh')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Working tree has changes',result.stderr)
        self.assertNotIn("'stop'",self.log.read_text())

    def test_failed_migration_preserves_backup_and_leaves_services_stopped(self):
        self.env['DEPLOY_TEST_FAIL_MIGRATE']='1'
        result = self.run_script('update-lxc.sh')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(list((self.repo/'backups').glob('*/database.sqlite3'))),1)
        self.assertNotIn("'up'",self.log.read_text())

    def test_install_keeps_existing_env(self):
        before = (self.repo/'.env').read_bytes()
        result = self.run_script('install-lxc.sh')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.repo/'.env').read_bytes(),before)
        self.assertTrue((self.repo/'.deploy/installed').exists())
