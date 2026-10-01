"""Offline negative proofs for the reusable synthetic paired staging admission."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import notes_pair_acceptance as pair


class RuntimeSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'src'
        self.source.mkdir()
        self.file=self.source/'core.py'
        self.file.write_text('value = "synthetic pinned source"\n')
        self.git('init','--quiet')
        self.git('add','src')
        self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                 'commit','--quiet','-m','Synthetic runtime baseline')
        self.baseline=self.git('rev-parse','HEAD')

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True).strip()

    def check(self):
        pair.verify_runtime_source(self.root,self.baseline)

    def test_exact_runtime_tree(self):
        self.check()

    def test_masked_modified_files_rejected(self):
        for flag in ('assume-unchanged','skip-worktree'):
            with self.subTest(flag=flag):
                self.git('update-index','--'+flag,'src/core.py')
                self.file.write_text('value = "unreviewed source"\n')
                self.assertEqual(self.git('diff',self.baseline,'--','src'),'')
                with self.assertRaisesRegex(AssertionError,'masked runtime index'):
                    self.check()
                self.git('update-index','--no-'+flag,'src/core.py')
                self.git('checkout','--','src/core.py')

    def test_actual_bytes_checked_even_if_index_tags_appear_clean(self):
        original=pair.command
        self.git('update-index','--assume-unchanged','src/core.py')
        self.file.write_text('value = "invisible edit"\n')
        def normal_tags(*args,**kw):
            result=original(*args,**kw)
            if args[:4]==('git','ls-files','-v','-z'):
                return '\0'.join('H '+row[2:] if row else row for row in result.split('\0'))
            return result
        with mock.patch.object(pair,'command',side_effect=normal_tags):
            with self.assertRaisesRegex(AssertionError,'runtime bytes mismatch'):
                self.check()

    def test_executable_mode_checked_when_git_ignores_it(self):
        self.git('config','core.filemode','false')
        self.file.chmod(0o755)
        self.assertEqual(self.git('diff',self.baseline,'--','src'),'')
        with self.assertRaisesRegex(AssertionError,'runtime executable mode'):
            self.check()

    def test_symlink_replacing_source_is_rejected(self):
        target=self.root/'other.py'
        target.write_bytes(self.file.read_bytes())
        self.file.unlink()
        self.file.symlink_to(target)
        with self.assertRaisesRegex(AssertionError,'runtime file type'):
            self.check()

    def test_untracked_imports_and_bytecode_are_rejected(self):
        for name in ('shadow.py','__pycache__/core.cpython-312.pyc','nested/plugin.py'):
            with self.subTest(name=name):
                extra=self.source/name
                extra.parent.mkdir(parents=True,exist_ok=True)
                extra.write_bytes(b'synthetic unreviewed import')
                with self.assertRaisesRegex(AssertionError,'unexpected or missing runtime files/imports'):
                    self.check()
                extra.unlink()


class ContainerBindingGuardTests(unittest.TestCase):
    def setUp(self):
        self.cid='a'*64
        self.info={'Id':self.cid,'Config':{'Image':pair.IMAGE},'State':{'Running':True},
                   'NetworkSettings':{'Ports':{'5432/tcp':[{'HostIp':'0.0.0.0','HostPort':'55473'}]}}}

    def test_exact_container_mapping_is_accepted(self):
        pair.verify_container_binding(self.info,self.cid)
        self.info['NetworkSettings']['Ports']['5432/tcp'][0]['HostIp']='127.0.0.1'
        pair.verify_container_binding(self.info,self.cid[:12])

    def test_another_container_cannot_authorize_target(self):
        with self.assertRaisesRegex(AssertionError,'container identity'):
            pair.verify_container_binding(self.info,'b'*64)

    def test_wrong_missing_or_non_loopback_port_is_rejected(self):
        for ports in ({}, {'5432/tcp':None}, {'5432/tcp':[{'HostIp':'0.0.0.0','HostPort':'55474'}]},
                      {'5433/tcp':[{'HostIp':'0.0.0.0','HostPort':'55473'}]},
                      {'5432/tcp':[{'HostIp':'192.0.2.1','HostPort':'55473'}]},
                      {'5432/tcp':[{'HostIp':'::','HostPort':'55473'}]}):
            with self.subTest(ports=ports):
                info=copy.deepcopy(self.info)
                info['NetworkSettings']['Ports']=ports
                with self.assertRaisesRegex(AssertionError,'does not own IPv4 loopback port'):
                    pair.verify_container_binding(info,self.cid)

    def test_wrong_image_or_stopped_container_is_rejected(self):
        for field,value,error in [('Config',{'Image':'unapproved'},'pinned disposable'),
                                  ('State',{'Running':False},'not running')]:
            info=copy.deepcopy(self.info)
            info[field]=value
            with self.assertRaisesRegex(AssertionError,error):
                pair.verify_container_binding(info,self.cid)

    def test_exited_process_or_wrong_executable_is_rejected(self):
        proc=mock.Mock(pid=os.getpid())
        proc.poll.return_value=0
        with self.assertRaisesRegex(AssertionError,'process exited'):
            pair.require_process_identity(proc,pair.BINARY_SHA)
        proc.poll.return_value=None
        with self.assertRaisesRegex(AssertionError,'executable identity mismatch'):
            pair.require_process_identity(proc,'0'*64)


if __name__=='__main__':
    unittest.main()
