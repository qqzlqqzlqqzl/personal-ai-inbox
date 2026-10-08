# Existing HTTPS listeners use HTTP/2

Issue #227 was deployed on 2026-10-09 at approximately 04:09 +08.
Nginx 1.24 already included http_v2_module. The only live edits were
`listen 443 ssl;` to `listen 443 ssl http2;` in the existing IP listener
`/etc/nginx/conf.d/toolbox-https.conf` and domain listener
`/etc/nginx/conf.d/zz-toolbox-domain-https.conf`.

Certificates, authentication, service routes and login-only rate limits
were preserved. Internal proxy_http_version 1.1 remains valid and is
independent of browser-to-Nginx HTTP/2; do not change it to 2.

Deployment ran `timeout 12s sudo -n nginx -t`, reloaded Nginx, and waited
for fresh TLS connections to negotiate h2. Certificate validation passed
for lylme.cn, www.lylme.cn and 106.53.40.6. Homepage, /inbox/ and
/events/api/health returned 200. Previous affected bytes were held in RAM
for rollback until checks passed; no new backup directory was created.

A real Edge reload used h2 for API requests. One observed first list took
930ms, metadata 335–463ms. An earlier HTTP/1.1 page waited 3301ms before
send; the h2 page samples sent in about 1ms. These are samples, not a fixed
guarantee: one h2 page still took 5149ms with app timing 525ms, so other
backend/network delay must not be hidden by this change.

This records the live listeners; it does not replace unrelated virtual
hosts. Future changes must keep both 443 listeners consistent and pass
nginx -t before reload.
