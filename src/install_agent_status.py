"""Final additive overlay after the reviewed Reader stages; no publish operation."""
import hashlib
import os
import shutil
from pathlib import Path

PIN='534eeb97723ac11025de4ec1ac56335072e3be52'
ROUTES_BEFORE='90eb803fcf6feeaa32038dce6e8e8e6a14c09db6d37d51a930e3d348e39a2a34'
PANEL_BEFORE='88344d306911a0fe15dd9a47987f8e58d7dc2cb5501999f342f8ec8d31cc040b'
TOOLBAR_BEFORE='f4f433120afe9fce94f1ca816b8bc49211b53cb4db1aca1aa32e20e6efe89e62'
LINK_IMPORT='import { Link } from "react-router";\n'
PANEL_ANCHOR='    <section className="review-resource-freshness" aria-label="各资源读取状态">'
LINK='    <p><Link to="/agent-status" style={{display:"inline-flex",alignItems:"center",minHeight:44,padding:"8px 12px"}} onClick={e => {if(dirty.current||busyRef.current){e.preventDefault();setMessage("请先保存或还原设置，再打开任务状态。")}else onClose()}}>任务状态</Link></p>\n'
ANCHOR='            ...routes,\n'
ADDITION=ANCHOR+'            { path: "agent-status", lazy: lazyRoute(() => import("./pages/AgentStatus")) },\n'

def install(root):
    root=Path(root).resolve();web=root/'upstream/reactflux';source=root/'patches/agent-status'
    if (web/'UPSTREAM_REVISION').read_text().strip()!=PIN:raise RuntimeError('Unexpected Reader revision')
    routes=web/'src/routes.jsx';before=routes.read_text()
    base=before.replace(ADDITION,ANCHOR) if ADDITION in before else before
    if hashlib.sha256(base.encode()).hexdigest()!=ROUTES_BEFORE or base.count(ANCHOR)!=1:raise RuntimeError('Unreviewed authenticated routes; refusing status overlay')
    toolbar=web/'src/components/Ai/AiToolbar.jsx';toolbar_base=toolbar.read_text()
    if hashlib.sha256(toolbar_base.encode()).hexdigest()!=TOOLBAR_BEFORE:raise RuntimeError('Unreviewed toolbar; refusing status entry')
    panel=web/'src/components/Ai/AiPanel.jsx';panel_base=panel.read_text().replace(LINK_IMPORT,'').replace(LINK,'')
    if hashlib.sha256(panel_base.encode()).hexdigest()!=PANEL_BEFORE or panel_base.count(PANEL_ANCHOR)!=1:raise RuntimeError('Unreviewed settings panel; refusing status entry')
    # Validate all inputs before writing any file. Static test fixtures are excluded.
    names=['AgentStatus.jsx','status-controller.mjs','status-view.mjs','status-cache.mjs','status-contract.mjs','reader-status-client.mjs','status.css']
    for name in names:
        if not (source/name).is_file():raise RuntimeError('Missing status authoring file')
    target=web/'src/components/AgentStatus';target.mkdir(parents=True,exist_ok=True)
    for name in names:
        destination=web/'src/pages/AgentStatus.jsx' if name=='AgentStatus.jsx' else target/name
        shutil.copy2(source/name,destination)
    routes.write_text(base.replace(ANCHOR,ADDITION,1))
    panel.write_text(LINK_IMPORT+panel_base.replace(PANEL_ANCHOR,LINK+PANEL_ANCHOR,1))

if __name__=='__main__':install(Path(os.environ.get('AI_NEWS_ROOT',Path(__file__).resolve().parents[1])))