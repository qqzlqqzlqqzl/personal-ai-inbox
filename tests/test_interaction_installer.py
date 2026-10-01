from pathlib import Path
import pytest
from patch_interaction_review import install


def fixture(tmp_path):
    root=tmp_path
    for name in ['frontend-review/before/src','frontend-review/after/src','upstream/reactflux/src']:(root/name).mkdir(parents=True)
    (root/'frontend-review/before/src/a.jsx').write_text('old')
    (root/'frontend-review/after/src/a.jsx').write_text('new')
    (root/'frontend-review/after/src/new.jsx').write_text('new component')
    (root/'upstream/reactflux/src/a.jsx').write_text('old')
    return root


def test_ui_installer_is_repeatable_and_only_writes_reviewed_paths(tmp_path):
    root=fixture(tmp_path);assert len(install(root))==2;assert install(root)==[]
    assert (root/'upstream/reactflux/src/a.jsx').read_text()=='new'


def test_ui_installer_refuses_unknown_drift_before_any_write(tmp_path):
    root=fixture(tmp_path);(root/'upstream/reactflux/src/a.jsx').write_text('someone else WIP')
    with pytest.raises(RuntimeError,match='source drift'):install(root)
    assert not (root/'upstream/reactflux/src/new.jsx').exists()
    assert (root/'upstream/reactflux/src/a.jsx').read_text()=='someone else WIP'
