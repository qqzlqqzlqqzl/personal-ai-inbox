"""Install reviewed UI overlays, refusing unknown source drift before any writes.

Runs last, after the pinned reader's existing patches. It never publishes a
bundle, reads secrets or changes services. Tested on an isolated checkout first.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def install(root):
    root=Path(root); overlays=root/'frontend-review';web=root/'upstream/reactflux'
    history_path=overlays/'previous-hashes.json'
    history=json.loads(history_path.read_text()) if history_path.exists() else {}
    planned=[]
    for target in sorted((overlays/'after').rglob('*')):
        if not target.is_file():continue
        name=target.relative_to(overlays/'after');destination=web/name;before=overlays/'before'/name
        original=before.read_bytes() if before.is_file() else None;updated=target.read_bytes()
        if updated==original:continue
        current=destination.read_bytes() if destination.exists() else None
        if current==updated:continue
        accepted=original is not None and current==original
        accepted=accepted or (original is None and current is None)
        accepted=accepted or (current is not None and hashlib.sha256(current).hexdigest() in history.get(str(name),[]))
        if not accepted:raise RuntimeError(f'Unreviewed source drift, refusing overwrite: {name}')
        planned.append((destination,updated))
    for destination,data in planned:
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(dir=destination.parent,prefix='.review-')
        try:
            with os.fdopen(fd,'wb') as out:out.write(data)
            os.chmod(temp,0o644);os.replace(temp,destination)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    return [str(path.relative_to(web)) for path,_ in planned]


if __name__=='__main__':
    root=Path(os.environ.get('AI_NEWS_ROOT','/home/ubuntu/ai-news'))
    print(json.dumps({'ui_review_installed':install(root)},ensure_ascii=False))
