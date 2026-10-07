# Reader reading tools

Full article AI translation was removed at the user request on 2026-10-07.
The translation worker, detail enqueue, API endpoint, source module, installer
and three language buttons are removed. The Reader private BILINGUAL_*
environment was removed before publishing; other services and their keys remain.
Existing article/cached database data is retained.

Reading layout and focus controls use labeled icon buttons at the top right
of the article; layout opens the existing settings panel to the left, Escape
returns focus, and focus mode retains reading position.

Runtime src/ returns exactly to the previously deployed image-cache release
37cf05313e1652ca093609025bfbc1e268092daf. Retirement CI requires that exact tree
and removal of the complete named feature set; any unknown deletion or mixed
runtime change still selects the full regression. The focused image/API tests
and locked frontend build cover the restored reader and its image cache.
