import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core
import worker
from content_quality import public_for_row


async def process_body(db,entry,monkeypatch,body):
    entry={**entry,'content':body}
    core.discover([entry])
    monkeypatch.delenv('ARK_API_KEY',raising=False)
    monkeypatch.setattr(worker,'mf_get',AsyncMock(side_effect=[entry,{'content':body}]))
    monkeypatch.setattr(worker,'discover_original_cover',AsyncMock(return_value=(None,None)))
    monkeypatch.setattr(worker,'add_original_cover',AsyncMock(return_value=body))
    def forbidden_budget(*args,**kwargs):
        raise AssertionError('No model budget or request is authorized in this test')
    monkeypatch.setattr(worker,'reserve_budget',forbidden_budget)
    client=SimpleNamespace(post=AsyncMock(side_effect=AssertionError('No model request')))
    with core.connect() as connection:
        row=connection.execute('SELECT * FROM analyses WHERE entry_id=?',(entry['id'],)).fetchone()
    await worker.process_one(client,row,core.settings())
    client.post.assert_not_called()
    with core.connect() as connection:
        return dict(connection.execute('SELECT * FROM analyses WHERE entry_id=?',(entry['id'],)).fetchone())


@pytest.mark.asyncio
async def test_nonfree_metadata_is_saved_before_any_model_request(db,entry,monkeypatch):
    body='<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','url':entry['url'],'isAccessibleForFree':False})+'</script><article>Public preview.</article>'
    row=await process_body(db,entry,monkeypatch,body)
    assert row['state']=='content_excluded'
    assert row['score'] is None and row['result'] is None
    assert public_for_row(row)['recommendation_eligible'] is False
    assert public_for_row(row)['access']=='unknown'


@pytest.mark.asyncio
async def test_actionable_short_security_notice_is_not_blocked_by_length_alone(db,entry,monkeypatch):
    body='<article>CVE-2026-12345 permits remote code execution. Upgrade to 2.4 to prevent exploitation.</article>'
    row=await process_body(db,entry,monkeypatch,body)
    assert row['state']=='waiting_model'
    assert public_for_row(row)['recommendation_eligible'] is True


@pytest.mark.asyncio
async def test_short_technical_instruction_is_not_blocked_by_length_alone(db,entry,monkeypatch):
    body='<article>Use fsync before rename to preserve crash consistency.</article>'
    row=await process_body(db,entry,monkeypatch,body)
    assert row['state']=='waiting_model'
    assert public_for_row(row)['recommendation_eligible'] is True


def test_reviewed_html_selector_accepts_short_risk_and_mitigation():
    from fulltext_source import extract
    text='CVE-2026-12345 permits remote code execution. Upgrade to 2.4 to prevent exploitation.'
    result=extract('<div class="Article"><div class="markdown">'+text+'</div></div>','https://go.dev/blog/synthetic')
    assert result['source_text']==text
    assert result['content_quality']['recommendation_eligible'] is True


def test_short_generic_announcement_does_not_bypass_extraction_review():
    from fulltext_source import extract, FulltextUnavailable
    with pytest.raises(FulltextUnavailable,match='body_too_short_requires_review'):
        extract('<div class="Article"><div class="markdown">A new version is available.</div></div>','https://go.dev/blog/synthetic')


def test_security_label_alone_does_not_protect_download_template():
    from content_quality import assess
    text='security (#123)\n\nWebsite: project Attestations: proof macOS/iOS: app Linux: bin Android: app Windows: zip'
    record=assess(url='https://github.com/org/repo/releases/tag/v1',text=text,extraction_state='available')
    assert record['recommendation_eligible'] is False
