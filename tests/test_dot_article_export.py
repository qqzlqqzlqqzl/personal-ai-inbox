import tempfile,pathlib,sqlite3,json,hashlib,time,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
import export_dot_articles_readonly as m

class ReadOnlyExportTests(unittest.TestCase):
    def test_scope_claims_fidelity_and_no_write(self):
        with tempfile.TemporaryDirectory() as t:
         p=pathlib.Path(t);src=p/'src';(src/'kaggle_batch').mkdir(parents=True)
         (src/'core.py').write_text('DEFAULT_PROMPT="default"\nDEFAULT_PROMPT += " fidelity"\n')
         (src/'card_translation.py').write_text('PROMPT="real translation"\nVERSION="zh-cards-v1"\n')
         (src/'kaggle_batch/build_manifest.py').write_text('ANALYSIS_FIDELITY="evidence exact"\nTRANSLATION_FIDELITY="faithful"\n')
         dbpath=p/'analysis.db';c=sqlite3.connect(dbpath)
         c.executescript('CREATE TABLE settings(name,value);CREATE TABLE analyses(entry_id INTEGER,user_id INTEGER,feed_id INTEGER,title,url,published_at,state,attempts,next_try,content_hash,source_text,truncated,content_source,source_chars,extracted_at);CREATE TABLE card_translations(entry_id INTEGER,user_id INTEGER,status,source_hash,original_title,excerpt,source_kind,attempts,next_try);CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner,expires);')
         c.execute('INSERT INTO settings VALUES (?,?)',('preferences',json.dumps({'prompt':'current real prompt','enabled':False,'translation_enabled':False,'base_url':'SECRET-NOT-EXPORT','model':'PAID-MODEL-NOT-EXPORT','max_output_tokens':1400})))
         for i in range(1,10):
          text='' if i==7 else 'Complete stored body '+str(i)
          c.execute('INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,1,2 if i==9 else 1,'t','https://example.com/'+str(i),'2026-10-01T00:00:00Z','done' if i==8 else 'waiting_model',0,0,'hash'+str(i),text,0,'original_url_site_rule',len(text),100.0))
         c.execute('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',(6,'other',time.time()+660));c.commit();c.close()
         lane=p/'lane';lane.mkdir();c=sqlite3.connect(lane/'batches.sqlite3');c.executescript('CREATE TABLE batches(id,state);CREATE TABLE batch_claims(batch_id,entry_id);');c.execute('INSERT INTO batches VALUES (?,?)',('unknown','submit_unknown'));c.execute('INSERT INTO batch_claims VALUES (?,?)',('unknown',5));c.commit();c.close()
         cfg={'source':str(src),'database':str(dbpath),'peer_state_roots':[str(lane)],'state_root':str(lane),'scope_user_id':1}
         before=hashlib.sha256(dbpath.read_bytes()).hexdigest();out=m.export(cfg,enabled_feed_ids={1});encoded=json.dumps(out)
         assert [r['entry_id'] for r in out['articles']]==[4,3,2],out
         assert out['needs_source_preparation'][0]['entry_id']==7
         assert out['analysis_prompt']=='current real prompt';assert 'SECRET-NOT-EXPORT' not in encoded and 'PAID-MODEL' not in encoded
         assert before==hashlib.sha256(dbpath.read_bytes()).hexdigest()
         copy=dict(out);h=copy.pop('export_hash');assert m.digest(copy)==h
         assert m.export(cfg,enabled_feed_ids=set())['articles']==[]
         c=sqlite3.connect(dbpath);c.execute("UPDATE analyses SET content_source='rss_excerpt' WHERE entry_id=4");c.commit();c.close()
         assert 4 not in [r['entry_id'] for r in m.export(cfg,enabled_feed_ids={1})['articles']]
         print('PASS readonly export: newest-first3; enabledscope/done/unknownclaim/activelease/source-provenance guards; currentprompt; no credential/model leak; hash integrity; DB bytes unchanged')

if __name__=="__main__":unittest.main()
