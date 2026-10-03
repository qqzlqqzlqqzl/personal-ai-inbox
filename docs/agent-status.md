# Reader agent status draft integration

This change adds /inbox/agent-status inside the existing authenticated Reader app and an admin-authorized, same-origin GET /mf/v1/ai/agent-status. It reuses the existing API client and session lifecycle. No deployment, production state path, timer, credentials, OAuth scopes, ports, public sharing or publisher changes are configured.

The API reads only an optional AGENT_STATUS_STATE_PATH cache. Unset, missing or invalid state returns unknown. A separately coordinated puller may later use the existing private Git data branch ops/dot-agent-status and fixed agent-status.json; it validates and atomically replaces last-good, retaining the old observation on failure. The source remains revision 2 and stale; successful retrieval never refreshes the source sampling time. The publisher mechanism and real native agent sampling remain the parent thread's responsibility. The current publisher target is 600 seconds, not a guaranteed 60-second feed.

Browser responses are fully validated before replacing last-good: exact whitelisted fields, schema, safe sequence, UTC/calendar timestamps, task labels/states, unique tasks, capacity and computed statistics. Tasks waiting still count as scheduler-active; active inference is unknown. Malformed/transient responses retain a validated old sample and expose stale/unknown state. HTTP 401/403, stale/invalid authentication and component/session cleanup erase cached private names, counts and timestamps. In-flight results after disposal cannot repopulate the page. Sampling, pull and page refresh times are displayed separately.

The installer runs after all reviewed frontend patch stages and accepts only the pinned public ReactFlux revision and exact reviewed route/toolbar/panel hashes. It adds the lazy route below AuthenticatedApp and an entry in the existing settings panel; it is idempotent and fails before writes on an unknown baseline. The CI builder uses the same stage. No mock file is copied to the built application.

Validation: focused API/installer tests; adversarial contract/cache and actual controller DOM tests; existing view/client tests; pinned frontend preparation repeated successfully; isolated synthetic authentication tests and desktop/mobile mock browser checks. Existing Reader and metadata CI run on the draft PR. Metadata paired-source admission may require separate review because src/api.py changes; do not weaken or regenerate that approval automatically.

Base: current Reader work branch codex/kaggle-qwen36-batches at 0d77969743868cd4ff859a4aec2784115c8c49c8, which includes the reviewed frontend pipeline and PR CI.

