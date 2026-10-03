"""Help and invalid argv must exit before any operational module or function.

No product main function is called. A denied import is an isolated sentinel,
not a production read, network request, provider invocation or systemctl call.
"""
import ast
import builtins
from pathlib import Path
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ('backup.py', 'finalize_runtime.py', 'import_sources.py', 'x_feed_prefetch.py',
           'kaggle_batch/recovery_watchdog.py', 'kaggle_batch/snapshot_retention.py',
           'kaggle_batch/lane_scheduler.py')


@pytest.mark.parametrize('entry', ENTRIES)
@pytest.mark.parametrize('arguments,exit_code', [(['--help'],0),(['--slot6-unknown'],2),(['--dry-run'],2)])
def test_parameter_gate_exits_before_application_imports(entry,arguments,exit_code,monkeypatch,capsys):
    original=builtins.__import__
    imported=[]
    def guarded(name,*args,**kwargs):
        if name.split('.')[0] in {'initialize_secrets','ops_common','httpx','lane_scheduler',
                                  'snapshot_retention','batch_control','live_scope','exception_audit'}:
            imported.append(name)
            raise AssertionError('operational import before argv validation')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    monkeypatch.setattr(sys,'argv',[entry,*arguments])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(ROOT/'src'/entry),run_name='__main__')
    assert caught.value.code==exit_code and imported==[]
    output=capsys.readouterr()
    assert 'usage:' in (output.out+output.err)


def test_backup_verify_option_is_only_declared_operational_option():
    # Inspect the leading gate rather than invoking the authorized-write mode.
    tree=ast.parse((ROOT/'src/backup.py').read_text())
    gate=tree.body[1]
    args=[node.args[0].value for node in ast.walk(gate)
          if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
          and node.func.attr=='add_argument']
    assert args==['--verify-latest']
