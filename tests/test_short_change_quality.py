"""Synthetic contrast cases, separate from retained public release fixtures.

No production rows, access calls, provider requests or score assertions.
"""
import json
from pathlib import Path

import pytest

from content_quality import assess, meaningful_short


SAMPLES = json.loads((Path(__file__).parent / 'fixtures/public-llama-release-samples.json').read_text())
TEMPLATE = '**Website:**' + SAMPLES[0]['body'].split('**Website:**', 1)[1]
URL = 'https://github.com/example/project/releases/tag/synthetic'

CONCRETE = [
    'http: fix request smuggling by rejecting conflicting Content-Length and Transfer-Encoding headers (#456)',
    'server: fix a use-after-free when an active request is cancelled (#456)',
    'json: preserve integer precision by parsing IDs as 64-bit values (#456)',
    'kernel: prevent NULL pointer dereference when the device disconnects (#456)',
    'storage: avoid a deadlock by releasing the mutex before the callback (#456)',
    'parser: fix an out-of-bounds read by validating the buffer length (#456)',
]

GENERIC = [
    'fix a crash (#456)',
    'fix a crash when needed (#456)',
    'fix a crash by making it better (#456)',
    'fix a crash by fixing the server (#456)',
    'fix a crash when the release is ready (#456)',
    'improve performance when parsing JSON (#456)',
    'fix request smuggling (#456)',
    'request smuggling when a request is cancelled (#456)',
    'update preset validation (#456)',
]


@pytest.mark.parametrize('subject', CONCRETE)
def test_concrete_short_change_is_not_excluded_as_template_subject(subject):
    assert meaningful_short(subject) is True
    result = assess(url=URL, text=subject + '\n\n' + TEMPLATE, extraction_state='available')
    assert result['recommendation_eligible'] is True
    assert result['information'] == 'substantive'
    assert result['access'] == 'unknown'


@pytest.mark.parametrize('subject', GENERIC)
def test_generic_or_unexplained_subject_does_not_gain_short_exception(subject):
    assert meaningful_short(subject) is False
    result = assess(url=URL, text=subject + '\n\n' + TEMPLATE, extraction_state='available')
    assert result['recommendation_eligible'] is False
    assert result['reason_codes'] == ['release_subject_without_explanation']


@pytest.mark.parametrize('sample', SAMPLES, ids=lambda sample: sample['tag'])
def test_three_public_release_bodies_remain_separate_negative_controls(sample):
    result = assess(url=sample['url'], text=sample['body'], extraction_state='available')
    assert result['recommendation_eligible'] is False
    assert result['information'] == 'low_information'


def test_concrete_change_cannot_override_paid_gate_or_failed_extraction():
    body = '<article>' + CONCRETE[0] + '</article><div class="paywall">This article is for paid subscribers only.</div>'
    result = assess(url=URL, html_body=body, extraction_state='available')
    assert result['access'] == 'paid_subscription'
    assert result['recommendation_eligible'] is False
    failed = assess(url=URL, html_body=body, extraction_state='failed')
    assert failed['recommendation_eligible'] is None
    assert failed['information'] == failed['access'] == 'unknown'


@pytest.mark.parametrize('subject', CONCRETE)
def test_reviewed_fulltext_selector_keeps_concrete_short_change(subject):
    from fulltext_source import extract
    result = extract('<div class="Article"><div class="markdown">' + subject + '</div></div>',
                     'https://go.dev/blog/synthetic')
    assert result['source_text'] == subject
    assert result['content_quality']['recommendation_eligible'] is True


@pytest.mark.parametrize('subject', CONCRETE)
@pytest.mark.asyncio
async def test_worker_preserves_concrete_short_change_without_model_call(db, entry, monkeypatch, subject):
    from content_quality import public_for_row
    from test_content_quality_flow import process_body
    row = await process_body(db, entry, monkeypatch, '<article>' + subject + '</article>')
    assert row['state'] == 'waiting_model'
    assert row['score'] is None
    assert public_for_row(row)['recommendation_eligible'] is True


def test_existing_false_receipt_remains_until_explicit_source_bound_correction(db, entry):
    import core
    from content_quality import public_for_row
    source = CONCRETE[0] + '\n\n' + TEMPLATE
    entry = {**entry, 'url': URL}
    core.discover([entry])
    core.update(entry['id'], state='done', source_text=source, content_hash='fixed-synthetic-body', score=8)

    def saved():
        with core.connect() as connection:
            return dict(connection.execute('SELECT * FROM analyses WHERE entry_id=?', (entry['id'],)).fetchone())

    revised = assess(url=URL, text=source, extraction_state='available', observed_at=123)
    # An explicitly constructed old v1 receipt, matching the retained baseline
    # red evidence. It is not silently reinterpreted by a changed pure function.
    previous = {**revised, 'recommendation_eligible': False, 'information': 'low_information',
                'reason_codes': ['release_subject_without_explanation']}
    assert core.record_content_quality(saved(), previous)
    old = saved()
    assert public_for_row(old)['recommendation_eligible'] is False
    assert core.decorate(entry, entry['user_id'])['ai']['content_quality']['recommendation_eligible'] is False
    assert saved() == old
    # Computing a new assessment alone does not mutate the historical receipt.
    assert revised['recommendation_eligible'] is True
    assert not core.record_content_quality(old, revised, expected_quality='stale-receipt')
    assert saved() == old
    assert core.record_content_quality(old, revised, expected_quality=old['content_quality'])
    after = saved()
    assert public_for_row(after)['recommendation_eligible'] is True
    assert after['state'] == 'done' and after['score'] == 8
