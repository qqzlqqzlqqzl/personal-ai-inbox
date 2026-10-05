"""Exact known-text admission for the sidebar projection's bounded upgrade."""
import ast
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'src/patch_reader_entry_defaults.py'


def patcher(tmp_path):
    tree = ast.parse(SCRIPT.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'patch')
    scope = {'WEB': tmp_path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), 'exec'), scope)
    return scope['patch']


@pytest.mark.parametrize('source', ['pristine', 'reviewed-current', 'reviewed-older'])
def test_exact_variants_upgrade_and_repeat_without_adjacent_changes(tmp_path, source):
    target = tmp_path / 'Sidebar.jsx'
    target.write_text('before\n' + source + '\nafter\n')
    patch = patcher(tmp_path)
    patch('Sidebar.jsx', 'pristine', 'updated', previous=('reviewed-current', 'reviewed-older'))
    assert target.read_text() == 'before\nupdated\nafter\n'
    patch('Sidebar.jsx', 'pristine', 'updated', previous=('reviewed-current', 'reviewed-older'))
    assert target.read_text() == 'before\nupdated\nafter\n'


@pytest.mark.parametrize('source', ['unknown', 'reviewed-current\nreviewed-older', 'reviewed-current\nreviewed-current'])
def test_unknown_or_ambiguous_variants_are_rejected(tmp_path, source):
    target = tmp_path / 'Sidebar.jsx'
    target.write_text(source)
    with pytest.raises(RuntimeError, match='reader-entry patch anchor mismatch'):
        patcher(tmp_path)('Sidebar.jsx', 'pristine', 'updated', previous=('reviewed-current', 'reviewed-older'))
    assert target.read_text() == source
