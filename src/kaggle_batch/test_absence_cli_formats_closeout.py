"""Current 24-format matrix, authored for 2026-10-04 closeout.

This is not the missing historical reviewer's 24-case script. All inputs below
are synthetic. Independent review must execute this frozen file separately.
"""
import csv
import io
import json

import pytest

from absence_proof import parse_refs

WARNING = ("Warning: Looks like you're using an outdated `kaggle` version "
           "(installed: 2.2.3), please consider upgrading to the latest version (2.2.4)\n")
TOKEN = "Next Page Token = next-synthetic-page\n"


def csv_page(refs, title='Synthetic title', newline='\n'):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator=newline)
    writer.writerow(['ref', 'title', 'author', 'lastRunTime', 'totalVotes'])
    for ref in refs:
        writer.writerow([ref, title, 'Synthetic author', '2026-10-04 00:00:00', 0])
    return stream.getvalue()


CSV = csv_page(['Owner/Existing'])
JSON = json.dumps([{'ref': 'Owner/Existing'}])
# Same shape/counts as the reported incident; no original production bytes.
INCIDENT_SHAPE = csv_page([''] * 73 + [f'owner/known-{i}' for i in range(27)])

CASES = [
    ('01-plain-lf', CSV, ['owner/existing']),
    ('02-plain-crlf', csv_page(['Owner/Existing'], newline='\r\n'), ['owner/existing']),
    ('03-official-warning', WARNING + CSV, ['owner/existing']),
    ('04-official-next-page', TOKEN + CSV, ['owner/existing']),
    ('05-warning-and-next-page', WARNING + TOKEN + CSV, ['owner/existing']),
    ('06-quoted-multiline-metadata-is-data', csv_page(['Owner/Existing'], title='First\nNext Page Token = title-only\nThird'), ['owner/existing']),
    ('07-plain-json', JSON, ['owner/existing']),
    ('08-warning-json', WARNING + JSON, ['owner/existing']),
    ('09-token-json', TOKEN + JSON, ['owner/existing']),
    ('10-warning-official-empty', WARNING + 'Not found\n', []),
    ('11-incident-100-rows-73-empty-ref', INCIDENT_SHAPE, None),
    ('12-empty-json-reference', '[{"ref":""}]', None),
    ('13-bom-not-normalized', '\ufeff' + CSV, None),
    ('14-ansi-not-normalized', '\x1b[31m' + CSV, None),
    ('15-unrecognized-preamble', 'Informational output\n' + CSV, None),
    ('16-duplicate-warning', WARNING + WARNING + CSV, None),
    ('17-duplicate-token-prefix', TOKEN + TOKEN + CSV, None),
    ('18-token-before-warning', TOKEN + WARNING + CSV, None),
    ('19-header-only-not-terminal', csv_page([]), None),
    ('20-unterminated-last-row', CSV.rstrip('\n'), None),
    ('21-duplicate-ref-column', 'ref,ref\nowner/first,owner/second\n', None),
    ('22-incomplete-row', 'ref,title\nowner/existing\n', None),
    ('23-duplicate-json-ref-key', '[{"ref":"owner/first","ref":"owner/second"}]', None),
    ('24-continuation-cannot-be-empty-terminal', TOKEN + 'Not found\n', None),
]


@pytest.mark.parametrize('case_id,output,expected', CASES, ids=[case[0] for case in CASES])
def test_current_official_cli_formats(case_id, output, expected):
    if expected is None:
        with pytest.raises(ValueError):
            parse_refs(output)
    else:
        assert parse_refs(output) == expected


def test_incident_shaped_page_never_reaches_quota_or_absence_proof():
    from absence_proof import prove_absent
    calls = []
    # Include valid rows first: the parser must validate the entire 100-row page.
    output = csv_page([f'owner/known-{i}' for i in range(27)] + [''] * 73)
    def client(args, timeout):
        calls.append(args)
        assert args[:2] == ['kernels', 'list']
        assert timeout == 20
        return output
    with pytest.raises(ValueError, match='^invalid_kernel_ref$'):
        prove_absent(client, 'owner', 'still-unknown')
    assert len(calls) == 1
    assert not any(args[0] == 'quota' for args in calls)


# Reuse the current base's initialized fixture, not the old PR68 constructor API.
from test_recovery_safety import uncertain


def test_incident_shape_preserves_unknown_claims_and_cooldown(uncertain):
    from unittest.mock import patch
    from recovery_policy import ProviderError
    c, batch, calls, _manifest = uncertain
    with patch('batch_control.time.time', return_value=2000), pytest.raises(ProviderError):
        c.status(batch)
    receipt = c.root / batch / 'absence-observations.json'
    assert json.loads(receipt.read_text())['count'] == 1
    before = c.row(batch)
    with c.db() as db:
        claims = [tuple(row) for row in db.execute('SELECT * FROM batch_claims ORDER BY batch_id, entry_id')]
    cooldown = c.root / 'recovery.json'
    cooldown.write_text('{"failures":7,"retry_at":5000,"at":2000}')
    cooldown_bytes = cooldown.read_bytes()
    calls.clear()
    def client(args, timeout):
        calls.append(args)
        if args[:2] == ['kernels', 'status']:
            raise ProviderError('inaccessible')
        assert args[:2] == ['kernels', 'list']
        return INCIDENT_SHAPE
    c.client = client
    with patch('batch_control.time.time', return_value=2700), pytest.raises(ProviderError, match='^inaccessible$'):
        c.status(batch)
    assert c.row(batch) == before
    assert c.row(batch)['state'] == 'submit_unknown'
    assert json.loads(receipt.read_text())['count'] == 0
    with c.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM batch_claims ORDER BY batch_id, entry_id')] == claims
    assert claims and cooldown.read_bytes() == cooldown_bytes
    assert [args[:2] for args in calls] == [['kernels', 'status'], ['kernels', 'list']]
