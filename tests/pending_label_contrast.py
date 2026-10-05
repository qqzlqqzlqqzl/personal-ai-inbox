"""Real informational-label colors; reuse the reviewed browser compositing probe."""
import math
import time

from playwright.sync_api import expect
from console_link_contrast import contrast, sample

LEGACY = '.ai-pending{color:var(--color-text-3,#64748b)!important}'


def validate_measurement(row, theme):
    """No anti-aliased pixel inference or disabled/large-text exemption."""
    assert theme in ('light', 'dark'), 'unsupported measured theme'
    assert row['tag'] in ('DIV', 'P'), 'expected ordinary information text'
    assert row['theme'] == theme, 'wrong measured theme'
    assert not row['disabled'], 'disabled/inert text cannot pass this gate'
    assert row['font_size'] == '12px', 'pending text size changed'
    assert row['text'], 'empty informational text'
    assert row['rect']['width'] > 0 and row['rect']['height'] > 0, 'empty label rectangle'
    for color in (row['foreground'], row['effective_background']):
        assert len(color) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                     and math.isfinite(v) and 0 <= v <= 255 for v in color), 'invalid composed color'
    ratio = contrast(row['foreground'], row['effective_background'])
    assert abs(row['contrast'] - ratio) < 1e-9, 'inconsistent contrast record'
    assert ratio >= 4.5, 'ordinary informational text has insufficient contrast'
    return ratio


def verify_pending_labels(h, container, entry_id, view, theme, records, capture_legacy=None):
    """Current/old/restored styles on actual product nodes and actual ancestor backgrounds."""
    pending = container.locator('.ai-pending')
    expect(pending).to_have_count(1)
    targets = [('state', pending), ('quality', pending.locator('.ai-content-quality'))]
    if entry_id == 705:
        targets.append(('processing', pending.locator('.ai-processing p')))
    record = {'entry_id': entry_id, 'view': view, 'theme': theme, 'labels': [],
              'legacy_control': 'isolated old CSS injection; no data or product state mutation'}
    records.append(record)
    for label, node in targets:
        expect(node).to_have_count(1)
        current = sample(node)
        record['labels'].append({'label': label, 'current': current})
        validate_measurement(current, theme)
        h.check(f'{view}_{entry_id}_{label}_computed_contrast', True)
    legacy = h.page.add_style_tag(content=LEGACY)
    try:
        if theme == 'light':
            expect(pending).to_have_css('color', 'rgb(134, 144, 156)')
        for (label, node), item in zip(targets, record['labels']):
            old = sample(node)
            item['legacy'] = old
            if theme == 'light':
                h.check(f'{view}_{entry_id}_{label}_old_light_negative', old['contrast'] < 4.5)
            h.check(f'{view}_{entry_id}_{label}_text_font_geometry_preserved', all(
                old[key] == item['current'][key] for key in ('text', 'font_size', 'font_weight', 'tag', 'rect', 'disabled')))
        if capture_legacy is not None and theme == 'light':
            capture_legacy()
    except Exception as exc:
        record['legacy_failure'] = type(exc).__name__ + ': ' + str(exc)
        failure = h.out / f'pending-contrast-{view}-{entry_id}-failure-{time.time_ns()}.png'
        try:
            h.page.screenshot(path=str(failure))
            record['legacy_failure_png'] = failure.name
        except Exception as capture_error:
            record['legacy_failure_capture_error'] = type(capture_error).__name__
        raise
    finally:
        # Test-only DOM style removal. No file or user content is deleted.
        legacy.evaluate('(element)=>element.remove()')
    expect(pending).to_have_css('color', record['labels'][0]['current']['computed_color'])
    for (_, node), item in zip(targets, record['labels']):
        restored = sample(node)
        validate_measurement(restored, theme)
        item['restored'] = restored
        h.check(f'{view}_{entry_id}_{item["label"]}_actual_style_restored', all(
            restored[key] == item['current'][key] for key in ('foreground', 'effective_background', 'computed_color',
                                                           'text', 'font_size', 'font_weight', 'tag', 'rect', 'disabled')))
