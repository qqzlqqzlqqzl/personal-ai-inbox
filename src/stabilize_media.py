"""One-time repair of saved proxy URLs after configuring Miniflux's stable key.
Matches Miniflux 2.3.3 internal/mediaproxy/url.go. Never exposes the key.
"""
import argparse,base64,hashlib,hmac,ipaddress,json,re,secrets,time
from pathlib import Path
from urllib.parse import urlsplit
import core

PROXY = re.compile(r'(?:https?://(?:106\.53\.40\.6|127\.0\.0\.1)(?::\d+)?)?/mf/proxy/[A-Za-z0-9_=-]+/([A-Za-z0-9_=-]+)')


def decoded_original(value):
    match = PROXY.fullmatch(value or '')
    if not match:
        return None
    try:
        return base64.urlsafe_b64decode(match[1] + '='*(-len(match[1])%4)).decode()
    except (ValueError,UnicodeError):
        return None


def signed_url(url,key):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('invalid_media_url')
    if p.hostname.lower() in ('localhost','metadata.google.internal'):
        raise ValueError('nonpublic_media_url')
    try: address=ipaddress.ip_address(p.hostname)
    except ValueError: address=None
    if address is not None and not address.is_global:
        raise ValueError('nonpublic_media_url')
    raw=url.encode()
    digest=hmac.new(key.encode(),raw,hashlib.sha256).digest()
    return '/mf/proxy/'+base64.urlsafe_b64encode(digest).decode()+'/'+base64.urlsafe_b64encode(raw).decode()


def renew(value,key):
    def replace(match):
        original=decoded_original(match[0])
        if not original:
            return match[0]
        try: return signed_url(original,key)
        except ValueError: return match[0]
    return PROXY.sub(replace,value or '')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true',required=True)
    parser.parse_args()
    from initialize_secrets import read_env
    envpath=core.ROOT/'.private/miniflux.env'
    config=read_env('miniflux.env')
    key=config.get('MEDIA_PROXY_PRIVATE_KEY')
    if config.get('MEDIA_PROXY_PRIVATE_KEY_FILE') and not key:
        raise RuntimeError('Review file-based key manually; do not replace it')
    created=not bool(key)
    if created:
        backup=core.ROOT/'.private'/('miniflux-before-stable-media-'+str(int(time.time()))+'.env')
        backup.write_bytes(envpath.read_bytes());backup.chmod(0o600)
        key=secrets.token_urlsafe(36)
        lines=[line for line in envpath.read_text().splitlines() if not line.startswith('MEDIA_PROXY_PRIVATE_KEY=')]
        envpath.write_text('\n'.join(lines)+'\nMEDIA_PROXY_PRIVATE_KEY='+key+'\n')
        envpath.chmod(0o600)
    counts={'covers':0,'prepared_bodies':0}
    with core.connect() as db:
        for row in db.execute('SELECT entry_id,cover_url FROM analyses WHERE cover_url IS NOT NULL').fetchall():
            value=renew(row['cover_url'],key)
            if value!=row['cover_url']:
                db.execute('UPDATE analyses SET cover_url=? WHERE entry_id=?',(value,row['entry_id']))
                counts['covers']+=1
        for row in db.execute('SELECT entry_id,content FROM prepared_articles').fetchall():
            value=renew(row['content'],key)
            if value!=row['content']:
                db.execute('UPDATE prepared_articles SET content=? WHERE entry_id=?',(value,row['entry_id']))
                counts['prepared_bodies']+=1
    report={'at':time.time(),'stable_key_created':created,'private_key_exposed':False,
            'updated':counts,'miniflux_restart_required':created,'model_requests':0}
    (core.ROOT/'artifacts/media-signature-repair.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':
    main()
