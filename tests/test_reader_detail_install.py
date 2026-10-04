"""Exercise the actual narrow installer without executing the production entry."""
import ast
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
