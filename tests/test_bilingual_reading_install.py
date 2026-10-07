"""Direct offline installer checks; never run the production patch entry point."""
import ast
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / 'frontend-review/after/src/components/Article/ArticleDetail.jsx'


class BilingualReadingInstallTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='reader-bilingual-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        web = root / 'upstream/reactflux'
        (root / 'patches').mkdir()
        for name in ('BilingualReading.jsx', 'BilingualReading.css'):
            (root / 'patches' / name).write_bytes((ROOT / 'patches' / name).read_bytes())
        tree = ast.parse((ROOT / 'src/patch_frontend.py').read_text())
        fn, = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == 'install_bilingual_reading']
        namespace = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'actual-bilingual-installer', 'exec'), namespace)
        return root, web, namespace['install_bilingual_reading']

    def test_installs_owned_files_and_repeats_without_touching_detail(self):
        root, web, install = self.fixture()
        detail = web / 'src/components/Article/ArticleDetail.jsx'
        detail.parent.mkdir(parents=True)
        detail.write_text('pinned source stays unchanged')
        install(root, web)
        for name in ('BilingualReading.jsx', 'BilingualReading.css'):
            target = web / 'src/components/Ai' / name
            self.assertEqual(target.read_bytes(), (root / 'patches' / name).read_bytes())
        self.assertEqual(detail.read_text(), 'pinned source stays unchanged')
        before = {path.relative_to(web): path.read_bytes() for path in web.rglob('*') if path.is_file()}
        install(root, web)
        self.assertEqual(before, {path.relative_to(web): path.read_bytes() for path in web.rglob('*') if path.is_file()})

    def test_unknown_helper_refuses_before_writing_the_other_file(self):
        for name in ('BilingualReading.jsx', 'BilingualReading.css'):
            with self.subTest(name=name):
                root, web, install = self.fixture()
                target = web / 'src/components/Ai' / name
                target.parent.mkdir(parents=True)
                target.write_text('unreviewed existing file')
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed bilingual reading helper'):
                    install(root, web)
                self.assertEqual(target.read_text(), 'unreviewed existing file')
                self.assertEqual(len(list(target.parent.iterdir())), 1)

    def test_missing_source_refuses_before_any_writes(self):
        root, web, install = self.fixture()
        (root / 'patches/BilingualReading.css').unlink()
        with self.assertRaises(FileNotFoundError):
            install(root, web)
        self.assertFalse(web.exists())

    def test_live_detail_keeps_one_toolbar_parser_and_image_channel(self):
        text = DETAIL.read_text()
        self.assertIn('const translation = useBilingualTranslation(activeContent)', text)
        self.assertIn('const activeContentHtml = bilingualReading.html', text)
        self.assertLess(text.index('getBilingualReading({ ...activeContent, translation }, readingMode)'),
                        text.index('useDeferredValue(activeContentHtml, "")'))
        self.assertEqual(text.count('ReactHtmlParser(renderableContentHtml, htmlParserOptions)'), 1)
        self.assertEqual(text.count('buildArticleImageModel(renderableContentHtml, attachmentItems)'), 1)
        self.assertEqual(text.count('<ReadingControls'), 1)
        self.assertEqual(text.count('<BilingualReading'), 1)
        self.assertEqual(text.count('<ArticleNote entry={activeContent} key={activeContent.id} />'), 1)
        self.assertLess(text.index('<ReadingControls'), text.index('<BilingualReading'))
        self.assertLess(text.index('<BilingualReading'), text.index('</ReadingControls>'))
        controls = (ROOT / 'frontend-review/after/src/components/Ai/ReadingControls.jsx').read_text()
        self.assertEqual(controls.count('className="review-reading-bar"'), 1)
        self.assertEqual(controls.count('{children}'), 1)

    def test_detail_prefetch_and_list_helpers_have_no_translation_side_effect(self):
        for name in ('reader-entry-detail.js', 'ProgressiveLoadMore.jsx'):
            text = (ROOT / 'patches' / name).read_text()
            self.assertNotIn('/ai/translation/', text)
            self.assertNotIn('useBilingualTranslation', text)


if __name__ == '__main__':
    unittest.main()
