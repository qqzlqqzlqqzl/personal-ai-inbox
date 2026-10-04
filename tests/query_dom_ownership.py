"""Strict test-only UI ownership; URL changes and rAF are not paint proofs."""
import math
import re

LABELS = {'all': '全部', 'today': '今天'}
COUNT = r'(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)'
COMMIT_DEADLINE_MS = 5000


def finite_time(value):
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def identity(frame):
    """Classify only recorded fields; this alone does not prove menu uniqueness."""
    if type(frame) is not dict:
        raise ValueError('frame_shape')
    scope, location = frame.get('scope'), frame.get('location')
    if type(scope) is not str or scope not in LABELS or type(location) is not dict or location.get('pathname') != '/inbox/' + scope:
        raise ValueError('url_identity')
    if type(frame.get('sequence')) is not int or frame['sequence'] < 1 or not finite_time(frame.get('time')):
        raise ValueError('observation_clock')
    dom = frame.get('dom')
    if type(dom) is not dict or type(dom.get('selected_scope')) is not str or dom['selected_scope'] not in LABELS:
        raise ValueError('menu_identity_missing')
    selected = dom['selected_scope']
    if dom.get('selected_label') != LABELS[selected]:
        raise ValueError('menu_identity_conflict')
    headers = dom.get('headers')
    if type(headers) is not list or len(headers) != 1 or type(headers[0]) is not dict:
        raise ValueError('header_identity_missing')
    header = headers[0].get('rendered_text')
    # This suite fixes the AI lens and account timezone in its synthetic harness.
    title = LABELS[selected] + ' · AI精选' + (' · Asia/Shanghai' if selected == 'today' else '')
    match = re.fullmatch(re.escape(title) + r'(?:\s*\((' + COUNT + r')\))?\s*', header.strip()) if type(header) is str else None
    if match is None:
        raise ValueError('menu_header_query_conflict')
    if type(frame.get('count')) is not str or type(dom.get('count_rendered_text')) is not str:
        raise ValueError('count_shape')
    count = dom['count_rendered_text']
    if count and (len(count) > 64 or re.fullmatch(COUNT, count) is None):
        raise ValueError('count_shape')
    if match[1] and len(match[1]) > 64:
        raise ValueError('count_shape')
    total = int(match[1].replace(',', '')) if match[1] is not None else None
    number = int(count.replace(',', '')) if count else None
    if total is not None and (number != total if total else number not in (None, 0)):
        raise ValueError('menu_header_count_conflict')
    if total is None and number is not None:
        raise ValueError('menu_header_count_conflict')
    ids = frame.get('ids')
    if type(ids) is not list or any(type(value) is not str or not value.isascii() or not value.isdecimal() for value in ids):
        raise ValueError('row_identity_shape')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_row_identity')
    if total is not None and total > 0 and not ids:
        raise ValueError('count_without_baseline_rows')
    return selected, header.strip(), total


def logical_snapshot(frame):
    selected, header, _ = identity(frame)
    return selected, header, frame['count'], frame['dom']['count_rendered_text'], tuple(frame['ids'])


def require_unique_menu(frame):
    dom = frame['dom']
    candidates = dom.get('selected_candidates')
    if type(dom.get('selected_count')) is not int or dom['selected_count'] != 1 or type(candidates) is not list or len(candidates) != 1:
        raise ValueError('unique_menu_identity_not_proven')
    if type(candidates[0]) is not dict or candidates[0].get('scope') != dom['selected_scope'] or candidates[0].get('label') != dom['selected_label']:
        raise ValueError('menu_candidate_conflict')


def assess_pending_ui(scope, baseline, frames, *, dropped):
    """Verify every old UI snapshot and require an empty committed target in 5s."""
    result = {'passed': False, 'target_scope': scope, 'old_ui_sequences': [],
              'url_lead_sequences': [], 'target_ui_sequences': [], 'violations': [],
              'first_target_after_ms': None, 'paint_verified': False}

    def reject(reason, sequence=None):
        result['violations'].append({'sequence': sequence, 'reason': reason})

    if type(scope) is not str or scope not in LABELS or type(dropped) is not int or dropped != 0:
        reject('target_or_evidence_incomplete')
        return result
    try:
        old = logical_snapshot(baseline)
        require_unique_menu(baseline)
        if baseline['scope'] != old[0] or scope == old[0]:
            raise ValueError('baseline_route_not_distinct_and_coherent')
    except (ValueError, TypeError, KeyError) as error:
        reject(str(error))
        return result
    if type(frames) is not list or not frames:
        reject('no_observations')
        return result
    previous_sequence, previous_time = baseline['sequence'] - 1, baseline['time']
    for frame in frames:
        sequence = frame.get('sequence') if type(frame) is dict else None
        try:
            selected, _, total = identity(frame)
            require_unique_menu(frame)
            if frame['time'] < previous_time or sequence <= previous_sequence:
                raise ValueError('observation_order')
            previous_sequence, previous_time = sequence, frame['time']
            if selected == old[0]:
                if result['target_ui_sequences']:
                    raise ValueError('committed_target_regressed')
                if logical_snapshot(frame) != old:
                    raise ValueError('previous_ui_changed_or_cross_query_mixed')
                result['old_ui_sequences'].append(sequence)
                if frame['scope'] == scope:
                    result['url_lead_sequences'].append(sequence)
            elif selected == scope:
                if frame['scope'] != scope:
                    raise ValueError('target_ui_url_conflict')
                if frame['count'] or frame['dom']['count_rendered_text'] or frame['ids'] or total is not None:
                    raise ValueError('target_pending_borrows_result')
                if result['first_target_after_ms'] is None:
                    result['first_target_after_ms'] = frame['time'] - baseline['time']
                    if result['first_target_after_ms'] > COMMIT_DEADLINE_MS:
                        raise ValueError('target_commit_deadline')
                result['target_ui_sequences'].append(sequence)
            else:
                raise ValueError('unrelated_query_identity')
        except (ValueError, TypeError, KeyError) as error:
            reject(str(error), sequence)
    if not result['target_ui_sequences']:
        reject('target_ui_never_committed')
    result['passed'] = not result['violations']
    return result
