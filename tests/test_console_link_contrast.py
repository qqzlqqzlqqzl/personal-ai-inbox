"""WCAG color arithmetic and narrow product scope; real browser is separate."""
from pathlib import Path
import re

from console_link_contrast import contrast

ROOT = Path(__file__).resolve().parents[1]


def test_reference_black_white_contrast():
    assert contrast((0, 0, 0), (255, 255, 255)) == 21
    assert contrast((35, 35, 36), (35, 35, 36)) == 1


def test_original_dark_blue_negative_and_original_light_positive():
    assert 3.03 < contrast((37, 99, 235), (35, 35, 36)) < 3.04
    assert contrast((37, 99, 235), (35, 35, 36)) < 4.5
    assert contrast((37, 99, 235), (255, 255, 255)) >= 4.5


def test_product_dark_palette_exceeds_threshold_without_font_changes():
    css = (ROOT / 'patches/AiNews.css').read_text()
    rule = re.search(r'body\[arco-theme=dark\] \.ai-dialog a,body\[arco-theme=dark\] \.ai-dialog a:hover,body\[arco-theme=dark\] \.ai-dialog a:focus-visible\{([^}]+)\}', css)
    assert rule and rule[1] == 'color:#93c5fd'
    assert '.ai-dialog a{color:var(--color-link,#2563eb)}' in css
    rgb = tuple(int('93c5fd'[start:start+2], 16) for start in (0, 2, 4))
    assert contrast(rgb, (35, 35, 36)) >= 4.5
    # The dark-only color would fail on white; the explicit theme boundary matters.
    assert contrast(rgb, (255, 255, 255)) < 4.5
