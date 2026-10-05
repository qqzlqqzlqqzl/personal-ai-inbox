# Canonical-parent virtual environment compatibility

This source starts with the reviewed c7bbb3dc5e660ca790cef41922e3cc2f6f3b56c7 package. canonical_operator.py adds runtime path binding; the collector, transport, SOURCE_PINS and original 40 controls remain byte-identical. README.zh.md now documents canonical-parent execution. Nine additional Linux filesystem controls cover interpreter/path compatibility and retain every fixture, including the original alias before recreation, under OPERATOR_TEST_RETAIN_ROOT.

The current deployment exposes its existing Kaggle venv through a release symlink. Running its Python from the canonical **bin directory**, while retaining the executable leaf, makes importlib locate the same verified packages through canonical paths. Fully resolving bin/python to the system binary loses the venv and is rejected.

The operator now binds configured and running interpreter files, bin directories, configured venv root, sys.prefix and sys.exec_prefix. All alias/directory/leaf traces are saved in the preflight snapshot, rewalked before acceptance and compared by the existing before/after execution checks. No SDK-source guard, token guard, request cap, proxy/TLS requirement, claim/submission restriction or evidence-directory guard is removed. This is path compatibility, not permission expansion.

Use a new attempt after independent source review; do not replay any old preflight or execution marker. Provider observations still do not grant logical-lane continuity, CAS, claim transitions, duplicate-safe resubmission or specific submission-version verification. Real Kaggle recovery and the new-article pipeline remain separate acceptance work.
