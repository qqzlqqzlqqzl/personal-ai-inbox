"""Compare five complete before/after pairs only; refuses failed or unmeasured input."""
import argparse
import copy
import json
from pathlib import Path
from reader_loading_performance import (save_new, finite_nonnegative, MEASUREMENT_CONTRACT,
    decoded_reopen_observation, require_new_page_same_cache, warm_image_proof)
from reader_loading_fixture import bound_read, require
from reader_loading_transport import validate_profile
from reader_loading_body import BODY_CONTRACT, validate_ready


def compare(before, after):
    require(before['status']==after['status']=='PASSED', 'both actual runs must pass')
    require(before['phase']=='baseline' and after['phase']=='candidate', 'phase labels do not match')
    require(before.get('measurement_contract')==after.get('measurement_contract')==MEASUREMENT_CONTRACT,
            'same instrumentation contract required; old warm meanings cannot be compared')
    for side in (before,after):
        validate_profile(side.get('transport_profile'))
    require(before['transport_profile']==after['transport_profile'],'transport profiles differ')
    for key in ('scenario','input','weak_network','browser_version'):
        require(before[key]==after[key], 'measurement conditions differ: '+key)
    require(len(before['pairs'])==len(after['pairs'])==5,'exactly five complete pairs required')
    for side in (before,after):
        require([row.get('pair') for row in side['pairs']]==list(range(1,6)) and
                all(type(row.get('pair')) is int for row in side['pairs']), 'expected unique pair IDs 1 through 5 in order')
        for row in side['pairs']:
            require(validate_profile(row.get('transport_profile'))==side['transport_profile'],'pair transport profile mismatch')
            require(row.get('measurement_contract')==MEASUREMENT_CONTRACT,'pair uses a different measurement contract')
            require(row.get('warm_observation_class')=='SAME_PAGE_DECODED_REOPEN; HTTP_CACHE_HIT_NOT_CLAIMED',
                    'same-page reopen was mislabeled as an HTTP cache hit')
            decoded_reopen_observation(row['warm_images'],row['warm_start_wall_ms'],row['warm_end_wall_ms'])
            require(type(row['warm_image_http_requests']) is int and row['warm_image_http_requests']==0,'reopen issued image HTTP requests')
            cache=row['http_cache_page']
            require(cache['status']=='PASSED' and cache['scope']=='NEW_PAGE_SAME_BROWSER_CONTEXT_HTTP_CACHE','separate HTTP-cache page did not pass')
            require_new_page_same_cache(cache['previous_page_identity'],cache['identity'])
            require(type(cache['image_http_requests']) is int and cache['image_http_requests']==0,'HTTP-cache page issued image HTTP requests')
            for key in ('bootstrap_ms','click_to_body_ms'):finite_nonnegative(cache[key],key)
            require(type(cache['image_event_start']) is int and 0<=cache['image_event_start']<=len(cache['cdp']),'invalid new-page event boundary')
            checked=copy.deepcopy(cache['images'])
            for image in checked:finite_nonnegative(image['scroll_to_visible_ms'],'cache image scroll_to_visible_ms')
            proof=warm_image_proof(cache['cdp'][cache['image_event_start']:],checked,cache['origin'],cache['start_wall_ms'],cache['end_wall_ms'])
            require(proof==cache['image_cache_proof'],'published cache proof changed')
            for original,replayed in zip(cache['images'],checked):
                require(original==replayed,'published cache metrics differ from their verified request')
            for key in ('list_initial_ms','cold_click_to_body_ms','warm_click_to_body_ms'):
                finite_nonnegative(row[key],key)
            for key, value in [('cold_body_observation', row['cold_click_to_body_ms']),
                               ('warm_body_observation', row['warm_click_to_body_ms'])]:
                observation=row[key]
                require(observation.get('contract')==BODY_CONTRACT,'body contract differs')
                validate_ready(observation['prose_ready'])
                require(finite_nonnegative(observation['container_ready_ms'],'container readiness')<=
                        finite_nonnegative(observation['prose_dom_ready_ms'],'prose readiness')==value,
                        'published body time does not match actual prose observation')
            observation=cache['body_observation']
            require(observation.get('contract')==BODY_CONTRACT,'cache body contract differs')
            validate_ready(observation['prose_ready'])
            require(finite_nonnegative(observation['container_ready_ms'],'cache container readiness')<=
                    finite_nonnegative(observation['prose_dom_ready_ms'],'cache prose readiness')==cache['click_to_body_ms'],
                    'cache body time does not match prose observation')
            for phase in ('cold_images','warm_images'):
                require([image.get('image') for image in row[phase]]==list(range(1,7)) and
                        all(type(image.get('image')) is int for image in row[phase]),'wrong image identities')
                for image in row[phase]:
                    finite_nonnegative(image['scroll_to_visible_ms'],'scroll_to_visible_ms')
                    for key in ('request_to_visible_ms','request_to_finished_ms'):
                        if image.get(key) is not None: finite_nonnegative(image[key],key)
            require([page.get('loaded') for page in row['next_pages']]==[48,72],'wrong list identities')
            for page in row['next_pages']:
                finite_nonnegative(page['fast_scroll_bottom_wait_ms'],'fast_scroll_bottom_wait_ms')
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
                                  'delta':b[metric]-a[metric] if a.get(metric) is not None and b.get(metric) is not None else None}
                row[phase].append(item)
        require([p['loaded'] for p in left['next_pages']]==[p['loaded'] for p in right['next_pages']]==[48,72], 'list sample identities differ')
        row['next_pages']=[{'loaded':a['loaded'], 'before_bottom_wait_ms':a['fast_scroll_bottom_wait_ms'],
                            'after_bottom_wait_ms':b['fast_scroll_bottom_wait_ms'],
                            'delta_ms':b['fast_scroll_bottom_wait_ms']-a['fast_scroll_bottom_wait_ms']}
                           for a,b in zip(left['next_pages'],right['next_pages'])]
        row['http_cache_page']={key:{'before':left['http_cache_page'][key],'after':right['http_cache_page'][key],
            'delta':right['http_cache_page'][key]-left['http_cache_page'][key]} for key in ('bootstrap_ms','click_to_body_ms')}
        row['http_cache_page']['images']=[{'image':a['image'],**{key:{'before':a[key],'after':b[key],'delta':b[key]-a[key]}
            for key in ('scroll_to_visible_ms','request_to_visible_ms','request_to_finished_ms','encoded_bytes')}}
            for a,b in zip(left['http_cache_page']['images'],right['http_cache_page']['images'])]
        rows.append(row)
    return {'status':'COMPARED','measurement_contract':MEASUREMENT_CONTRACT,'transport_profile':before['transport_profile'],
            'warm_milliseconds_meaning':'same-page reopen, not HTTP-cache hit latency; new-page costs are separate',
            'before_identity':before['identity'],'after_identity':after['identity'],
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
