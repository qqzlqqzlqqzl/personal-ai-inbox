import unittest
from batch_runner import generation_parameters,calibrate_probe,server_layout


class GenerationBudgetTests(unittest.TestCase):
    def test_parallel_requests_each_keep_full_context(self):
        self.assertEqual((2,131072),server_layout({'context_size':65536,'parallel_requests':2}))
        self.assertEqual((1,65536),server_layout({'context_size':65536}))
        with self.assertRaises(ValueError):
            server_layout({'context_size':65536,'parallel_requests':3})

    def test_unlimited_thinking_uses_remaining_window_without_changing_input(self):
        manifest={'context_size':65536,'generation':{'thinking':True,'use_remaining_context':True,
                                                   'temperature':1.0,'top_k':20}}
        result=generation_parameters(manifest,{'max_tokens':1500},8192)
        self.assertEqual(65536-8192-16,result['max_tokens'])
        self.assertTrue(result['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(1.0,result['temperature'])

    def test_long_input_preserves_final_answer_reserve(self):
        manifest={'context_size':65536,'generation':{'thinking':True,'use_remaining_context':True}}
        self.assertEqual(3520,generation_parameters(manifest,{'max_tokens':1500},62000)['max_tokens'])
        with self.assertRaises(ValueError):
            generation_parameters(manifest,{'max_tokens':1500},65000)

    def test_archived_non_thinking_manifest_keeps_original_budget(self):
        result=generation_parameters({'context_size':16384},{'max_tokens':1500},1000)
        self.assertEqual(1500,result['max_tokens'])
        self.assertFalse(result['chat_template_kwargs']['enable_thinking'])

    def test_calibrated_long_probe_preserves_all_codes_and_stays_below_target(self):
        def post(path,body,timeout):
            if path=='/apply-template':
                return {'prompt':' '.join(item['content'] for item in body['messages'])}
            return {'tokens':body['content'].split()}
        messages,count=calibrate_probe(post,60000)
        self.assertLessEqual(count,60000)
        self.assertGreater(count,59970)
        for code in ('MAPLE-731','QUARTZ-482','HARBOR-956'):
            self.assertEqual(1,messages[1]['content'].count(code))


if __name__=='__main__':
    unittest.main()
