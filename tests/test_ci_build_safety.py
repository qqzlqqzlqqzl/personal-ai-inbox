import ast
import importlib.util
import json
from pathlib import Path

import pytest


def helper():
    path=Path(__file__).with_name('build_ci_reader.py')
    spec=importlib.util.spec_from_file_location('ci_reader_helper',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ci_reader_pin_matches_project_lock():
    module=helper()
    assert module.PIN==json.loads((module.ROOT/'upstream.lock.json').read_text())['reactflux']


def test_ci_helper_refuses_private_config_checkout(tmp_path,monkeypatch):
    module=helper()
    (tmp_path/'.private').mkdir()
    monkeypatch.setattr(module,'ROOT',tmp_path)
    with pytest.raises(RuntimeError,match='refuses'):
        module.prepare()


def test_ci_helper_rejects_unpinned_source_before_running_overlay(tmp_path,monkeypatch):
    module=helper()
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**kw:'unexpected-sha\n')
    with pytest.raises(RuntimeError,match='exact pinned'):
        module.prepare()
    assert not (tmp_path/'runtime').exists()


def test_playwright_route_handler_does_not_capture_request_as_fixture():
    # Playwright supplies (route, request); fixture captures must be keyword-only.
    source=Path(__file__).with_name('history_browser_acceptance.py').read_text()
    handler=next(node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.FunctionDef) and node.name=='api_route')
    assert [arg.arg for arg in handler.args.args]==['route','_request']
    assert {arg.arg for arg in handler.args.kwonlyargs}=={'calls','mode','pending','feed','quota','writes'}
