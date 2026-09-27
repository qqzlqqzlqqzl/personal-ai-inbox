#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root"; exit 1; }
BASE=/home/ubuntu/server-review-20260927/nginx-staged/etc/nginx
STAMP=$(date +%Y%m%dT%H%M%S)
BACK=/var/backups/personal-nginx-$STAMP
mkdir -p "$BACK/conf.d" "$BACK/toolbox" "$BACK/snippets"
echo "5ad618c803433da5181743f68fdc1fa107cad3b7f410d523e7ff298177ffe0fe  /etc/nginx/conf.d/toolbox-https.conf" | sha256sum -c -
cp -a "/etc/nginx/conf.d/toolbox-https.conf" "$BACK/conf.d/toolbox-https.conf"
echo "5067b741eb16d310f92fe64418e89623c60964b7a806cc789815ee17c68f2964  /etc/nginx/toolbox/inbox.conf" | sha256sum -c -
cp -a "/etc/nginx/toolbox/inbox.conf" "$BACK/toolbox/inbox.conf"
echo "00422de6b5290fb51085ba16f2c51c5b9da3f2b397a938bc688ded987f244bb3  /etc/nginx/toolbox/newapi.conf" | sha256sum -c -
cp -a "/etc/nginx/toolbox/newapi.conf" "$BACK/toolbox/newapi.conf"
echo "0e9ba0345f046d467e2e4cf7ceb944656dbb3df011c650ac5d278ea7e567ec67  /etc/nginx/toolbox/portal.conf" | sha256sum -c -
cp -a "/etc/nginx/toolbox/portal.conf" "$BACK/toolbox/portal.conf"
for rel in snippets/personal-security-headers.conf conf.d/01-personal-hardening.conf; do [ -e "/etc/nginx/$rel" ] && cp -a "/etc/nginx/$rel" "$BACK/$rel.preexisting" || true; done
install -m 0644 "$BASE/conf.d/toolbox-https.conf" /etc/nginx/conf.d/toolbox-https.conf
install -m 0644 "$BASE/toolbox/inbox.conf" /etc/nginx/toolbox/inbox.conf
install -m 0644 "$BASE/toolbox/newapi.conf" /etc/nginx/toolbox/newapi.conf
install -m 0644 "$BASE/toolbox/portal.conf" /etc/nginx/toolbox/portal.conf
install -m 0644 "$BASE/snippets/personal-security-headers.conf" /etc/nginx/snippets/personal-security-headers.conf
install -m 0644 "$BASE/conf.d/01-personal-hardening.conf" /etc/nginx/conf.d/01-personal-hardening.conf
rollback(){ echo "Rolling back Nginx"; cp -a "$BACK/conf.d/toolbox-https.conf" /etc/nginx/conf.d/toolbox-https.conf; cp -a "$BACK/toolbox/inbox.conf" /etc/nginx/toolbox/inbox.conf; cp -a "$BACK/toolbox/newapi.conf" /etc/nginx/toolbox/newapi.conf; cp -a "$BACK/toolbox/portal.conf" /etc/nginx/toolbox/portal.conf; [ -e "$BACK/snippets/personal-security-headers.conf.preexisting" ] && cp -a "$BACK/snippets/personal-security-headers.conf.preexisting" /etc/nginx/snippets/personal-security-headers.conf || rm -f /etc/nginx/snippets/personal-security-headers.conf; [ -e "$BACK/conf.d/01-personal-hardening.conf.preexisting" ] && cp -a "$BACK/conf.d/01-personal-hardening.conf.preexisting" /etc/nginx/conf.d/01-personal-hardening.conf || rm -f /etc/nginx/conf.d/01-personal-hardening.conf; /usr/sbin/nginx -t && systemctl reload nginx; }
/usr/sbin/nginx -t || { rollback; exit 1; }
systemctl reload nginx || { rollback; exit 1; }
curl -kfsS -H "Host: 106.53.40.6" https://127.0.0.1/ >/dev/null || { rollback; exit 1; }
curl -kfsS -H "Host: 106.53.40.6" https://127.0.0.1/api/status >/dev/null || { rollback; exit 1; }
echo "Nginx hardening applied; backup: $BACK"
