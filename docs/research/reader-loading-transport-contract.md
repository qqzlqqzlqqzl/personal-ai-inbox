# Reader loading transport profiles

This changes the synthetic transport used for comparison. It is not a product
speed improvement, a production latency claim, or evidence that the five pairs
have run. The earlier identity-only weak-network failures remain valid retained
results under their original conditions.

`identity-v1` preserves admitted static bytes without content coding. The new
`gzip6-static-text-v1` profile uses the locked Python standard library with level
6 and mtime 0, for exact static HTML/CSS/JavaScript MIME types, with a threshold
of zero bytes. These are reproducible fixture parameters, not a claim to know
production nginx's gzip implementation, threshold, or complete configuration.
The local coordinator has observed gzip on specific production resources; that
limited evidence does not imply every resource uses gzip or prove equal wire
bytes. Python and zlib build/runtime versions are part of each profile receipt.

The baseline's 125-file source manifest and every raw SHA stay unchanged. Each
static response records raw size/hash and selected representation hash, actual
received Accept-Encoding, selected coding and Vary. Compressed bytes must
roundtrip to the admitted source before serving. The server's body length is not
substituted for CDP received bytes, especially for unfinished requests.

Negotiation follows RFC 9110 section 12.5.3. Coding tokens and q are case
insensitive. Explicit coding entries override `*`; a zero quality forbids that
coding. Explicit identity quality competes with gzip. Implicit identity remains
the fallback when no supported coding is requested. For a missing header the
server chooses identity; an empty header also selects identity. `x-gzip` is
recognized as gzip. When both available text representations are unacceptable,
the fixture responds 406. Malformed or conflicting duplicate preferences return
400. Input is bounded to 2048 ASCII characters and 32 list members; reasonable
empty list members are ignored. Equal gzip/identity quality prefers gzip.

GET and HEAD use the same selected representation headers; HEAD writes no body.
Vary: Accept-Encoding is sent for both representations in the new text profile.
Content-Length describes that representation. Errors remain no-store and are
not compressed. No new static ETag or 304 mechanism is added. Existing image
ETags/304 and PNG bytes are unchanged; API JSON stays outside this transport
selection. Existing CSP, origin rejection, authorization, unknown-route failure,
cache lifetimes, first-24 5s assertion, target-row 15s deadline, 24/48/72 pages,
five pairs, and strict six-image new-page cache proof are retained.

The CLI accepts an explicit `--transport-profile`; its default remains identity.
The candidate Hosted measurement adapter selects the new gzip profile explicitly.
Baseline and candidate must use identical profile parameters, implementation
versions, browser, input, network and scenario. The comparator refuses missing,
mixed, altered or pair-inconsistent profiles, including old records that do not
state the transport contract. CDP evidence now retains Content-Encoding and Vary.
No old raw timing is compared to new gzip timing as a product improvement.

References: https://www.rfc-editor.org/rfc/rfc9110.html#section-12.5.3 and
https://www.rfc-editor.org/rfc/rfc9111.html#section-4.1
