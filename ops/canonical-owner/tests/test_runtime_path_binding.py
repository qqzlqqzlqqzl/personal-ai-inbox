"""Linux-only filesystem controls for canonical-parent venv execution."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'canonical_operator.py'
spec = importlib.util.spec_from_file_location('canonical_runtime_binding', SOURCE)
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


class RuntimeBindingControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='canonical-venv-binding-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / 'system-python'
        self.binary.write_bytes(b'SYNTHETIC_EXECUTABLE_ONLY')
        self.venv = self.root / 'retained' / 'venv'
        (self.venv / 'bin').mkdir(parents=True)
        self.python = self.venv / 'bin' / 'python'
        self.python.symlink_to(self.binary)
        self.alias = self.root / 'live'
        self.alias.symlink_to(self.venv.parent, target_is_directory=True)
        self.configured = str(self.alias / 'venv' / 'bin' / 'python')

    def runtime(self, executable=None, prefix=None, exec_prefix=None):
        return patch.multiple(op.sys, executable=executable or str(self.python),
                              prefix=prefix or str(self.venv),
                              exec_prefix=exec_prefix or str(self.venv))

    def test_canonical_parent_with_same_venv_is_accepted(self):
        with self.runtime():
            bound = op.bind_interpreter(self.configured)
        self.assertTrue(bound['configured']['aliases'])
        self.assertEqual(bound['configured_bin']['canonical'], str(self.venv / 'bin'))
        self.assertEqual(bound['configured_prefix']['canonical'], str(self.venv))

    def test_unchanged_literal_runner_is_accepted(self):
        with self.runtime():
            self.assertEqual(op.bind_interpreter(str(self.python))['running']['logical'], str(self.python))

    def test_other_venv_sharing_same_binary_is_rejected(self):
        other = self.root / 'other-venv'
        (other / 'bin').mkdir(parents=True)
        (other / 'bin' / 'python').symlink_to(self.binary)
        with self.runtime(executable=str(other / 'bin' / 'python'), prefix=str(other), exec_prefix=str(other)):
            with self.assertRaises(op.Refused):
                op.bind_interpreter(self.configured)

    def test_system_executable_resolution_is_rejected(self):
        with self.runtime(executable=str(self.binary), prefix=str(self.root), exec_prefix=str(self.root)):
            with self.assertRaises(op.Refused):
                op.bind_interpreter(self.configured)

    def test_wrong_prefix_is_rejected(self):
        with self.runtime(prefix=str(self.root)):
            with self.assertRaises(op.Refused):
                op.bind_interpreter(self.configured)

    def test_wrong_exec_prefix_is_rejected(self):
        with self.runtime(exec_prefix=str(self.root)):
            with self.assertRaises(op.Refused):
                op.bind_interpreter(self.configured)

    def test_alias_retarget_changes_saved_binding(self):
        with self.runtime():
            first = op.bind_interpreter(self.configured)
            self.alias.unlink()
            self.alias.symlink_to(self.venv.parent, target_is_directory=True)
            second = op.bind_interpreter(self.configured)
        self.assertNotEqual(first, second)

    def test_nonexistent_configured_interpreter_is_rejected(self):
        with self.runtime(), self.assertRaises(op.Refused):
            op.bind_interpreter(str(self.root / 'missing' / 'python'))

    def test_runtime_binding_carries_all_prefix_graphs(self):
        with self.runtime():
            bound = op.bind_interpreter(self.configured)
        self.assertEqual(set(bound), {'configured', 'running', 'configured_bin', 'running_bin',
                                     'configured_prefix', 'running_prefix', 'running_exec_prefix'})
        self.assertTrue(all(row['leaf']['kind'] in {'file', 'directory'} for row in bound.values()))


if __name__ == '__main__':
    unittest.main()
