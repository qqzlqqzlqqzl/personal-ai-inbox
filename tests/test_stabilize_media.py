import pytest
from stabilize_media import signed_url,decoded_original,renew


def test_stable_signature_survives_repeat_and_matches_source():
    original='https://ph-files.imgix.net/actual.jpg?auto=format&fit=crop'
    signed=signed_url(original,'isolated-test-key')
    assert signed_url(original,'isolated-test-key')==signed
    assert decoded_original(signed)==original
    assert renew(signed,'isolated-test-key')==signed
    old=signed_url(original,'old-test-key')
    assert renew(old,'isolated-test-key')==signed
    assert renew('<img src="'+old+'">','isolated-test-key')=='<img src="'+signed+'">'


@pytest.mark.parametrize('url',['http://127.0.0.1/secret','http://169.254.169.254/','http://localhost/','https://user:secret@example.org/','file:///etc/passwd'])
def test_media_repair_does_not_sign_local_or_authenticated_urls(url):
    with pytest.raises(ValueError):
        signed_url(url,'isolated-test-key')
