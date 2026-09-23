import unittest
import sqlite3
import uuid
from pathlib import Path
from cloud_bridge import upstream_hash,backup_before_import,validate_model_config,resolve_recovery
from types import SimpleNamespace
from build_manifest import build


class SourceVersionTests(unittest.TestCase):
    def test_recovery_resolution_requires_exact_import_receipts_and_sources(self):
        folder=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        (folder/'original').mkdir(parents=True)
        database=folder/'ledger.sqlite3'
        original={'batch_id':'original','model':{'sha256':'model'},'items':[
            {'id':'a','input_hash':'old','source_refs':[{'source_hash':'same'}]}]}
        child={'batch_id':'child','resume_of':'original','model':original['model'],'items':[
            {'id':'a','input_hash':'new','source_refs':[{'source_hash':'same'}]}]}
        states=[]
        control=SimpleNamespace(root=folder,manifest=lambda key:{'original':original,'child':child}[key],
            _set=lambda *args:states.append(args))
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE kaggle_imports(batch_id TEXT,item_id TEXT,input_hash TEXT)')
        with self.assertRaises(ValueError):
            resolve_recovery(control,database,'original',['child'])
        self.assertEqual([],states)
        with sqlite3.connect(database) as db:
            db.execute('INSERT INTO kaggle_imports VALUES (?,?,?)',('child','a','new'))
        child['items'][0]['source_refs']=[{'source_hash':'changed'}]
        with self.assertRaises(ValueError):
            resolve_recovery(control,database,'original',['child'])
        child['items'][0]['source_refs']=original['items'][0]['source_refs']
        report=resolve_recovery(control,database,'original',['child'])
        self.assertEqual({'a':'child'},report['coverage'])
        self.assertEqual([('original','resolved')],states)

    def test_cloud_rejects_wrong_dataset_and_reduced_context(self):
        config={'model_dataset':'owner/q4','context_size':65536,'parallel_requests':2}
        versions={'dataset_source':'owner/q4'}
        validate_model_config(config,versions)
        for changed in ({'model_dataset':'owner/old-cache'},{'context_size':16384},{'parallel_requests':3}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                validate_model_config({**config,**changed},versions)

    def test_translation_backlog_does_not_rescore_finished_article(self):
        sample={'settings':{'prompt':'score','max_output_tokens':100},'translation_prompt':'translate',
                'samples':[{'entry_id':1,'user_id':2,'skip_analysis':True,'upstream_hash':'source-version',
                            'card':{'original_title':'English title','excerpt':'Text',
                                    'source_kind':'source_excerpt','source_hash':'card-version'}}]}
        versions={'model_repo':'owner/model','model_revision':'revision','llama_commit':'commit',
                  'file':{'rfilename':'model.gguf','size':1,'lfs':{'sha256':'hash'}}}
        manifest=build(sample,versions,'runtime-hash','owner/runtime',model_dataset='owner/cache')
        self.assertEqual(['translation'],[item['kind'] for item in manifest['items']])
        self.assertEqual('source-version',manifest['items'][0]['source_refs'][0]['upstream_hash'])

    def test_unrelated_reader_state_does_not_invalidate_input(self):
        entry={'id':1,'user_id':2,'title':'title','url':'https://example.com','content':'body'}
        self.assertEqual(upstream_hash(entry),upstream_hash({**entry,'status':'read','starred':True}))

    def test_content_identity_or_owner_change_invalidates_result(self):
        entry={'id':1,'user_id':2,'title':'title','url':'https://example.com','content':'body'}
        for key,value in [('id',2),('user_id',3),('title','changed'),('url','https://example.org'),('content','new body')]:
            with self.subTest(key=key):
                self.assertNotEqual(upstream_hash(entry),upstream_hash({**entry,key:value}))

    def test_backup_preserves_before_state_and_repeat_does_not_overwrite(self):
        folder=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        folder.mkdir(parents=True)
        database=folder/'source.sqlite3'
        db=sqlite3.connect(database)
        try:
            db.execute('CREATE TABLE example (value TEXT)')
            db.execute("INSERT INTO example VALUES ('before')")
            db.commit()
            backup_before_import(database,folder)
            db.execute("UPDATE example SET value='after'")
            db.commit()
            backup_before_import(database,folder)
        finally:
            db.close()
        backup=sqlite3.connect(folder/'before-import.sqlite3')
        try:
            self.assertEqual('before',backup.execute('SELECT value FROM example').fetchone()[0])
        finally:
            backup.close()

    def test_partial_backup_is_not_accepted(self):
        folder=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        folder.mkdir(parents=True)
        (folder/'before-import.sqlite3').write_bytes(b'incomplete')
        with self.assertRaises(RuntimeError):
            backup_before_import(folder/'source.sqlite3',folder)


if __name__=='__main__':
    unittest.main()
