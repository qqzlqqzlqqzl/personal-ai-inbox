"""Synthetic official CLI output only; no Kaggle requests or production state."""
import csv
import io
import json
import unittest

from absence_proof import MAX_BYTES, PAGE_SIZE, parse_page, parse_refs, prove_absent

HEADER = ['ref', 'title', 'author', 'lastRunTime', 'totalVotes']
WARNING = ("Warning: Looks like you're using an outdated `kaggle` version "
           "(installed: 2.2.3), please consider upgrading to the latest version (2.2.4)\n")


def page(*refs, title='Synthetic fixture', token=None):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(HEADER)
    for ref in refs:
        writer.writerow([ref, title, 'Synthetic author', '2026-10-01 00:00:00', 0])
    return (f'Next Page Token = {token}\n' if token is not None else '') + stream.getvalue()


class TestCliPageFormat(unittest.TestCase):
    def test_plain_csv_and_quoted_multiline_title(self):
        for title in ['Synthetic', 'Comma, and "quote"', 'Line one\nNext Page Token = title-content\nLine three']:
            with self.subTest(title=title):
                self.assertEqual(parse_refs(page('Owner/Existing', title=title)), ['owner/existing'])

    def test_official_prefixes_preserve_continuation(self):
        for prefix in ['', WARNING]:
            with self.subTest(warning=bool(prefix)):
                parsed = parse_page(prefix + page('owner/existing', token='synthetic-next-page'))
                self.assertEqual(parsed, {'refs': ['owner/existing'], 'next_page_token': 'synthetic-next-page'})
        self.assertEqual(parse_refs(WARNING + page('owner/existing')), ['owner/existing'])

    def test_official_empty_and_complete_json(self):
        for empty in ['Not found\n', 'Not found\r\n', '[]', WARNING + 'Not found\n']:
            with self.subTest(empty=empty):
                self.assertEqual(parse_page(empty), {'refs': [], 'next_page_token': None})
        self.assertEqual(parse_refs('[{"ref":"owner/existing"}]'), ['owner/existing'])

    def test_empty_continuation_never_becomes_terminal(self):
        for body in ['Not found\n', '[]', page()]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    parse_refs('Next Page Token = synthetic\n' + body)

    def test_malformed_metadata_fails_closed(self):
        for prefix in [
            'Next Page Token = \n', 'Next Page Token =  \n', 'Next Page Token = x y\n',
            'Next Page Token = ' + 'x' * 8193 + '\n',
            'Next Page Token = first\nNext Page Token = second\n',
            WARNING + WARNING, 'Next Page Token = synthetic\n' + WARNING,
            'unrecognized preamble\n', '\ufeff', '\x1b[31m',
        ]:
            with self.subTest(prefix=prefix[:80]):
                with self.assertRaises(ValueError):
                    parse_refs(prefix + page('owner/existing'))
        for output in ['Next Page Token = synthetic', WARNING.rstrip('\n'), '', '\n']:
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_refs(output)

    def test_metadata_inside_data_is_not_skipped(self):
        for line in ['Next Page Token = synthetic\n', WARNING, 'ref,title,author,lastRunTime,totalVotes\n']:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    parse_refs(page('owner/existing') + line)

    def test_header_and_row_integrity(self):
        for output in [
            'Ref,title\nowner/existing,t\n',
            'ref,ref\nowner/one,owner/two\n',
            'ref,title,title\nowner/existing,t,t\n',
            'ref,\nowner/existing,t\n',
            'ref,title\nowner/existing\n',
            'ref,title\nowner/existing,t,extra\n',
            'ref,title\n',
            'ref,title\n\n',
            'ref,title\nowner/existing,t',
            'ref,title\nowner/existing,"truncated\n',
            'ref,title\nowner/existing,"quoted"junk\n',
        ]:
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_refs(output)

    def test_invalid_refs_and_duplicate_json_keys(self):
        for ref in ['', 'owner', '/owner/existing', ' owner/existing', 'owner/existing ',
                    'https://www.kaggle.com/code/owner/existing', 'owner/existing/version', 'owner/existing?x=y']:
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    parse_refs(page(ref))
        for output in ['[{"ref":"owner/existing","ref":"owner/other"}]', '[null]', '[1]', '[{}]', '[{"ref":null}]', '[']:
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_refs(output)

    def test_response_and_row_bounds(self):
        self.assertEqual(len(parse_refs(page(*(f'owner/item-{i}' for i in range(PAGE_SIZE))))), PAGE_SIZE)
        for output in [page(*(f'owner/item-{i}' for i in range(PAGE_SIZE + 1))), 'x' * (MAX_BYTES + 1), None]:
            with self.subTest(kind=type(output).__name__):
                with self.assertRaises(ValueError):
                    parse_refs(output)


class TestCompleteOwnListing(unittest.TestCase):
    def client_for(self, pages, quota='[{"resource":"GPU","remaining":"0h"}]'):
        calls = []
        def client(args, timeout):
            calls.append(args)
            if args[0] == 'quota':
                return quota
            self.assertEqual(args[:5], ['kernels', 'list', '--mine', '--csv', '--page-size'])
            self.assertEqual(args[-2:], ['--sort-by', 'dateCreated'])
            index = int(args[args.index('--page') + 1]) - 1
            return pages[index]
        return client, calls

    def test_complete_numeric_pages_with_tokens_and_zero_quota(self):
        client, calls = self.client_for([
            WARNING + page('owner/first', token='synthetic-page-2'),
            page('owner/second', token='synthetic-page-3'), 'Not found\n'])
        proof = prove_absent(client, 'owner', 'missing')
        self.assertEqual((proof['listed_count'], proof['pages']), (2, 3))
        self.assertEqual(len(proof['listing_sha256']), 64)
        self.assertNotIn('synthetic-page', json.dumps(proof))
        self.assertEqual([args[args.index('--page') + 1] for args in calls if args[0] != 'quota'], ['1', '2', '3'])
        self.assertEqual(calls[-1], ['quota', '--format', 'json'])

    def test_short_page_without_token_is_not_a_terminal_page(self):
        client, calls = self.client_for([page('owner/first'), page('owner/target'), 'Not found\n'])
        self.assertIsNone(prove_absent(client, 'owner', 'target'))
        self.assertEqual(len(calls), 2)

    def test_ambiguous_list_never_queries_quota(self):
        cases = [
            ['Not found\n'],
            [page('foreign/first'), 'Not found\n'],
            [page('owner/first', 'foreign/second'), 'Not found\n'],
            [page('owner/first', 'OWNER/FIRST'), 'Not found\n'],
            [page('owner/first'), page('OWNER/FIRST')],
            [page('owner/first', token='same'), page('owner/second', token='same')],
            [page('owner/target')],
        ]
        for pages in cases:
            with self.subTest(pages=pages):
                client, calls = self.client_for(pages)
                self.assertIsNone(prove_absent(client, 'owner', 'target'))
                self.assertFalse(any(args[0] == 'quota' for args in calls))

    def test_truncated_or_contradictory_terminal_never_queries_quota(self):
        for final in [page(), page('owner/second').rstrip(), 'Next Page Token = more\nNot found\n', 'ref,title\n']:
            with self.subTest(final=final):
                client, calls = self.client_for([page('owner/first'), final])
                with self.assertRaises(ValueError):
                    prove_absent(client, 'owner', 'target')
                self.assertFalse(any(args[0] == 'quota' for args in calls))

    def test_ten_nonempty_pages_remain_inconclusive(self):
        client, calls = self.client_for([page(f'owner/item-{i}', token=f'synthetic-{i}') for i in range(10)])
        self.assertIsNone(prove_absent(client, 'owner', 'target'))
        self.assertEqual(len(calls), 10)

    def test_unreadable_quota_never_proves_absence(self):
        for quota in ['', 'not json', '[]', '[{"resource":"GPU","remaining":null}]']:
            with self.subTest(quota=quota):
                client, _ = self.client_for([page('owner/first'), 'Not found\n'], quota=quota)
                self.assertIsNone(prove_absent(client, 'owner', 'target'))


if __name__ == '__main__':
    unittest.main()
