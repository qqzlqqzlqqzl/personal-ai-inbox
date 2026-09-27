import xml.etree.ElementTree as ET

from x_feed_server import render_atom


def test_render_atom_decodes_upstream_html_entities_once():
    xml = render_atom(
        "rasbt",
        [
            {
                "id": "123",
                "url": "https://x.com/rasbt/status/123",
                "created_at": "2026-09-09T13:26:10Z",
                "text": "Models &amp; systems",
                "author": {"name": "Sebastian Raschka"},
                "media": [],
            }
        ],
    )
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    assert entry.findtext("a:title", namespaces=ns) == "Models & systems"
    content = entry.findtext("a:content", namespaces=ns)
    assert "Models &amp; systems" in content
    assert "&amp;amp;" not in content
