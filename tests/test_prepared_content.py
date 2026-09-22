import core
import card_translation as cards
import prepared_content as prepared


def product(entry):
    return {**entry,'url':'https://www.producthunt.com/products/example'}


def test_feed_poll_cannot_erase_product_preparation_or_translation(db,entry):
    entry = product(entry)
    body = '<h2>产品介绍（Product Hunt）</h2><p>Real source description.</p>'
    body += '<img src="https://ph-files.imgix.net/large.jpg?w=1200">'
    body += '<h3>原始 RSS 简介</h3>' + entry['content']
    prepared.remember(entry,body,'product_page')
    full = {**entry,'content':body}
    cards.enqueue([full])
    with core.connect() as c:
        c.execute("UPDATE card_translations SET status='done',title_zh='示例产品',summary_zh='真实介绍的中文译文。',translated_at=123")
        before = tuple(c.execute('SELECT source_hash,title_zh,translated_at FROM card_translations').fetchone())
    # Simulate the reader returning the original RSS again after its next poll.
    cards.enqueue([entry])
    shown = core.decorate(entry,entry['user_id'])
    assert shown['content'] == body
    assert shown['card']['title'] == '示例产品'
    with core.connect() as c:
        assert tuple(c.execute('SELECT source_hash,title_zh,translated_at FROM card_translations').fetchone()) == before
        assert c.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 0


def test_changed_product_identity_and_long_new_source_are_not_hidden(db,entry):
    entry = product(entry)
    prepared.remember(entry,'<p>Enriched product body</p>','product_page')
    for changed in ({**entry,'title':'Changed title'}, {**entry,'url':entry['url']+'-different'},
                    {**entry,'user_id':2}, {**entry,'content':'<p>'+('new text '*150)+'</p>'}):
        assert prepared.apply(changed) == changed


def test_image_repair_is_durable_but_does_not_mask_new_article_text(db,entry):
    original = {**entry,'content':'<p>Original text.</p><img alt="Diagram" src="data:image/svg+xml,%3Csvg%3E">'}
    repaired = '<p>Original text.</p><img alt="Diagram" src="https://example.org/figure.png">'
    prepared.remember(original,repaired,'body_images_repaired')
    assert prepared.apply(original)['content'] == repaired
    changed = {**original,'content':original['content'].replace('Original text.','New article text.')}
    assert prepared.apply(changed)['content'] == changed['content']
