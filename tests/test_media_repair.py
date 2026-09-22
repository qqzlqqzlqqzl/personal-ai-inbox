from media_repair import repair_html, needs_repair
from bs4 import BeautifulSoup

BASE = 'https://example.org/article'
ALT = 'A diagram showing three side-by-side ROS nodes.'
EMPTY = 'data:image/svg+xml,%3Csvg%3E%3C/svg%3E'

def test_recovers_same_image_from_lazy_source_and_noscript():
    extracted = f'<p>Original article text.</p><img src="{EMPTY}" alt="{ALT}">'
    raw = f'<noscript><img src="/real.webp" alt="{ALT}"></noscript><img src="{EMPTY}" data-src="/real.webp" alt="{ALT}">'
    html, count = repair_html(extracted, raw, BASE)
    soup = BeautifulSoup(html, 'html.parser')
    assert count == 1 and soup.img['src'] == 'https://example.org/real.webp'
    assert 'Original article text.' in soup.get_text()
    assert repair_html(html, raw, BASE) == (html, 0)

def test_refuses_ambiguous_alt_from_different_images():
    old = f'<img src="{EMPTY}" alt="{ALT}">'
    raw = f'<img src="/a.png" alt="{ALT}"><img src="/b.png" alt="{ALT}">'
    assert repair_html(old, raw, BASE) == (old, 0)

def test_does_not_change_real_images_or_use_javascript():
    old = '<img src="https://example.org/keep.png"><p>Keep body</p>'
    assert not needs_repair(old)
    assert repair_html(old, '<img src="/different.png">', BASE) == (old, 0)
    old = f'<img src="{EMPTY}" data-src="javascript:evil()" alt="{ALT}">'
    assert repair_html(old, '', BASE) == (old, 0)
