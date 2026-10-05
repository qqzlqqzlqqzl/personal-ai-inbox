"""Exercise the actual narrow installer without executing the production entry."""
import ast
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DetailInstallTests(unittest.TestCase):
    def fixture(self, reviewed=False):
        root = Path(tempfile.mkdtemp(prefix='reader-detail-install-'))
        web = root / 'upstream/reactflux'
        name = 'src/components/Content/Content.jsx'
        path = web / name; path.parent.mkdir(parents=True)
        original = subprocess.check_output(['git', '-C', str(ROOT / 'upstream/reactflux'), 'show',
            '534eeb97723ac11025de4ec1ac56335072e3be52:' + name], timeout=15).decode()
        if reviewed:
            original = original.replace('if (existingEntry) {', 'if (existingEntry && !existingEntry.content_deferred) {')
            original = original.replace('currentActiveContent?.id !== Number(entryId)',
                                        'currentActiveContent?.id !== Number(entryId) || currentActiveContent?.content_deferred')
        path.write_text(original)
        context = subprocess.check_output(['git', '-C', str(ROOT / 'upstream/reactflux'), 'show',
            '534eeb97723ac11025de4ec1ac56335072e3be52:src/components/Content/ContentContext.jsx'], timeout=15)
        (path.parent / 'ContentContext.jsx').write_bytes(context)
        entries_path = web / 'src/apis/entries.js'; entries_path.parent.mkdir(parents=True)
        entries_path.write_bytes(subprocess.check_output(['git', '-C', str(ROOT / 'upstream/reactflux'), 'show',
            '534eeb97723ac11025de4ec1ac56335072e3be52:src/apis/entries.js'], timeout=15))
        (web / 'src/utils').mkdir(parents=True)
        (root / 'patches').mkdir()
        helper = (ROOT / 'patches/reader-entry-detail.js').read_bytes()
        (root / 'patches/reader-entry-detail.js').write_bytes(helper)
        tree = ast.parse((ROOT / 'src/patch_frontend.py').read_text())
        fn, = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'install_reader_entry_detail']
        ns = {'backup': lambda name: None}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-detail-installer', 'exec'), ns)
        return root, web, path, ns['install_reader_entry_detail']

    def test_exact_native_and_reviewed_inputs_install_and_repeat(self):
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                root, web, path, install = self.fixture(reviewed)
                install(root, web); first = path.read_bytes()
                self.assertIn(b'useReaderEntryDetail({', first)
                self.assertNotIn(b'const fetchSingleEntry = useCallback(', first)
                self.assertEqual((web / 'src/utils/reader-entry-detail.js').read_bytes(),
                                 (root / 'patches/reader-entry-detail.js').read_bytes())
                install(root, web); self.assertEqual(path.read_bytes(), first)
                self.assertIn('getEntry = async (entryId, options = {})', (web / 'src/apis/entries.js').read_text())

    def test_shared_loader_is_public_and_legacy_hook_upgrades(self):
        root, web, path, install = self.fixture()
        install(root, web)
        first = path.read_text()
        self.assertIn('const ArticleDetail = lazy(loadArticleDetail)', first)
        self.assertEqual(first.count('import("@/components/Article/ArticleDetail")'), 1)
        self.assertIn('restoreEntryListFocus, loadArticleDetail })', first)
        # A reviewed installed Content from before the shared loader upgrade.
        legacy = first.replace('let articleDetailPromise\nconst loadArticleDetail = () => (articleDetailPromise ??= import("@/components/Article/ArticleDetail"))\nconst ArticleDetail = lazy(loadArticleDetail)',
                               'const ArticleDetail = lazy(() => import("@/components/Article/ArticleDetail"))')
        legacy = legacy.replace('restoreEntryListFocus, loadArticleDetail })', 'restoreEntryListFocus })')
        path.write_text(legacy)
        install(root, web)
        self.assertEqual(path.read_text(), first)

    def test_unknown_or_duplicate_loader_refuses_before_any_writes(self):
        for change in ('different-import', 'duplicate', 'missing'):
            with self.subTest(change=change):
                root, web, path, install = self.fixture()
                lazy = 'const ArticleDetail = lazy(() => import("@/components/Article/ArticleDetail"))'
                original = path.read_text()
                if change == 'different-import': original = original.replace('import("@/components/Article/ArticleDetail")', 'import("unknown")')
                elif change == 'duplicate': original += '\n' + lazy + '\n'
                else: original = original.replace(lazy, '')
                path.write_text(original)
                context = (path.parent / 'ContentContext.jsx').read_bytes()
                entries = (web / 'src/apis/entries.js').read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail module loader'):
                    install(root, web)
                self.assertEqual(path.read_text(), original)
                self.assertEqual((path.parent / 'ContentContext.jsx').read_bytes(), context)
                self.assertEqual((web / 'src/apis/entries.js').read_bytes(), entries)
                self.assertFalse((web / 'src/utils/reader-entry-detail.js').exists())

    def test_reviewed_previous_helper_upgrade_preserves_wiring(self):
        root, web, path, install = self.fixture()
        install(root, web); before = path.read_bytes()
        previous = subprocess.check_output(['git', '-C', str(ROOT), 'show',
            '3045d90dc1c4e4edc952705331c1b393fbba4ca1:patches/reader-entry-detail.js'], timeout=15)
        self.assertEqual(hashlib.sha256(previous).hexdigest(),
                         '82cc620457d6ee140e90a1edc85523d3c39532d5b51c36d0b9d26bf1cecc9ee4')
        target = web / 'src/utils/reader-entry-detail.js'
        target.write_bytes(previous)
        install(root, web)
        self.assertEqual(target.read_bytes(), (root / 'patches/reader-entry-detail.js').read_bytes())
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_transport_refuses_before_any_detail_writes(self):
        root, web, path, install = self.fixture(); before = path.read_bytes()
        entries = web / 'src/apis/entries.js'
        original = entries.read_text().replace('getEntry = async (entryId)', 'getEntry = async (otherId)')
        entries.write_text(original)
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail transport source'):
            install(root, web)
        self.assertEqual(entries.read_text(), original)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((web / 'src/utils/reader-entry-detail.js').exists())

    def test_transport_options_drift_and_duplicate_exports_refuse(self):
        for change in ('options-drift', 'duplicate'):
            with self.subTest(change=change):
                root, web, path, install = self.fixture(); install(root, web)
                entries = web / 'src/apis/entries.js'
                text = entries.read_text()
                text = (text.replace('${entryId}`, options)', '${entryId}`, {})') if change == 'options-drift'
                        else text + '\nexport const getEntry = async (other) => null\n')
                entries.write_text(text); before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail transport source'):
                    install(root, web)
                self.assertEqual(entries.read_text(), text)
                self.assertEqual(path.read_bytes(), before)

    def test_unknown_loading_block_refuses_before_writes(self):
        root, web, path, install = self.fixture()
        original = path.read_text().replace('const requestId = ++entryRequestIdRef.current', 'const requestId = 17')
        path.write_text(original)
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail loading source'):
            install(root, web)
        self.assertEqual(path.read_text(), original)
        self.assertFalse((web / 'src/utils/reader-entry-detail.js').exists())

    def test_unknown_helper_refuses_before_content_write(self):
        root, web, path, install = self.fixture(); before = path.read_bytes()
        helper = web / 'src/utils/reader-entry-detail.js'; helper.write_text('unknown helper')
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail helper'):
            install(root, web)
        self.assertEqual(path.read_bytes(), before); self.assertEqual(helper.read_text(), 'unknown helper')

    def test_malformed_new_wiring_refuses(self):
        root, web, path, install = self.fixture(); install(root, web)
        path.write_text(path.read_text().replace('import useReaderEntryDetail from', 'import unknownDetail from'))
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader detail wiring'):
            install(root, web)
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_close_source_refuses_before_content_write(self):
        root, web, path, install = self.fixture(); before = path.read_bytes()
        context = path.parent / 'ContentContext.jsx'
        context.write_text(context.read_text().replace('const closeActiveContent = useCallback(() => {', 'const closeActiveContent = () => {'))
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed reader close intent source'): install(root, web)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((web / 'src/utils/reader-entry-detail.js').exists())

    def test_close_navigation_and_focus_body_are_byte_preserved(self):
        root, web, path, install = self.fixture(); context = path.parent / 'ContentContext.jsx'; before = context.read_text()
        install(root, web)
        after = context.read_text().replace('import { invalidateReaderEntryDetail } from "@/utils/reader-entry-detail"\n', '', 1).replace('    invalidateReaderEntryDetail()\n', '', 1)
        self.assertEqual(after, before)

    def test_missing_authoring_helper_refuses(self):
        root, web, path, install = self.fixture(); before = path.read_bytes()
        source = root / 'patches/reader-entry-detail.js'; source.rename(root / 'patches/retained-reader-entry-detail.js')
        with self.assertRaises(FileNotFoundError): install(root, web)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
