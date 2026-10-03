"""Normalize the observed private Drive snapshot shape; no network or credentials."""
from agent_status_store import InvalidSample, MAX_BYTES, STATES, unique_object, validate, integer, keys, timestamp
import json

def normalize(raw, now):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise InvalidSample("size")
    try:
        source = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        raise InvalidSample("json") from None
    base=["schema_version", "feed_id", "writer_id", "revision", "generated_at", "observed_at", "expected_interval_seconds", "collector_state", "scope", "running_count", "workers", "samples", "limitations", "last_error"]
    extended=["source_scope_confirmed", "running_count_definition", "waiting_count", "active_inference_count", "available_slots", "recent_completed", "pending_followups"]
    if not isinstance(source,dict) or set(source) not in [set(base),set(base+extended)]:raise InvalidSample("fields")
    current='source_scope_confirmed' in source
    if current and (source['source_scope_confirmed'] is not True or source['active_inference_count'] is not None):raise InvalidSample('observation_scope')
    if type(source["schema_version"]) is not int or source["schema_version"] != 1 or source["collector_state"] != "ok" or source["last_error"] is not None:
        raise InvalidSample("collector")
    keys(source["scope"], ["kind", "root_excluded", "capacity"])
    if source["scope"]["kind"] != "current_main_task_tree" or source["scope"]["root_excluded"] is not True:
        raise InvalidSample("scope")
    timestamp(source["observed_at"])
    if not isinstance(source["workers"], list) or len(source["workers"]) > 128:
        raise InvalidSample("workers")
    tasks = []
    for worker in source["workers"]:
        worker_base=["id", "name", "state", "model", "reasoning", "observed_at"]
        keys(worker,worker_base+(['scheduler_state','progress'] if current else []))
        if current and worker['scheduler_state']!='running':raise InvalidSample('scheduler_state')
        if worker["observed_at"] != source["observed_at"]:
            raise InvalidSample("mixed_observation")
        tasks.append({key: worker[key] for key in ["name", "state", "model"]})
    counts = dict(total=len(tasks), capacity=source["scope"]["capacity"], active=0, waiting=0, completed=0, failed=0, unknown=0)
    for task in tasks:
        if not isinstance(task["state"], str) or task["state"] not in STATES:
            raise InvalidSample("state")
        counts[STATES[task["state"]]] += 1
        if task['state']=='waiting':counts['active']+=1
    if not integer(source["running_count"]) or source["running_count"] != counts["active"]:
        raise InvalidSample("running_count")
    if current and (not integer(source['waiting_count']) or source['waiting_count']!=counts['waiting']):raise InvalidSample('waiting_count')
    result = {"schema_version": 1, "sequence": source["revision"], "observed_at": source["observed_at"], "tasks": tasks, "statistics": counts}
    # generated_at, writer metadata, reasoning, limitations and errors are never exposed.
    return validate(json.dumps(result, ensure_ascii=False).encode("utf-8"), now)