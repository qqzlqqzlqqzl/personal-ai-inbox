import shutil
from pathlib import Path
import pytest
import install_agent_status as installer

def test_refuses_unreviewed_routes_before_writing_anything(tmp_path):
    web=tmp_path/'upstream/reactflux';(web/'src').mkdir(parents=True)
    (web/'UPSTREAM_REVISION').write_text(installer.PIN)
    (web/'src/routes.jsx').write_text('unknown routes')
    with pytest.raises(RuntimeError,match='Unreviewed'):installer.install(tmp_path)
    assert not (web/'src/components').exists()

def test_reviewed_route_overlay_is_idempotent_and_gated(tmp_path,monkeypatch):
    import hashlib
    web=tmp_path/'upstream/reactflux';(web/'src/pages').mkdir(parents=True)
    (web/'UPSTREAM_REVISION').write_text(installer.PIN)
    before='RouterProtect\n'+installer.ANCHOR+'AuthenticatedApp\n'
    (web/'src/routes.jsx').write_text(before);monkeypatch.setattr(installer,'ROUTES_BEFORE',hashlib.sha256(before.encode()).hexdigest())
    toolbar=web/'src/components/Ai/AiToolbar.jsx';toolbar.parent.mkdir(parents=True);toolbar.write_text(installer.TOOLBAR_ANCHOR);monkeypatch.setattr(installer,'TOOLBAR_BEFORE',hashlib.sha256(installer.TOOLBAR_ANCHOR.encode()).hexdigest())
    source=Path(__file__).parents[1]/'patches/agent-status';shutil.copytree(source,tmp_path/'patches/agent-status')
    installer.install(tmp_path);first=(web/'src/routes.jsx').read_text();installer.install(tmp_path)
    assert first==(web/'src/routes.jsx').read_text() and first.count('path: "agent-status"')==1
    assert not (web/'src/components/AgentStatus/mock-state.json').exists()
    assert toolbar.read_text().count('to="/agent-status"')==1