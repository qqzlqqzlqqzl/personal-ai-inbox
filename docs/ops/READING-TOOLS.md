# Reader reading tools

Full article translation was reauthorized on 2026-10-07 as on-demand work.
Only the mounted view of a current score >=8 English article requests translation
through authenticated POST. Detail/status GET and every prefetch remain read-only
for body translation. Previously complete translations are reused; cancelled
unfinished jobs remain dormant until their article is opened again.
See [BILINGUAL-READER.md](BILINGUAL-READER.md) for its cache and usage boundaries.

Reading layout and focus controls use labeled icon buttons at the top right
of the article, together with the language icon; layout opens the existing settings panel to the left, Escape
returns focus, and focus mode retains reading position.

The bilingual CI scope runs its worker/API/install tests, real React component
tests and the locked frontend build. The existing 1 GiB shared image cache and
native reader are preserved. Unknown or unrelated mixed source changes still
select the existing full regression.
