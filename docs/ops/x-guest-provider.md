# X guest provider

Personal AI Inbox reads public X timelines without an X API key by running `x-cli`
locally and translating its guest GraphQL output into Atom feeds.

## Runtime

- x-cli: v0.5.0
- binary: `runtime/x-cli/x`
- release archive: `x_0.5.0_linux_amd64.tar.gz`
- verified SHA-256: `d722fd04f05c3785202846a1400caa9bcc0f8237947c0e35f6e8cf9efcbd1dd7`
- provider: `127.0.0.1:17910`
- Atom adapter: `127.0.0.1:17911`
- outbound proxy: `127.0.0.1:17890`

The provider is deliberately read-only. No X account cookie, auth token, or API key is
stored by this integration.

## Install

Download the matching release archive and its checksums from
`tamnd/x-cli` release `v0.5.0`, verify the checksum, extract the `x` binary into
`runtime/x-cli/`, then install the four unit files in this directory into
`~/.config/systemd/user/`.

Run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now ai-news-x-provider.service
systemctl --user enable --now ai-news-x-feed.service
systemctl --user enable --now ai-news-x-prefetch.timer
```

## Behavior

`x_feed_server.py` serves `/x/user/<handle>` Atom feeds. It caches up to 20 recent
posts per author and serves stale cache when X temporarily rate-limits the guest
surface. `x_feed_prefetch.py` refreshes a bounded rotating batch so ordinary
Miniflux polling usually hits local cache rather than X.

The X roster is `x_sources.catalog.json`. The AI ingestion layer treats
`http://127.0.0.1:17911/x/user/... ` as a social-post source, so the post body is
used directly instead of attempting full-page extraction from x.com.
