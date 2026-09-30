# Adafruit attribution extraction (#36)

## Behavior

Only a link immediately attributed by “See more at” is considered. Multiple attributed targets, Via/Hacker News/social links, unknown publishers, embedded credentials, non-HTTP(S), nonstandard ports, and cross-site redirects are rejected. The first reviewed adapters are Medium and CERN EP; additional publishers require explicit selectors and regression evidence. A same-site redirect chain has at most three redirects, requests have a total 45-second deadline, decoded HTML is capped at 3 MiB, and extracted text at 200,000 characters.

A selected original needs matching canonical identity, an article heading related to the source title, a unique known body container, no detected access gate, at least 600 characters, and at least 1.35 times the Adafruit text. Otherwise the complete Adafruit summary remains available, accurately labeled `adafruit_summary`. The linked source is sanitized, preserves code text, and supplies both the model input and a durable reader body. The existing entry ID, title, and source URL remain unchanged. `content_source_url` and the receipt expose the final body URL; the batch receipt also records target URL, fallback reason, hashes and rule version. Changes to upstream content invalidate the durable body.

This change affects newly processed entries. It does not enqueue historical entries, run a library-wide repair, or spend any model/GPU budget. An explicitly requested historical repair must remain entry-ID bounded.

## Fresh external regression, 2026-09-30 UTC

The official https://blog.adafruit.com/feed/ returned HTTP 200 and included both issue examples with their exact links. Adafruit HTML pages returned HTTP 403 in this environment, so their feed-provided content was used, matching the production input described in the issue.

- Mitsubishi: https://blog.adafruit.com/2026/09/28/adding-a-non-wifi-mitsubishi-ac-to-home-assistant/
  - Exact attributed original: https://medium.com/@ivangomezarnedo/how-i-added-a-non-wi-fi-mitsubishi-ac-to-home-assistant-22770661dd77
  - Medium returned HTTP 403. All 1,021 summary characters were preserved; receipt reports `original_http_403` and `adafruit_summary`. No Hacker News request was made. Full Medium extraction was not verified and is not claimed.
- CERN: https://blog.adafruit.com/2026/09/28/the-cern-vhdl-common-library-released-for-everyone-to-use/
  - Exact attributed original: https://ep-news.web.cern.ch/colibri-cern-vhdl-common-library-released-for-everyone-to-use/
  - HTTP 200; canonical URL and heading matched. Selected 2,485 characters compared with the 1,046-character summary.
  - Selected body SHA-256: `34b37d994dc8eea27f150d664742c373bd1ad60027ee1109ae607985e75848c8`.

## Verification boundary

Focused adapter, durable-body, paid-worker-disabled, Kaggle preparation/recovery identity, and existing extraction tests are covered by the commands in the PR. Synthetic tests verify fallback, unsafe redirects, size/timeout bounds, content identity, concurrent state changes, no cross-user reader leakage, and retained publisher corrections. External regression uses no credentials, paid provider calls, GPU jobs or production writes.

A clean release-branch checkout cannot collect the full Kaggle suite: `exception_audit.py` and `recovery_policy.py` are imported but absent from Git. Existing frontend patch reproducibility and hardcoded deployment paths are separate baseline issues; they must not be represented as failures introduced or solved by this adapter. Deployment and production entry readback were not run from this cloud workspace. Keep #36 open until the deployment's tracked/untracked code difference is reconciled and the production readback is verified.
