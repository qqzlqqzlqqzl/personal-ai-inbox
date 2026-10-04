"""Visible local gate scope, including independent-review counterexamples."""
import json
import pytest
from content_quality import assess

URL='https://publisher.example/article'
PUBLIC='<script type="application/ld+json">'+json.dumps({'@type':'Article','url':URL,'isAccessibleForFree':True})+'</script><article>Complete freely accessible technical explanation.</article>'


@pytest.mark.parametrize('gate',[
    '<div hidden class="paywall">Subscribe to continue reading.</div>',
    '<section hidden><div class="paywall">Subscribe to continue reading.</div></section>',
    '<template><div class="paywall">Subscribe to continue reading.</div></template>',
    '<div style="display: none" class="paywall">Subscribe to continue reading.</div>',
    '<section style="visibility:hidden"><div class="paywall">Subscribe to continue reading.</div></section>',
])
def test_known_hidden_gate_does_not_override_public_article(gate):
    result=assess(url=URL,html_body=PUBLIC+gate,extraction_state='available')
    assert result['recommendation_eligible'] is True
    assert result['access']=='public'


@pytest.mark.parametrize('body',[
    '<article>Public preview.</article><div class="paywall"><p>This article is for paid subscribers only.</p><aside>Subscribe for free to our newsletter.</aside></div>',
    '<article>Public preview.</article><div class="paywall"><p>Subscribe to continue reading.</p><aside>Subscribe for free to our newsletter.</aside></div>',
    '<article>Public preview.</article><div class="paywall">This article is for paid subscribers only. Subscribe for free to our newsletter.</div>',
])
def test_neighboring_free_offer_does_not_cancel_paid_article_statement(body):
    result=assess(url=URL,html_body=body,extraction_state='available')
    assert result['recommendation_eligible'] is False
    assert result['access']=='paid_subscription'


def test_actual_free_gate_keeps_free_registration_distinction():
    result=assess(url=URL,html_body='<div class="paywall">Subscribe to continue reading. Subscribe for free.</div>',extraction_state='available')
    assert result['access']=='login_required'
    assert result['recommendation_eligible'] is None


def test_visible_one_article_payment_is_not_guessed_as_subscription():
    result=assess(url=URL,html_body='<div class="paywall">全文需付费</div>',extraction_state='available')
    assert result['access']=='paid_fulltext'
    assert result['recommendation_eligible'] is False
