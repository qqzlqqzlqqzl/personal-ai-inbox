import subprocess
from pathlib import Path

import pytest

import build_frontend


def test_standard_version_prebuild_uses_selected_node_and_upstream_cwd(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(build_frontend.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    node, web = tmp_path / "node/bin/node", tmp_path / "upstream/reactflux"
    env = {"VITE_BASE_PATH": "/inbox/"}
    build_frontend.prepare_version_info(web, node, env)
    assert calls == [(([str(node), str(web / "src/scripts/version-info.js")],),
                     {"cwd": web, "env": env, "check": True, "timeout": 60})]


def test_failed_version_prebuild_aborts_instead_of_publishing(monkeypatch, tmp_path):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(build_frontend.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        build_frontend.prepare_version_info(tmp_path, Path("/selected/node"), {})
