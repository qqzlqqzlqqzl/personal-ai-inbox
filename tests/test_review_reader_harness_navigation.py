"""Navigation readiness must belong to the requested foreground surface."""
import unittest
from unittest.mock import MagicMock, patch

from review_reader_harness import Harness


class NavigationReadiness(unittest.TestCase):
    def fixture(self, entries=None):
        h = object.__new__(Harness)
        h.base = 'http://127.0.0.1:12345'
        h.page = MagicMock()
        h.entries = entries if entries is not None else [
            {'id': 101, 'title': 'Requested fixture', 'url': 'https://example.test/101'}]
        return h

    def test_list_keeps_original_visible_toolbar_wait(self):
        h = self.fixture()
        h.goto('/inbox/today')
        h.page.get_by_role.assert_called_once_with('button', name='AI 精选', exact=True)
        h.page.get_by_role.return_value.wait_for.assert_called_once_with()

    def test_detail_waits_body_and_exact_title_and_original_url(self):
        h = self.fixture()
        observed = []
        def assertion(locator):
            value = MagicMock()
            observed.append((locator, value))
            return value
        with patch('review_reader_harness.expect', side_effect=assertion):
            h.goto('/inbox/all/entry/101')
        h.page.get_by_role.assert_not_called()
        article = h.page.locator.return_value
        h.page.locator.assert_called_once_with('.article-content')
        observed[1][1].to_have_text.assert_called_once_with('Requested fixture')
        observed[3][1].not_to_have_attribute.assert_called_once_with('aria-busy', 'true')
        observed[4][1].to_have_attribute.assert_called_once_with('href', 'https://example.test/101')
        self.assertEqual(article.locator.call_args_list[-1].args, ('.article-source-footer a',))
        h.page.set_default_timeout.assert_not_called()

    def test_invalid_routes_are_rejected_before_navigation(self):
        for path in ('/inbox/all/entry/0', '/inbox/all/entry/abc', '/inbox/all/entry/101/extra'):
            h = self.fixture()
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'invalid isolated article route'):
                h.goto(path)
            h.page.goto.assert_not_called()

    def test_missing_requested_fixture_is_rejected_before_navigation(self):
        h = self.fixture()
        with self.assertRaisesRegex(ValueError, 'one matching isolated fixture'):
            h.goto('/inbox/all/entry/102')
        h.page.goto.assert_not_called()

    def test_ambiguous_identity_is_rejected_before_navigation(self):
        h = self.fixture([{'id': 101}, {'id': '101'}])
        with self.assertRaisesRegex(ValueError, 'one matching isolated fixture'):
            h.goto('/inbox/all/entry/101')
        h.page.goto.assert_not_called()
