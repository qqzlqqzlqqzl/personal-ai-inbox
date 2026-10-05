# Explicit product comparison profile

This source change adds an optional manual profile to the existing Reader A/B
workflow. Pull requests and the default manual run remain `regression`, with
the original 879/7554 input table unchanged byte for byte.

`product` selects only the tracked fixed path
`tests/fixtures/reader-loading-product-ab-inputs.json`. Its current state is
`BOUND` to the reviewed original 34ad build-only artifact 11319676247, with
125 files, original ZIP and complete build-manifest hashes. Its producer run
37246559550 ended in **failure**; that terminal result is retained. All 125
build file paths, sizes and hashes match the separately reviewed F44 build.
This admits a bounded measurement of those product bytes, not deployment.
An isolated UNBOUND fixture still fails before prepare or network. The CLI
accepts only `regression` and `product`, not arbitrary paths, URLs or repository
names. Every phase checks the profile/path/manifest SHA saved by prepare; the
instrument identity includes the selected table.

After the separately reviewed build-only producer runs, verify its actual
immutable artifact ID, original ZIP bytes/SHA/CRC, complete build file set and
per-file SHA/manifest digest, HEAD/tree/src, producer run/attempt and terminal
conclusion. Only then may a separately reviewed commit replace UNBOUND with a
complete BOUND table. The fixed baseline remains 879. The producer's actual
new commit is recorded even if its product source matches F44. Matching source
does not establish byte-identical output: compare its complete build manifest
with the original F44 build manifest before making that statement.

Only after that binding is reviewed and the workflow change is actually
published can the main coordinator choose `comparison_profile=product` in the
existing `reader-loading-ab.yml` manual workflow, at an exact reviewed ref.
The old workflow version has no such input. Binding the current profile does
not establish a speedup result; measurement and final-release regression are
separate gates.

There is no permission or secret expansion. The same-repository action reads,
32 MiB input limit, archive limits, five serial AB/BA groups in all three modes,
590-second complete measurement deadline, v3 prose DOM observation, strict
six-image cache proof, sandbox, CSP and network rejection are preserved. No
existing timing, old failed producer or old measurement is relabeled. The new
profile alone does not replace ordinary regression, paired or visual evidence.
