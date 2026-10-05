import json
import core
from content_quality import assess


def test_changed_upstream_url_cannot_inherit_prior_nonfree_receipt(db, entry):
    core.discover([entry])
    core.update(entry['id'], state='done', source_text='Captured source A', content_hash='old-body')
    with core.connect() as connection:
        row=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=?',(entry['id'],)).fetchone())
    record=assess(url=entry['url'],html_body='<div class="paywall">This article is for paid subscribers only.</div>',extraction_state='available',observed_at=123)
    assert core.record_content_quality(row,record)
    replacement={**entry,'url':'https://example.org/different-current-article'}
    decorated=core.decorate(replacement,entry['user_id'])
    assert decorated['url']==replacement['url']
    assert decorated['ai']['content_quality']['recommendation_eligible'] is None


def test_correct_current_entry_keeps_bound_receipt(db, entry):
    core.discover([entry])
    core.update(entry['id'], state='done', source_text='Captured source A', content_hash='old-body')
    with core.connect() as connection:
        row=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=?',(entry['id'],)).fetchone())
    record=assess(url=entry['url'],html_body='<div class="paywall">This article is for paid subscribers only.</div>',extraction_state='available',observed_at=123)
    assert core.record_content_quality(row,record)
    assert core.decorate(entry,entry['user_id'])['ai']['content_quality']['recommendation_eligible'] is False
