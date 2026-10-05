"""Measure scored access-condition labels on the real raw/detail consumers."""
import math

from playwright.sync_api import expect
from console_link_contrast import contrast, sample


def verify_quality_label(h, container, entry_id, view, theme, records):
    node = container.locator('.ai-verdict .ai-content-quality')
    expect(node).to_have_count(1)
    expect(node).to_be_visible()
    current = sample(node)
    record = {'entry_id': entry_id, 'view': view, 'theme': theme,
              'kind': 'scored_access_condition', 'current': current}
    records.append(record)
    assert current['tag'] == 'P' and current['theme'] == theme and not current['disabled']
    assert current['font_size'] == ('12px' if view == 'raw' else '14px')
    assert current['text'] and current['rect']['width'] > 0 and current['rect']['height'] > 0
    for color in (current['foreground'], current['effective_background']):
        assert len(color) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and 0 <= v <= 255 for v in color)
    ratio = contrast(current['foreground'], current['effective_background'])
    assert abs(current['contrast'] - ratio) < 1e-9 and ratio >= 4.5
    h.check(f'{view}_{entry_id}_access_condition_computed_contrast', True)
    # Restore the old inherited color on this isolated product node only.
    legacy = h.page.add_style_tag(content='.ai-verdict .ai-content-quality{color:inherit!important}')
    try:
        old = sample(node)
        record['legacy'] = old
        if theme == 'dark' and view == 'raw':
            h.check(f'{view}_{entry_id}_old_dark_access_condition_negative', old['contrast'] < 4.5)
        h.check(f'{view}_{entry_id}_access_condition_content_geometry_retained', all(
            old[key] == current[key] for key in ('text', 'font_size', 'font_weight', 'tag', 'rect', 'disabled')))
    finally:
        legacy.evaluate('(element)=>element.remove()')
    expect(node).to_have_css('color', current['computed_color'])
    restored = sample(node)
    record['restored'] = restored
    h.check(f'{view}_{entry_id}_access_condition_style_restored', all(restored[key] == current[key]
        for key in ('foreground', 'effective_background', 'computed_color', 'text', 'font_size', 'rect')))
