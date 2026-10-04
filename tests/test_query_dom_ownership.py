"""Complete synthetic DOM fixtures; never fabricate missing hosted observations."""
import copy
import json
from pathlib import Path
import unittest

from query_dom_ownership import assess_pending_ui, identity


def frame(sequence, at, url='all', selected='all', count='1965', ids=('101', '102')):
    label = {'all': '全部', 'today': '今天'}[selected]
    header = label + ' · AI精选' + (' · Asia/Shanghai' if selected == 'today' else '')
    if count:
        header += '\n(' + count + ')'
    return {'sequence': sequence, 'time': at, 'kind': 'raf', 'scope': url,
            'location': {'pathname': '/inbox/' + url}, 'count': count * 2, 'ids': list(ids),
            'dom': {'selected_scope': selected, 'selected_label': label,
                    'selected_count': 1, 'selected_candidates': [{'scope': selected, 'label': label}],
                    'count_rendered_text': count, 'headers': [{'rendered_text': header}]}}


def fixture():
    baseline = frame(1, 100)
    return baseline, [baseline, frame(2, 120, url='today'), frame(3, 140, 'today', 'today', '', ())]


class Ownership(unittest.TestCase):
    def test_exact_ccc_preserves_old_red_without_inventing_menu_uniqueness(self):
        source = Path(__file__).parent / 'fixtures/query-ccc-dom-observations.json'
        data = json.loads(source.read_text())
        self.assertEqual(data['source_query_frames_sha256'], '871173408bca15b2b1783598d18d901701b3d45bc28a4f61f6134b8d49e70649')
        frames = data['frames']
        self.assertEqual(len(frames), 60)
        self.assertFalse(all(not f['count'] and not f['ids'] for f in frames if f['scope'] == 'today'))
        lead = [f for f in frames if f['scope'] == 'today' and identity(f)[0] == 'all']
        self.assertEqual([f['sequence'] for f in lead], [86, 87])
        self.assertEqual({f['rafTime'] for f in lead}, {1040.7})
        committed = [f for f in frames if identity(f)[0] == 'today']
        self.assertEqual(len(committed), 44)
        self.assertTrue(all(not f['count'] and not f['ids'] for f in committed))
        self.assertTrue(all('selected_count' not in f['dom'] for f in frames))
        strict = assess_pending_ui('today', frames[0], frames, dropped=0)
        self.assertFalse(strict['passed'])
        self.assertEqual(strict['violations'][0]['reason'], 'unique_menu_identity_not_proven')

    def test_complete_old_ui_is_retained_and_verified(self):
        baseline, frames = fixture()
        result = assess_pending_ui('today', baseline, frames, dropped=0)
        self.assertTrue(result['passed'])
        self.assertEqual(result['old_ui_sequences'], [1, 2])
        self.assertEqual(result['url_lead_sequences'], [2])
        self.assertEqual(result['target_ui_sequences'], [3])
        self.assertEqual(result['first_target_after_ms'], 40)
        self.assertFalse(result['paint_verified'])

    def test_reverse_transition_from_pending_today(self):
        baseline = frame(10, 100, 'today', 'today', '', ())
        frames = [baseline, frame(11, 120, 'all', 'today', '', ()), frame(12, 140, 'all', 'all', '', ())]
        self.assertTrue(assess_pending_ui('all', baseline, frames, dropped=0)['passed'])

    def test_bad_today_title_and_old_data_fail_in_every_callback_kind(self):
        for kind in ['mutation', 'raf', 'mutation-raf']:
            with self.subTest(kind=kind):
                baseline, frames = fixture()
                frames[-1] = frame(3, 140, 'today', 'today')
                frames[-1]['kind'] = kind
                result = assess_pending_ui('today', baseline, frames, dropped=0)
                self.assertFalse(result['passed'])
                self.assertIn('target_pending_borrows_result', [v['reason'] for v in result['violations']])

    def test_identity_conflicts_and_missing_fields_fail(self):
        for change in ['menu_header', 'header_count', 'rows', 'duplicate_menu', 'missing_menu',
                       'missing_header', 'multiple_headers', 'unknown_header', 'wrong_lens',
                       'candidate_conflict', 'url_conflict', 'order', 'late']:
            with self.subTest(change=change):
                baseline, frames = fixture(); target = frames[-1]
                if change == 'menu_header': target['dom']['headers'][0]['rendered_text'] = '全部 · AI精选'
                if change == 'header_count': target['dom']['headers'][0]['rendered_text'] += '\n(1965)'
                if change == 'rows': target['ids'] = ['101']
                if change == 'duplicate_menu':
                    target['dom']['selected_count'] = 2
                    target['dom']['selected_candidates'].append({'scope': 'all', 'label': '全部'})
                if change == 'missing_menu': target['dom'].pop('selected_count')
                if change == 'missing_header': target['dom']['headers'] = []
                if change == 'multiple_headers': target['dom']['headers'].append({'rendered_text': '全部 · AI精选'})
                if change == 'unknown_header': target['dom']['headers'][0]['rendered_text'] = 'ErrorPage'
                if change == 'wrong_lens': target['dom']['headers'][0]['rendered_text'] = '今天 · 全部原始 · Asia/Shanghai'
                if change == 'candidate_conflict': target['dom']['selected_candidates'][0]['scope'] = 'all'
                if change == 'url_conflict': target['location']['pathname'] = '/inbox/all'
                if change == 'order': target['sequence'] = 1
                if change == 'late': target['time'] = 5101
                self.assertFalse(assess_pending_ui('today', baseline, frames, dropped=0)['passed'])

    def test_old_ui_cannot_mutate_or_reappear_after_target(self):
        for change in ['count_and_rows', 'only_rows', 'menu_and_header', 'regress_after_commit']:
            with self.subTest(change=change):
                baseline, frames = fixture()
                if change == 'count_and_rows': frames[1] = frame(2, 120, url='today', count='2', ids=('401', '402'))
                if change == 'only_rows': frames[1]['ids'] = ['401', '402']
                if change == 'menu_and_header': frames[1]['dom']['headers'][0]['rendered_text'] = '今天 · AI精选 · Asia/Shanghai\n(1965)'
                if change == 'regress_after_commit': frames.append(frame(4, 150, url='today'))
                self.assertFalse(assess_pending_ui('today', baseline, frames, dropped=0)['passed'])

    def test_exact_deadline_and_absence_of_commit(self):
        baseline, frames = fixture()
        self.assertFalse(assess_pending_ui('today', baseline, frames[:-1], dropped=0)['passed'])
        frames[-1]['time'] = 5100
        self.assertTrue(assess_pending_ui('today', baseline, frames, dropped=0)['passed'])
        frames[-1]['time'] = 5100.001
        self.assertFalse(assess_pending_ui('today', baseline, frames, dropped=0)['passed'])
        for dropped in (1, None, True):
            self.assertFalse(assess_pending_ui('today', baseline, frames, dropped=dropped)['passed'])

    def test_untyped_or_overflowing_observations_fail_without_exception(self):
        for field, value in [('scope', []), ('location', []), ('dom', None), ('ids', '101'),
                             ('sequence', True), ('time', 10 ** 500), ('time', float('nan'))]:
            with self.subTest(field=field, type=type(value).__name__):
                baseline, frames = fixture(); frames[-1][field] = value
                self.assertFalse(assess_pending_ui('today', baseline, frames, dropped=0)['passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
