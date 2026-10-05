"""Pinned-source contracts only; no browser timing or production observations.

This file can run with Python's standard library, without the API test packages.
Retain owned fixture directories instead of deleting files on Windows.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import install_agent_status as installer

FIXTURE = json.loads((ROOT / 'tests/fixtures/reader-startup-pinned-sources.json').read_text(encoding='utf-8'))


def populate_startup_sources(root):
    web = Path(root) / 'upstream/reactflux'
    for name, row in FIXTURE['files'].items():
        destination = web / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(row['text'], encoding='utf-8')
    return web


class StartupInstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'runtime/retained-startup-tests' / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.patchers = [
            patch.object(installer, 'PANEL_BEFORE', hashlib.sha256(installer.PANEL_ANCHOR.encode()).hexdigest()),
            patch.object(installer, 'TOOLBAR_BEFORE', hashlib.sha256(b'reviewed toolbar').hexdigest()),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_root(self, name='fixture'):
        root = self.directory / name
        web = populate_startup_sources(root)
        (web / 'UPSTREAM_REVISION').write_text(installer.PIN, encoding='utf-8')
        ai = web / 'src/components/Ai'
        ai.mkdir(parents=True)
        (ai / 'AiToolbar.jsx').write_text('reviewed toolbar', encoding='utf-8')
        (ai / 'AiPanel.jsx').write_text(installer.PANEL_ANCHOR, encoding='utf-8')
        # The final prior overlay's auth/expiry/401 logic stays untouched.
        shutil.copy2(ROOT / 'frontend-review/after/src/pages/RouterProtect.jsx', web / 'src/pages/RouterProtect.jsx')
        shutil.copytree(ROOT / 'patches/agent-status', root / 'patches/agent-status')
        return root, web

    @staticmethod
    def snapshot(root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    def test_snapshots_match_the_four_exact_reviewed_guards(self):
        self.assertEqual(FIXTURE['upstream'], installer.PIN)
        constants = {
            'src/routes.jsx': installer.ROUTES_BEFORE,
            'src/pages/AuthenticatedApp.jsx': installer.AUTHENTICATED_BEFORE,
            'src/pages/ContentPages.jsx': installer.CONTENT_PAGES_BEFORE,
            'src/components/Main/Main.jsx': installer.MAIN_BEFORE,
        }
        for name, expected in constants.items():
            self.assertEqual(hashlib.sha256(FIXTURE['files'][name]['text'].encode()).hexdigest(), expected)

    def test_complete_install_is_idempotent_and_keeps_the_auth_gate(self):
        root, web = self.make_root()
        auth_gate = (web / 'src/pages/RouterProtect.jsx').read_bytes()
        installer.install(root)
        first = self.snapshot(root)
        installer.install(root)
        self.assertEqual(first, self.snapshot(root))
        self.assertEqual(auth_gate, (web / 'src/pages/RouterProtect.jsx').read_bytes())
        routes = (web / 'src/routes.jsx').read_text(encoding='utf-8')
        self.assertEqual(routes.count('path: "agent-status"'), 1)
        self.assertIn('import("./pages/ErrorPage")', routes)
        self.assertIn('return { Component, ErrorBoundary }', routes)
        self.assertIn('lazy: lazyRoute(() => import("./pages/RouterProtect"))', routes)
        self.assertNotIn('loadContentPage', routes)
        self.assertIn('{ path: `/${path}/entry/:entryId`, Component: contentPageComponents[pageKey] }', routes)
        self.assertNotIn('mock-state.json', first)

    def test_upgrades_the_exact_previous_status_overlay(self):
        root, web = self.make_root()
        routes = web / 'src/routes.jsx'
        routes.write_text(routes.read_text(encoding='utf-8').replace(installer.ANCHOR, installer.ADDITION, 1), encoding='utf-8')
        panel = web / 'src/components/Ai/AiPanel.jsx'
        panel.write_text(installer.LINK_IMPORT + installer.LINK + installer.PANEL_ANCHOR, encoding='utf-8')
        installer.install(root)
        self.assertEqual(routes.read_text(encoding='utf-8'), installer._routes_apply(FIXTURE['files']['src/routes.jsx']['text']))
        self.assertEqual(panel.read_text(encoding='utf-8').count('to="/agent-status"'), 1)

    def test_drift_in_each_startup_input_refuses_before_any_write(self):
        paths = ['src/routes.jsx', 'src/pages/AuthenticatedApp.jsx', 'src/pages/ContentPages.jsx',
                 'src/components/Main/Main.jsx', 'src/components/DeferredComponent.jsx',
                 'src/pages/LoadedContentPages.jsx']
        for index, name in enumerate(paths):
            with self.subTest(name=name):
                root, web = self.make_root(str(index))
                path = web / name
                original = path.read_text(encoding='utf-8') if path.exists() else (
                    installer.LOADED_CONTENT_PAGES if name.endswith('/LoadedContentPages.jsx') else installer.DEFERRED_COMPONENT
                )
                path.write_text(original + '\n// another collaborator\n', encoding='utf-8')
                before = self.snapshot(root)
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
                    installer.install(root)
                self.assertEqual(before, self.snapshot(root))

    def test_drift_after_install_and_duplicate_generated_fragments_are_refused(self):
        for index, name in enumerate(['src/routes.jsx', 'src/pages/AuthenticatedApp.jsx', 'src/pages/ContentPages.jsx', 'src/components/Main/Main.jsx', 'src/pages/LoadedContentPages.jsx']):
            with self.subTest(name=name):
                root, web = self.make_root(str(index))
                installer.install(root)
                path = web / name
                path.write_text(path.read_text(encoding='utf-8') + '\n// drift after install\n', encoding='utf-8')
                before = self.snapshot(root)
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
                    installer.install(root)
                self.assertEqual(before, self.snapshot(root))
        root, web = self.make_root('duplicate')
        installer.install(root)
        routes = web / 'src/routes.jsx'
        routes.write_text(installer.STARTUP_ROUTE_IMPORT + routes.read_text(encoding='utf-8'), encoding='utf-8')
        before = self.snapshot(root)
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
            installer.install(root)
        self.assertEqual(before, self.snapshot(root))

    def test_business_imports_are_deferred_inside_independent_surfaces(self):
        root, web = self.make_root()
        installer.install(root)
        auth = (web / 'src/pages/AuthenticatedApp.jsx').read_text(encoding='utf-8')
        self.assertNotIn('import App from', auth)
        self.assertIn('const App = deferComponent(() => import("@/App"))', auth)
        self.assertIn('<AppDataProvider>\n    <App />\n  </AppDataProvider>', auth)
        pages = (web / 'src/pages/ContentPages.jsx').read_text(encoding='utf-8')
        self.assertEqual(pages.count('deferComponent('), 1)
        self.assertIn('await import("./LoadedContentPages")', pages)
        self.assertNotIn('import All from', pages)
        loaded = (web / 'src/pages/LoadedContentPages.jsx').read_text(encoding='utf-8')
        self.assertEqual(loaded, FIXTURE['files']['src/pages/ContentPages.jsx']['text'])
        self.assertIn('const Deferred = lazy(loadComponent)', installer.DEFERRED_COMPONENT)
        self.assertIn('<Suspense fallback={<LoadingSurface />}>', installer.DEFERRED_COMPONENT)
        self.assertNotIn('prefetch', installer.DEFERRED_COMPONENT)

    def test_legacy_early_overlay_upgrades_without_moving_auth_or_provider(self):
        root, web = self.make_root()
        routes = web / 'src/routes.jsx'
        auth = web / 'src/pages/AuthenticatedApp.jsx'
        main = web / 'src/components/Main/Main.jsx'
        routes.write_text(installer._routes_apply(routes.read_text(encoding='utf-8')), encoding='utf-8')
        auth.write_text(installer._authenticated_apply(auth.read_text(encoding='utf-8')), encoding='utf-8')
        main.write_text(installer._main_apply(main.read_text(encoding='utf-8')), encoding='utf-8')
        (web / 'src/pages/ContentPages.jsx').write_text(installer.LEGACY_CONTENT_PAGES, encoding='utf-8')
        (web / 'src/components/DeferredComponent.jsx').write_text(installer.DEFERRED_COMPONENT, encoding='utf-8')
        auth_before = auth.read_bytes()
        installer.install(root)
        self.assertEqual(auth_before, auth.read_bytes())
        self.assertEqual((web / 'src/pages/ContentPages.jsx').read_text(encoding='utf-8'), installer.CONTENT_PAGES)
        self.assertEqual((web / 'src/pages/LoadedContentPages.jsx').read_text(encoding='utf-8'),
                         FIXTURE['files']['src/pages/ContentPages.jsx']['text'])

    def test_shared_selector_keeps_one_lazy_identity_and_original_page_types(self):
        root, web = self.make_root()
        installer.install(root)
        pages = (web / 'src/pages/ContentPages.jsx').read_text(encoding='utf-8')
        self.assertLess(pages.index('const SharedContentPage = deferComponent('), pages.index('const contentRoute ='))
        self.assertEqual(pages.count('deferComponent('), 1)
        self.assertEqual(pages.count('await import('), 1)
        self.assertIn('const Page = pages[pageKey]\n    return <Page {...props} />', pages)
        self.assertIn('return <SharedContentPage {...props} pageKey={pageKey} />', pages)
        self.assertNotIn('key={', pages)
        self.assertNotIn('contentState', pages)
        self.assertNotIn('dynamicCount', pages)
        self.assertNotIn('page-info', pages)
        for scope in ('all', 'category', 'feed', 'history', 'starred', 'today'):
            self.assertIn(f'{scope}: contentRoute("{scope}")', pages)
        routes = (web / 'src/routes.jsx').read_text(encoding='utf-8')
        self.assertEqual(routes.count('Component: contentPageComponents[pageKey]'), 2)

    def test_loaded_page_authoring_and_duplicate_selector_refuse_before_write(self):
        root, web = self.make_root('authoring')
        before = self.snapshot(root)
        with patch.object(installer, 'LOADED_CONTENT_PAGES', installer.LOADED_CONTENT_PAGES + '\n// drift\n'):
            with self.assertRaisesRegex(RuntimeError, 'Unreviewed loaded page authoring'):
                installer.install(root)
        self.assertEqual(before, self.snapshot(root))
        root, web = self.make_root('duplicate-selector')
        (web / 'src/pages/ContentPages.jsx').write_text(installer.CONTENT_PAGES + installer.CONTENT_PAGES, encoding='utf-8')
        before = self.snapshot(root)
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed content pages'):
            installer.install(root)
        self.assertEqual(before, self.snapshot(root))

    def test_settings_keep_original_modal_and_only_render_content_when_visible(self):
        root, web = self.make_root()
        installer.install(root)
        main = (web / 'src/components/Main/Main.jsx').read_text(encoding='utf-8')
        self.assertIn('{settingsModalVisible && (', main)
        self.assertNotIn('import SettingsModalContent from', main)
        self.assertIn('deferComponent(() => import("@/components/Settings/SettingsModalContent"))', main)
        # Reversing only the import, constant and child wrapper yields exact
        # reviewed Main bytes, including every AccessibleModal/focus setting.
        self.assertEqual(installer._main_undo(main), FIXTURE['files']['src/components/Main/Main.jsx']['text'])

    def test_missing_status_authoring_file_refuses_before_any_write(self):
        root, web = self.make_root()
        # Move an owned synthetic input; retain it, never delete.
        file = root / 'patches/agent-status/status.css'
        file.rename(root / 'retained-status.css')
        before = self.snapshot(root)
        with self.assertRaisesRegex(RuntimeError, 'Missing status authoring file'):
            installer.install(root)
        self.assertEqual(before, self.snapshot(root))


if __name__ == '__main__':
    unittest.main(verbosity=2)
