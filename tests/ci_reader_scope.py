"""Conservative Reader CI selection from immutable event revisions, never HEAD^."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

FULLTEXT = {
    'src/kaggle_batch/fulltext_source.py',
    'src/kaggle_batch/test_fulltext_source.py',
    'src/kaggle_batch/test_fulltext_bridge.py',
    'src/kaggle_batch/test_reader_fulltext.py',
}
BACKUP = {'src/backup.py', 'tests/test_operational_cli_admission.py'}
# Benchmark orchestration/fixtures only; product and shared CI changes fail closed.
PERFORMANCE = {
    'tests/fixtures/reader-loading-product-ab-inputs.json',
    'tests/test_reader_product_ab_profile.py',
    'tests/reader_loading_ab_ci.py',
    'tests/test_reader_loading_ab_ci.py',
}
IMAGE_HISTORY = 'frontend-review/previous-hashes.json'
IMAGE_HELPER = 'frontend-review/after/src/components/Article/reader-image-variants.js'
IMAGE_KEY = 'src/components/Article/reader-image-variants.js'
IMAGE_HISTORY_FILES = {
    IMAGE_KEY: IMAGE_HELPER,
    'src/components/Article/ReaderThumbnail.jsx':
        'frontend-review/after/src/components/Article/ReaderThumbnail.jsx',
    'src/components/Article/ArticleDetail.jsx':
        'frontend-review/after/src/components/Article/ArticleDetail.jsx',
}
IMAGES = {
    IMAGE_HELPER, IMAGE_HISTORY, 'src/reader_image_proxy.py',
    'src/reader_cover_proxy.py', 'src/reader_image_cache.py',
    'src/warm_reader_covers.py', 'tests/test_warm_reader_covers.py',
    'deploy/systemd/ai-news-reader-covers.service',
    'deploy/systemd/ai-news-reader-covers.timer',
    'patches/ProgressiveLoadMore.jsx', 'patches/reading-session.js',
    'src/patch_reading_session.py', 'tests/test_reading_session.mjs',
    'src/patch_frontend.py', 'tests/test_frontend_overlay_rebuild.py',
    'tests/test_source_catalog_overlay.py',
    'tests/test_reader_cover_proxy.py', 'tests/test_reader_image_cache.py',
    'tests/test_api.py',
    'frontend-review/before/src/components/Article/ArticleGridCard.jsx',
    'frontend-review/after/src/components/Article/ArticleGridCard.jsx',
    'frontend-review/after/src/components/Article/ReaderThumbnail.jsx',
    'tests/test_reader_image_proxy.py', 'tests/test_reader_thumbnails.mjs',
    'tests/test_reader_thumbnail_component.mjs',
}
IMAGE_CI_SUPPORT = {
    'tests/ci_reader_scope.py', 'tests/test_ci_reader_scope.py',
    '.github/workflows/reader-regression.yml',
}
TRANSLATION = {
    'src/bilingual_translation.py', 'src/api.py', 'src/patch_frontend.py',
    'patches/BilingualReading.jsx', 'patches/BilingualReading.css',
    'frontend-review/after/src/components/Article/ArticleDetail.jsx', IMAGE_HISTORY,
    'tests/test_bilingual_translation.py', 'tests/test_bilingual_reading.mjs',
    'tests/test_bilingual_reading_component.mjs', 'tests/test_bilingual_reading_install.py',
    'tests/test_api.py', 'tests/test_frontend_overlay_rebuild.py',
    'tests/test_source_catalog_overlay.py',
    'docs/research/reader-pretranslation-20261007/outline.yaml',
    'docs/research/reader-pretranslation-20261007/fields.yaml',
}
TRANSLATION_ANCHORS = {'src/bilingual_translation.py', 'patches/BilingualReading.jsx', 'patches/BilingualReading.css'}
NATIVE = {
    'src/api.py', 'src/notes_metadata.py',
    'ops/miniflux-metadata/miniflux-2.3.3-entry-metadata.patch',
    'ops/miniflux-metadata/pins.json',
    'ops/miniflux-metadata/tests/internal/api/entry_metadata_handler_test.go',
    'ops/miniflux-metadata/tests/internal/api/entry_metadata_postgres_test.go',
    'ops/miniflux-metadata/tests/internal/storage/entry_metadata_test.go',
    'tests/notes_pair_acceptance.py', 'tests/test_notes_pair_guards.py',
}
INTERFACES = NATIVE | {
    'src/feed_history.py', 'tests/test_feed_history.py', 'tests/test_api.py',
    'tests/test_notes_metadata.py', 'tests/test_notes_pair_metadata_url_contract.py',
    'tests/test_query_dom_ownership.py', 'tests/test_query_route_queue.py',
    'tests/test_query_result_ownership.mjs', 'tests/test_source_history.mjs',
}
PIN_FILE = 'tests/dev_fixture_workspace_browser_acceptance.py'
PIN = re.compile(rb'^SRC = "([0-9a-f]{40})"$', re.MULTILINE)
PYTHON_TESTS = {
    'translation': [
        'tests/test_bilingual_translation.py', 'tests/test_api.py',
        'tests/test_bilingual_reading_install.py',
        'tests/test_ci_reader_scope.py', 'tests/test_frontend_overlay_rebuild.py',
        'tests/test_source_catalog_overlay.py',
    ],
    'performance': [
        'tests/test_reader_product_ab_profile.py',
        'tests/test_reader_loading_ab_ci.py',
    ],
    'images': [
        'tests/test_reader_image_proxy.py', 'tests/test_reader_image_cache.py',
        'tests/test_reader_cover_proxy.py', 'tests/test_api.py',
        'tests/test_ci_reader_scope.py',
        'tests/test_frontend_overlay_rebuild.py', 'tests/test_source_catalog_overlay.py',
        'tests/test_warm_reader_covers.py',
    ],
    'fulltext': [
        'src/kaggle_batch/test_fulltext_source.py',
        'src/kaggle_batch/test_fulltext_bridge.py',
        'src/kaggle_batch/test_reader_fulltext.py',
    ],
    # The admission module also protects shared operational CLI boundaries.
    'backup': ['tests/test_operational_cli_admission.py'],
    'interfaces': [
        'tests/test_api.py', 'tests/test_notes_metadata.py',
        'tests/test_notes_pair_guards.py', 'tests/test_notes_pair_metadata_url_contract.py',
        'tests/test_query_dom_ownership.py', 'tests/test_query_route_queue.py',
        'tests/test_feed_history.py',
    ],
}


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL, timeout=20)


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) and value != '0'*40


def pin_only(before, after, base_src, head_src):
    """One exact literal, unchanged surrounding bytes, both actual src trees bound."""
    old, new = list(PIN.finditer(before)), list(PIN.finditer(after))
    if len(old) != 1 or len(new) != 1:
        return False
    left, right = old[0], new[0]
    return (left[1].decode() == base_src and right[1].decode() == head_src
            and before[:left.start(1)] + before[left.end(1):]
            == after[:right.start(1)] + after[right.end(1):])


def classify(changes, verified_pin=False, verified_image_history=False):
    if not changes or any(row['status'] not in {'A', 'M'} for row in changes):
        return 'full'
    paths = {row['path'] for row in changes}
    paths = {path for path in paths if not (
        path == 'README.md' or path.startswith('docs/') and path.endswith('.md'))}
    if not paths:
        return 'docs'
    if paths <= PERFORMANCE:
        return 'performance'
    if PIN_FILE in paths:
        if not verified_pin:
            return 'full'
        paths.remove(PIN_FILE)
    if paths and paths <= FULLTEXT:
        return 'fulltext'
    if paths and paths <= BACKUP:
        return 'backup'
    if paths & TRANSLATION_ANCHORS and paths <= TRANSLATION | IMAGE_CI_SUPPORT:
        return 'translation' if IMAGE_HISTORY not in paths or verified_image_history else 'full'
    if paths - IMAGE_CI_SUPPORT and paths <= IMAGES | IMAGE_CI_SUPPORT:
        return 'images' if IMAGE_HISTORY not in paths or verified_image_history else 'full'
    if paths and paths <= INTERFACES:
        return 'interfaces'
    return 'full'


def select(root, event_name, event, expected_head):
    result = {'scope': 'full', 'native': False, 'reason': 'unverified_diff',
              'head': expected_head, 'changes': []}
    try:
        if not sha(expected_head) or git(root, 'rev-parse', 'HEAD').decode().strip() != expected_head:
            return {**result, 'reason': 'checkout_identity_mismatch'}
        git(root, 'diff', '--quiet', 'HEAD')
        if event_name == 'pull_request':
            base = event['pull_request']['base']['sha']
            head = event['pull_request']['head']['sha']
        elif event_name == 'push':
            base, head = event['before'], event['after']
        elif event_name == 'workflow_dispatch':
            inputs = event.get('inputs') or {}
            if inputs.get('scope', 'full') != 'auto':
                return {**result, 'reason': 'requested_full'}
            base, head = inputs.get('base_sha'), expected_head
        else:
            return {**result, 'reason': 'unsupported_event'}
        if not sha(base) or not sha(head) or head != expected_head:
            return {**result, 'reason': 'event_revision_missing_or_mismatched'}
        for revision in (base, head):
            git(root, 'cat-file', '-e', revision+'^{commit}')
        comparison = base
        if event_name == 'pull_request':
            comparison = git(root, 'merge-base', '--all', base, head).decode().strip()
            if not sha(comparison):
                return {**result, 'reason': 'ambiguous_merge_base'}
        elif event_name == 'workflow_dispatch':
            git(root, 'merge-base', '--is-ancestor', base, head)
        raw = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                  '--name-status', '-z', comparison, head, '--')
        fields = raw.decode('utf-8').split('\0')
        if fields[-1] != '' or (len(fields)-1) % 2:
            return {**result, 'reason': 'invalid_diff_records'}
        changes = [{'status': fields[i], 'path': fields[i+1]}
                   for i in range(0, len(fields)-1, 2)]
        verified = False
        if any(row['path'] == PIN_FILE for row in changes):
            base_src = git(root, 'rev-parse', comparison+':src').decode().strip()
            head_src = git(root, 'rev-parse', head+':src').decode().strip()
            verified = pin_only(git(root, 'show', comparison+':'+PIN_FILE),
                                git(root, 'show', head+':'+PIN_FILE), base_src, head_src)
        verified_history = False
        if any(row['path'] == IMAGE_HISTORY for row in changes):
            import hashlib
            before = json.loads(git(root, 'show', comparison+':'+IMAGE_HISTORY))
            after = json.loads(git(root, 'show', head+':'+IMAGE_HISTORY))
            appended = False
            verified_history = True
            for key, path in IMAGE_HISTORY_FILES.items():
                if before.get(key, []) == after.get(key, []):
                    continue
                previous = before.pop(key, [])
                updated = after.pop(key, [])
                old_helper = hashlib.sha256(git(root, 'show', comparison+':'+path)).hexdigest()
                verified_history &= isinstance(previous, list) and updated == previous + [old_helper]
                appended = True
            verified_history &= appended and before == after
        scope = classify(changes, verified, verified_history)
        return {**result, 'scope': scope, 'base': base, 'comparison_base': comparison,
                'changes': changes, 'verified_src_pin_only': verified,
                'native': bool({row['path'] for row in changes} &
                               (NATIVE - {'src/api.py'} if scope == 'translation' else NATIVE)),
                'reason': ('documentation_only' if scope == 'docs' else
                           'related_files' if scope != 'full' else 'unknown_mixed_or_structural_change')}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event-name', required=True)
    parser.add_argument('--event-path', type=Path, required=True)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    try:
        event = json.loads(args.event_path.read_text())
    except (OSError, ValueError):
        event = {}
    result = select(Path.cwd(), args.event_name, event, args.expected_head)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('scope='+result['scope']+'\n')
        output.write('native='+str(result['native']).lower()+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
