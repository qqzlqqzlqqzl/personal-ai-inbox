"""Run inside the Inbox Linux environment; read-only existing business validators."""
import argparse
import json
from pathlib import Path
import re
import sys
from scoring_policy import add_priority

def complete_summary(summary):
    """Drop only a visibly unfinished final sentence; retain raw output separately."""
    if not re.search(r'(?:…|\.{3})+$',summary):
        return summary
    ends=list(re.finditer(r'[。！？]',summary))
    if not ends:
        raise ValueError('translation_has_no_complete_sentence')
    return summary[:ends[-1].end()]

def validate(manifest, results, source):
    sys.path.insert(0,str(Path(source).resolve()))
    from worker import validate_result
    from core import evidence_matches
    from card_translation import validate_items
    inputs={item['id']:item for item in manifest['items']}
    seen=set()
    checked=[]
    for row in results:
        value={'id':row.get('id'),'valid':False}
        try:
            if row.get('batch_id')!=manifest['batch_id'] or row.get('manifest_hash')!=manifest['manifest_hash']:
                raise ValueError('wrong_batch')
            if row.get('id') not in inputs or row['id'] in seen:
                raise ValueError('unknown_or_duplicate_item')
            seen.add(row['id'])
            item=inputs[row['id']]
            if item['kind'] not in {'analysis','translation'}:
                raise ValueError('unsupported_business_task')
            if row.get('input_hash')!=item['input_hash']:
                raise ValueError('input_version_mismatch')
            if row.get('status')!='ok':
                raise ValueError(row.get('error','inference_failed'))
            if item['kind']=='analysis':
                content=re.sub(r'^```(?:json)?\s*|\s*```$','',row['content'].strip())
                data=validate_result(json.loads(content))
                original_worth_reading=data['worth_reading']
                data=add_priority(data,item['messages'][0]['content'])
                if data['worth_reading']!=original_worth_reading:
                    value['normalizations']=[{'field':'worth_reading','reason':'derived_from_reading_priority',
                                              'original':original_worth_reading,'value':data['worth_reading']}]
                if not evidence_matches(data['evidence'],item['source_refs'][0]['source_text']):
                    raise ValueError('evidence_not_in_original')
            else:
                data=validate_items(row['content'],item['source_refs'])
                changes=[]
                for entry_id,(title,summary) in data.items():
                    clean=complete_summary(summary)
                    if clean!=summary:
                        changes.append({'entry_id':entry_id,'reason':'unfinished_final_sentence',
                            'removed_characters':len(summary)-len(clean)})
                    data[entry_id]=(title,clean)
                if changes:
                    value['normalizations']=changes
            value.update(valid=True,result=data,kind=item['kind'],
                input_hash=item['input_hash'],source_refs=item['source_refs'],
                tokens=(row.get('usage') or {}).get('total_tokens',0))
        except Exception as exc:
            value['error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        checked.append(value)
    return {'batch_id':manifest['batch_id'],'manifest_hash':manifest['manifest_hash'],
            'valid':sum(row['valid'] for row in checked),
            'invalid':sum(not row['valid'] for row in checked),'missing_ids':sorted(set(inputs)-seen),
            'items':checked}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--results',required=True)
    parser.add_argument('--source',default='/home/ubuntu/ai-news/src')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    results_path=Path(args.results)
    if results_path.suffix=='.json':
        results=json.loads(results_path.read_text(encoding='utf-8'))['results']
    else:
        results=[json.loads(line) for line in results_path.read_text(encoding='utf-8').splitlines()]
    report=validate(json.loads(Path(args.manifest).read_text(encoding='utf-8')),
        results,args.source)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='items'}))
