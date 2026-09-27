# Personal notes and edge hardening follow-up

- Article notes are stored server-side by Miniflux user + entry ID, autosave, max 20,000 characters.
- ReactFlux exposes a “有笔记” view; list payloads expose only has_note metadata, not note text.
- Browser acceptance verified autosave, reload persistence, badge, notes filter, and test-data restoration.
- NewAPI now explicitly uses loopback-only trusted proxies, critical rate limiting, TLS verification, and secure session cookies for https://106.53.40.6.
- Nginx production files were not writable under the authorized session. The staged patch passed a shadow nginx syntax test.
- apply-nginx-hardening-NOT-RUN.sh checks reviewed baseline hashes, backs up, runs nginx -t, reloads, probes, and rolls back on failure. It has NOT been executed.
