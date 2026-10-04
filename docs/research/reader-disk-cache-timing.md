# Disk-cache causal timing proof

The failed ccc11d62 synthetic keyboard/touch runs first reached the new-page
HTTP-cache probe. Each had six exact image requests, disk-cache responses and
loadingFinished records, but the old response-event-before-finish predicate
rejected them. Those runs remain failed with zero completed measurement pairs.
The small regression fixture contains only that synthetic image subset and
uses the last actual visible snapshot as a conservative replay end bound.

Pinned Chromium 153.0.8010.12 InspectorNetworkAgent sends responseReceived with
TimeTicks::Now at notification (lines 1676–1681), while loadingFinished carries
the supplied monotonic_finish_time unless it is null (1800–1806). ResourceTiming
requestTime is seconds; its phase offsets are milliseconds (Network.pdl 110–155;
BuildObjectForTiming 871–897). Notification can therefore follow completion.

The normal ordered predicate is unchanged. Only a late notification with
fromDiskCache exactly true may use the extra proof. Its same-response timing
must include finite, nonnegative requestTime/sendStart/sendEnd/receiveHeadersStart/
receiveHeadersEnd. Sending and header phases must be ordered, and those headers
must complete after this request begins and no later than its loadingFinished.
Decimal arithmetic compares the supplied decimal timestamps without adding an
epsilon or clamping. Unused protocol -1 phase sentinels are retained, not used as
positive timing evidence. The notification itself must still be inside the same
warm/visible window. Every original request ID, URL, MIME, success, visibility,
completion, cache and phase guard remains in force. The evidence records which
causal basis was used. Missing timing and late non-disk responses remain errors.

Official full source, retained locally for review:
- https://raw.githubusercontent.com/chromium/chromium/153.0.8010.12/third_party/blink/renderer/core/inspector/inspector_network_agent.cc
  SHA256 29f17657e2df2a3725a0dc9fdca612bd47ba032453c7be33f7ca9b72de8441a7
- https://raw.githubusercontent.com/chromium/chromium/153.0.8010.12/third_party/blink/public/devtools_protocol/domains/Network.pdl
  SHA256 198b59065cb3e08a5900010f5099a7bf727db4c8f87682146dcfcd2ac6ecbd5b

Unit replays do not prove a new complete browser run or a performance gain.
The next exact Hosted head must rerun all three modes and all five pairs.
