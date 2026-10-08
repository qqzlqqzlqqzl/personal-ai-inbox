# Rolling translation and existing incomplete articles

Only eligible English articles scored at least 8 are translated. The opted-in
worker discovers recent articles every five minutes and shares the existing
100-request concurrency ceiling and daily 1,000-request / 1,000,000-token budget.
Budget pauses resume automatically after the UTC day changes (08:00 UTC+8).

An analysis plaintext fallback is a known loss of original structure. Before a
new rolling translation is enqueued, the worker attempts the existing bounded
original-body recovery. Native structured bodies and already bound repairs
retain their current source and successful cache. A failed recovery keeps its
explicit completeness state; that fallback is skipped rather than newly paid
for as a full structured article.

Technical literals such as sizes, opcodes and constrained product identifiers
can remain unchanged. Ordinary English prose still requires Chinese output.
When repeated format validation fails, translation recovery splits text around
the source markers, translates the text fragments, and inserts the original
markers programmatically. Missing or duplicate fragment IDs are rejected. HTML
and links still originate only from the source; successful blocks are retained.
The normal retry limit and budget remain in effect.

For issue185, production inspection found 24 current articles with 71 exhausted
missing blocks, and 35 other selected articles using plaintext fallback. The
one-time operator repair is constrained to those audited current source hashes
and still-missing blocks. It gives the corrected path two bounded attempts,
preserves previous successful translations, and records recovery failures.
It does not reset or retranslate the library.
