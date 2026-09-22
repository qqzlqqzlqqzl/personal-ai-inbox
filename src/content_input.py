"""Determine the actual input source; never treat a blog RSS summary as full text."""
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

SOCIAL_ROUTES = ('/telegram/channel/', '/twitter/user/', '/instagram/2/')

def is_our_social_feed(feed_url):
    u = urlsplit(feed_url)
    return (u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port == 1200
            and u.path.startswith(SOCIAL_ROUTES) and not u.username)

def content_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script','style','noscript']):
        node.decompose()
    return soup.get_text(' ',strip=True), len(soup.find_all('img'))

def model_payload(entry, text, source, config):
    import json
    used = text[:config['max_chars']]
    message = json.dumps({'title':entry['title'], 'url':entry['url'],
        'content_source':source, 'truncated':len(text)>len(used), 'content':used},ensure_ascii=False)
    return used, message
