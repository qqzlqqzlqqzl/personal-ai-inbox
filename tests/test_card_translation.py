import json
import httpx
import pytest
import core
import card_translation as cards

async def translate(entry, monkeypatch, invalid=False):
    monkeypatch.setenv('ARK_API_KEY','isolated-model-key')
    calls=[]
    def responder(req):
        calls.append(req)
        body=json.loads(req.content)
        inputs=json.loads(body['messages'][1]['content'])['items']
        output=[{'id':x['id'] if not invalid else 99999,
                 'title':'示例产品：减少延迟的技术方案','summary':'原文介绍了减少延迟的技术方案。'} for x in inputs]
        return httpx.Response(200,json={'usage':{'total_tokens':99},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'items':output},ensure_ascii=False)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        result=await cards.run_once(client)
    return calls,result

@pytest.mark.asyncio
async def test_translation_independent_of_scoring_and_cached(db,entry,monkeypatch):
    core.discover([entry]);core.save_settings({'enabled':False})
    core.update(entry['id'],state='fetch_error',attempts=2,next_try=999)
    calls,out=await translate(entry,monkeypatch)
    assert len(calls)==1 and out['processed']==1
    cards.enqueue([entry],priority=30)
    assert (await translate(entry,monkeypatch))[0]==[]
    decorated=core.decorate(entry,1)
    assert decorated['title']==entry['title'] and decorated['content']==entry['content']
    assert decorated['card']['language']=='zh-CN' and decorated['card']['status']=='done'
    assert decorated['ai']['state']=='fetch_error'
    with core.connect() as c:
        r=c.execute('SELECT purpose,actual FROM usage').fetchone()
        assert tuple(r)==('translation',99)
        r=c.execute('SELECT attempts,next_try FROM analyses WHERE entry_id=?',(entry['id'],)).fetchone()
        assert tuple(r)==(2,999)

@pytest.mark.asyncio
async def test_source_version_change_queues_new_translation(db,entry,monkeypatch):
    core.discover([entry]);await translate(entry,monkeypatch)
    new={**entry,'content':'<p>Updated introduction with a different feature.</p>'}
    cards.enqueue([new])
    assert cards.attach(new,1)['card']['status']=='pending'
    assert (await translate(new,monkeypatch))[1]['processed']==1

def test_native_chinese_not_sent_to_model(db,entry):
    entry={**entry,'title':'中文技术实践','content':'<p>这里介绍一个中文技术实现方案。</p>'}
    cards.enqueue([entry])
    assert cards.attach(entry,1)['card']['status']=='native'

@pytest.mark.asyncio
async def test_invalid_model_ids_are_not_saved(db,entry,monkeypatch):
    core.discover([entry])
    calls,out=await translate(entry,monkeypatch,True)
    assert out['failed']==1
    assert cards.attach(entry,1)['card']['status']=='error'
    assert 'title' not in cards.attach(entry,1)['card']
    with core.connect() as db:
        assert db.execute('SELECT actual FROM usage').fetchone()[0]==99

@pytest.mark.asyncio
async def test_shared_budget_blocks_translation_when_analysis_spent_it(db,entry,monkeypatch):
    core.discover([entry]);core.save_settings({'daily_articles':1})
    assert core.reserve_budget(entry['id'],'analysis',core.settings()) is not None
    calls,out=await translate(entry,monkeypatch)
    assert calls==[] and out['budget_paused']

@pytest.mark.asyncio
async def test_translation_toggle_does_not_change_score_toggle(db,entry,monkeypatch):
    core.discover([entry]);core.save_settings({'translation_enabled':False,'enabled':True})
    assert (await translate(entry,monkeypatch))[0]==[]
    assert core.settings()['enabled'] is True

def test_translation_read_is_user_scoped(db,entry):
    core.discover([entry])
    assert cards.attach(entry,2)['card']['status']=='pending'
    assert 'source_kind' not in cards.attach(entry,2)['card']

def test_rejects_english_disguised_as_completed(entry):
    rows=[{'entry_id':1}]
    with pytest.raises(ValueError):
        cards.validate_items(json.dumps({'items':[{'id':1,'title':'English title','summary':'English summary'}]}),rows)


def test_product_brand_only_title_gets_label_from_translated_introduction():
    rows=[{'entry_id':1,'original_title':'Keet','source_kind':'product_page'}]
    raw=json.dumps({'items':[{'id':1,'title':'Keet','summary':'通过交互式视频课程学习任何主题。'}]},ensure_ascii=False)
    result=cards.validate_items(raw,rows)
    assert result[1][0]=='Keet｜通过交互式视频课程学习任何主题'


def test_technical_chinese_allows_long_english_names():
    summary = 'Morphotonics 获得新融资，将其显示技术扩展至数据中心。'
    assert cards.chinese_translation(summary)
    assert not cards.chinese_translation('中文 ' + 'This is an untranslated English introduction. ' * 3)


@pytest.mark.asyncio
async def test_previous_source_version_restores_without_another_model_call(db,entry,monkeypatch):
    core.discover([entry])
    await translate(entry,monkeypatch)
    before = cards.attach(entry,1)['card']
    changed = {**entry,'content':'<p>A temporarily shorter RSS excerpt.</p>'}
    cards.enqueue([changed])
    assert cards.attach(changed,1)['card']['status'] == 'pending'
    cards.enqueue([entry])
    assert cards.attach(entry,1)['card'] == before
    assert (await translate(entry,monkeypatch))[0] == []
