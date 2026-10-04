"""Build the pinned reader in a disposable checkout; never publish or use secrets."""
import argparse
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from build_frontend import prepare_version_info, validate_reader_bundle

PIN = '534eeb97723ac11025de4ec1ac56335072e3be52'
STAGES = (
    'patch_frontend.py', 'polish_frontend.py', 'specialize_login.py',
    'patch_reading_session.py', 'patch_ui_review.py', 'patch_article_notes.py',
    'patch_reading_telemetry.py', 'patch_scope_ai_filters.py',
    'patch_reader_detail_quality.py', 'patch_reader_entry_defaults.py', 'patch_interaction_review.py', 'install_agent_status.py',
)


def prepare():
    if ROOT == Path('/home/ubuntu/ai-news') or (ROOT / '.private').exists():
        raise RuntimeError('CI helper refuses a deployment or private-config checkout')
    web = ROOT / 'upstream/reactflux'
    actual = subprocess.check_output(['git', '-C', str(web), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN:
        raise RuntimeError('Expected exact pinned ReactFlux commit')
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    archive = subprocess.check_output(['git', '-C', str(web), 'archive', '--format=tar', '--prefix=reactflux/', PIN])
    (runtime / 'reactflux.tar.gz').write_bytes(archive)
    (web / 'UPSTREAM_REVISION').write_text(PIN + '\n')
    os.environ['AI_NEWS_ROOT'] = str(ROOT)
    generated_patch = ROOT / 'patches/reactflux.patch'
    original_patch = generated_patch.read_bytes()
    try:
        for name in STAGES:
            script = ROOT / 'src' / name
            code = script.read_text().replace('/home/ubuntu/ai-news', str(ROOT))
            # Trusted checked-in overlay only; substituted paths stay in this checkout.
            if name == 'patch_interaction_review.py':
                # Public pinned source after the owned earlier patches, never runtime/private data.
                # Retain exact inputs so a fail-closed overlay mismatch can be reviewed, not bypassed.
                for before in (ROOT / 'frontend-review/before').rglob('*'):
                    if before.is_file():
                        relative = before.relative_to(ROOT / 'frontend-review/before')
                        source = web / relative
                        if source.is_file():
                            evidence = ROOT / 'artifacts/review-overlay-input' / relative
                            evidence.parent.mkdir(parents=True, exist_ok=True)
                            evidence.write_bytes(source.read_bytes())
            exec(compile(code, str(script), 'exec'), {'__name__': '__main__', '__file__': str(script)})  # noqa: S102
    finally:
        generated_patch.write_bytes(original_patch)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    prepare()
    if args.prepare_only:
        return
    web = ROOT / 'upstream/reactflux'
    env = {**os.environ, 'VITE_BASE_PATH': '/inbox/', 'NODE_OPTIONS': '--max-old-space-size=1536'}
    node, pnpm = shutil.which('node'), shutil.which('pnpm')
    if not node or not pnpm:
        raise RuntimeError('Node and pnpm must be installed from the workflow pins')
    prepare_version_info(web, Path(node), env)
    output = ROOT / 'runtime/browser-build'
    if output.exists():
        retained = ROOT / 'runtime/retained-browser-builds'
        retained.mkdir(exist_ok=True)
        output.rename(retained / ('browser-build-' + uuid.uuid4().hex))
    subprocess.run([pnpm, 'exec', 'vite', 'build', '--outDir', str(output)],
                   cwd=web, env=env, check=True, timeout=600)
    validate_reader_bundle(output)
    print('Validated isolated /inbox/ bundle:', output)


if __name__ == '__main__':
    main()
