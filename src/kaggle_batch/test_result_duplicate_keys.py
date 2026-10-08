"""Duplicate output-key checks using the existing synthetic durable-batch fixture."""
import json
import unittest

import test_pipeline_resilience as fixture_tests
from queue_dispatch import claimed_entries


class ResultDuplicateKeyTests(unittest.TestCase):
    setUp=fixture_tests.DurableBatchTests.setUp
    tearDown=fixture_tests.DurableBatchTests.tearDown
    record=fixture_tests.DurableBatchTests.record
    write=fixture_tests.DurableBatchTests.write

    def duplicate_key_records(self):
        for field,value in [('id','other-item'),('batch_id','other-batch'),
                            ('manifest_hash','other-manifest'),('input_hash','wrong-version'),
                            ('status','error'),('content','different'),('input_hash','ha')]:
            yield field,value,'{'+json.dumps(field)+':'+json.dumps(value)+','+json.dumps(self.record('a'))[1:]

    def test_strict_output_rejects_duplicate_json_keys_without_editing_raw(self):
        for field,value,record in self.duplicate_key_records():
            with self.subTest(field=field,value=value):
                path=self.write([record,self.record('b')]);before=path.read_bytes()
                with self.assertRaises(ValueError):self.c.verify_output(self.b)
                self.assertEqual(before,path.read_bytes())

    def test_salvage_rejects_duplicate_json_keys_but_keeps_independent_results(self):
        for field,value,record in self.duplicate_key_records():
            with self.subTest(field=field,value=value):
                path=self.write([record,self.record('b')]);before=path.read_bytes()
                result=self.c.verify_output(self.b,salvage=True)
                self.assertEqual(['b'],[row['id'] for row in result['results']])
                self.assertEqual(['a','c'],result['missing_ids'])
                self.assertEqual([{'line':1,'id':None,'code':'malformed_record'}],result['rejected_records'])
                self.assertEqual(before,path.read_bytes())
                self.assertEqual('terminal',self.c.row(self.b)['state'])
                self.assertEqual({1,2,3},claimed_entries([self.root]))
                self.assertEqual([],self.calls)


if __name__=='__main__':
    unittest.main()
