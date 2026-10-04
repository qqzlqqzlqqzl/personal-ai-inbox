"""Compare five complete before/after pairs only; refuses failed or unmeasured input."""
import argparse
import json
from pathlib import Path
from reader_loading_performance import save_new
from reader_loading_fixture import bound_read, require


def compare(before, after):
    require(before['status']==after['status']=='PASSED', 'both actual runs must pass')
    require(before['phase']=='baseline' and after['phase']=='candidate', 'phase labels do not match')
    for key in ('scenario','input','weak_network','browser_version'):
        require(before[key]==after[key], 'measurement conditions differ: '+key)
    require(len(before['pairs'])==len(after['pairs'])==5,'exactly five complete pairs required')
    rows=[]
    for left,right in zip(before['pairs'],after['pairs']):
        require(left['pair']==right['pair'] and left['status']==right['status']=='PASSED','pair identity mismatch')
        row={'pair':left['pair'], 'milliseconds':{key:{'before':left[key],'after':right[key],'delta':right[key]-left[key]}
                    for key in ('list_initial_ms','cold_click_to_body_ms','warm_click_to_body_ms')}}
        for phase in ('cold_images','warm_images'):
            require([x['image'] for x in left[phase]]==[x['image'] for x in right[phase]]==list(range(1,7)), 'image sample identities differ')
            row[phase]=[]
            for a,b in zip(left[phase],right[phase]):
                item={'image':a['image']}
                for metric in ('scroll_to_visible_ms','request_to_visible_ms','request_to_finished_ms'):
                    item[metric]={'before':a.get(metric),'after':b.get(metric),
                                  'delta':b[metric]-a[metric] if metric in a and metric in b else None}
                row[phase].append(item)
        require([p['loaded'] for p in left['next_pages']]==[p['loaded'] for p in right['next_pages']]==[48,72], 'list sample identities differ')
        row['next_pages']=[{'loaded':a['loaded'], 'before_bottom_wait_ms':a['fast_scroll_bottom_wait_ms'],
                            'after_bottom_wait_ms':b['fast_scroll_bottom_wait_ms'],
                            'delta_ms':b['fast_scroll_bottom_wait_ms']-a['fast_scroll_bottom_wait_ms']}
                           for a,b in zip(left['next_pages'],right['next_pages'])]
        rows.append(row)
    return {'status':'COMPARED','before_identity':before['identity'],'after_identity':after['identity'],
            'conditions':{k:before[k] for k in ('scenario','input','weak_network','browser_version')},
            'pairs':rows,'percentile_claim':None,'production_performance_claim':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True);parser.add_argument('--after',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    values=[]
    for value in (args.before,args.after):
        p=Path(value).absolute();values.append(json.loads(bound_read(p.parent,p.name,2*1024*1024)))
    result=compare(*values);save_new(Path(args.output),result);print(json.dumps(result,ensure_ascii=False,indent=2))
