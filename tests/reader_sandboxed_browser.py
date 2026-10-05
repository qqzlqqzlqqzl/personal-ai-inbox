"""Explicit opt-in for new synthetic suites; strict admission with no fallback."""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

from reader_loading_fixture import checked_directory, require
from reader_loading_performance import browser_env, save_new

VERSION = '153.0.8010.12'
BINARY_SHA = '8c599d43aec53f2460a31ae2f4af6bd863f8258b34ff519564bc5d4726bfaa1e'
CONTEXT_OPTIONS = {'viewport', 'locale', 'is_mobile', 'has_touch', 'device_scale_factor',
                   'color_scheme', 'reduced_motion', 'service_workers', 'accept_downloads'}


def validate_context_options(options):
    require(set(options) <= CONTEXT_OPTIONS, 'sandbox suite refuses unreviewed context options')
    require(options.get('service_workers', 'block') == 'block' and options.get('accept_downloads', False) is False,
            'sandbox suite refuses service workers or downloads')


def preflight():
    require(sys.platform == 'linux' and sys.version_info[:3] == (3, 12, 14), 'sandbox suite requires Linux Python 3.12.14')
    require(not os.environ.get('CHROMIUM_EXECUTABLE'), 'sandbox suite refuses executable overrides')
    require(metadata.version('playwright') == '1.63.0', 'sandbox suite requires Playwright 1.63.0')
    import playwright
    rows = json.loads((Path(playwright.__file__).parent / 'driver/package/browsers.json').read_text())['browsers']
    browsers = [r for r in rows if r['name'] in ('chromium', 'chromium-headless-shell')]
    require(len(browsers) == 2 and {r['name'] for r in browsers} == {'chromium', 'chromium-headless-shell'} and
            all(r['revision'] == '1243' and r['browserVersion'] == VERSION for r in browsers),
            'sandbox suite requires exact Chromium 1243 descriptors')


def launch(pw, origin):
    preflight()
    parsed = urlsplit(origin)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port is not None and
            parsed.netloc == f'127.0.0.1:{parsed.port}' and 0 < parsed.port < 65536 and
            not parsed.path and not parsed.query and not parsed.fragment, 'sandbox proxy must be the exact loopback fixture')
    executable = Path(pw.chromium.executable_path)
    checked_directory(executable.parent)
    require(executable.is_file() and not executable.is_symlink() and 'chromium-1243' in executable.parts,
            'sandbox suite requires pinned full Chromium path')
    require(hashlib.sha256(executable.read_bytes()).hexdigest() == BINARY_SHA, 'sandbox Chromium bytes differ')
    parent = checked_directory('/tmp')
    root = Path(tempfile.mkdtemp(prefix='reader-sandbox-', dir=parent))
    env = browser_env(root)
    receipt = {'status': 'NOT_RUN', 'sandbox': True, 'playwright': '1.63.0', 'version': VERSION,
               'binary_sha256': BINARY_SHA, 'environment_names': sorted(env), 'temporary_directory': env['TMPDIR'],
               'environment_and_launch_evidence_retained_at': str(root),
               'browser_profile_retention': 'NOT_PROVEN; managed by Playwright launch lifecycle',
               'fixture_proxy': origin, 'fallback': False}
    browser = None
    try:
        browser = pw.chromium.launch(channel='chromium', headless=True, timeout=15000, env=env, chromium_sandbox=True,
            proxy={'server': origin, 'bypass': '<-loopback>'},
            args=['--disable-background-networking', '--disable-component-update', '--disable-quic',
                  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
        require(browser.version == VERSION, 'actual sandbox Chromium version differs')
        receipt['status'] = 'LAUNCHED'
        return browser, receipt
    except Exception as exc:
        receipt.update(status='FAILED', error_type=type(exc).__name__)
        if browser is not None:
            browser.close()
        raise
    finally:
        save_new(root / 'launch.json', receipt)
