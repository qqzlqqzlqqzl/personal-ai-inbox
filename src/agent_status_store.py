"""Credential-free state validator/store; call only from a trusted pull adapter."""
import json
import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 65536
STALE_AFTER = 1800  # Three approved 10-minute publisher intervals.
STATES = {"running": "active", "waiting": "waiting", "done": "completed", "failed": "failed", "unknown": "unknown"}
# No display-name catalogue has been approved. A schema-safe string is not
# permission to publish it. Future additions require an explicit source review.
APPROVED_TASK_NAMES = frozenset()
APPROVED_MODEL_NAMES = frozenset()
class InvalidSample(ValueError):
    pass

def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise InvalidSample("timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidSample("timestamp") from None

def utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise InvalidSample("fields")

def integer(value):
    return type(value) is int and 0 <= value <= 9007199254740991

def label(value):
    return isinstance(value, str) and 0 < len(value) <= 80 and not any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in value) and not any(c in value for c in "\\/:<>")

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidSample("duplicate_field")
        result[key] = value
    return result

def _validate_source(raw, now):
    """Internal structural input; may contain private labels, never an API result."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise InvalidSample("size")
    try:
        sample = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError):
        raise InvalidSample("json") from None
    keys(sample, ["schema_version", "sequence", "observed_at", "tasks", "statistics"])
    if type(sample["schema_version"]) is not int or sample["schema_version"] != 1 or not integer(sample["sequence"]):
        raise InvalidSample("version_or_sequence")
    if (timestamp(sample["observed_at"]) - now).total_seconds() > 30:
        raise InvalidSample("future_sample")
    tasks = sample["tasks"]
    if not isinstance(tasks, list) or len(tasks) > 128:
        raise InvalidSample("tasks")
    capacity = sample["statistics"].get("capacity") if isinstance(sample["statistics"], dict) else None
    if not integer(capacity) or not len(tasks) <= capacity <= 128:
        raise InvalidSample("capacity")
    counts = dict(total=len(tasks), capacity=capacity, active=0, waiting=0, completed=0, failed=0, unknown=0)
    names = set()
    for task in tasks:
        if not isinstance(task, dict) or 'state' not in task or not set(task) <= {'name', 'state', 'model'}:
            raise InvalidSample("fields")
        if any(not label(task[key]) for key in ('name', 'model') if key in task):
            raise InvalidSample("labels")
        if 'name' in task:
            if task['name'] in names:
                raise InvalidSample('labels')
            names.add(task['name'])
        if not isinstance(task["state"], str) or task["state"] not in STATES:
            raise InvalidSample("state")
        counts[STATES[task["state"]]] += 1
        if task['state']=='waiting':counts['active']+=1 # Scheduler-running includes waits.
    keys(sample["statistics"], counts)
    if any(not integer(v) for v in sample["statistics"].values()) or sample["statistics"] != counts:
        raise InvalidSample("statistics")
    return sample

def public_sample(sample):
    """Copy only approved display fields; unnamed tasks keep their truthful state."""
    tasks = []
    for task in sample['tasks']:
        public = {'state': task['state']}
        if task.get('name') in APPROVED_TASK_NAMES:
            public['name'] = task['name']
        if task.get('model') in APPROVED_MODEL_NAMES:
            public['model'] = task['model']
        tasks.append(public)
    return {**sample, 'tasks': tasks, 'statistics': dict(sample['statistics'])}

def validate(raw, now):
    return public_sample(_validate_source(raw, now))

def sample_identity(sample):
    # Stored privately, never returned by response(). Preserve same-revision
    # conflict detection without retaining the rejected display strings.
    data = json.dumps(sample, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def read_store(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        expected = {"sample", "last_successful_pull_at", "last_attempt_at", "pull_status", "error_code"}
        if not isinstance(value, dict) or set(value) not in (expected, expected | {'sample_sha256'}):
            raise InvalidSample('fields')
        if 'sample_sha256' in value and (value['sample_sha256'] is not None and
                (not isinstance(value['sample_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', value['sample_sha256']) is None)):
            raise InvalidSample('sample_identity')
        if 'sample_sha256' in value and (value['sample'] is None) != (value['sample_sha256'] is None):
            raise InvalidSample('sample_identity')
        if value['pull_status'] not in ['ok','failed','unknown']:
            raise InvalidSample('pull_status')
        for key in ['last_successful_pull_at','last_attempt_at']:
            if value[key] is not None:timestamp(value[key])
        if value['sample'] is None and (value['pull_status']=='ok' or value['last_successful_pull_at'] is not None):
            raise InvalidSample('missing_sample')
        return value
    except FileNotFoundError:
        return {"sample": None, "last_successful_pull_at": None, "last_attempt_at": None, "pull_status": "unknown", "error_code": None}

def record_pull(path, raw, now):
    """Single writer required. None means adapter transport failure; never persist error text."""
    value = read_store(path)
    old = value["sample"]
    if old is not None:
        old = _validate_source(json.dumps(old, ensure_ascii=False).encode('utf-8'), now)
        old_identity = value.get('sample_sha256') or sample_identity(old)
        value.update(sample=public_sample(old), sample_sha256=old_identity)
    else:
        old_identity = None
        value['sample_sha256'] = None
    value["last_attempt_at"] = utc(now)
    try:
        if raw is None:
            raise InvalidSample("transport")
        sample = _validate_source(raw, now)
        identity = sample_identity(sample)
        if old is not None:
            if sample["sequence"] < old["sequence"] or timestamp(sample["observed_at"]) < timestamp(old["observed_at"]):
                raise InvalidSample("regression")
            if sample["sequence"] == old["sequence"] and identity != old_identity:
                raise InvalidSample("sequence_conflict")
        value.update(sample=public_sample(sample), sample_sha256=identity,
                     last_successful_pull_at=utc(now), pull_status="ok", error_code=None)
    except InvalidSample as error:
        value.update(pull_status="failed", error_code=str(error))
    atomic_write(path, value)
    return response(value, now)

def response(value, now):
    sample = value["sample"]
    if sample is not None:
        sample = validate(json.dumps(sample, ensure_ascii=False).encode("utf-8"), now)
    age = (now - timestamp(sample["observed_at"])).total_seconds() if sample else None
    pulled = value["last_successful_pull_at"]
    pull_age = (now - timestamp(pulled)).total_seconds() if pulled else None
    freshness = "unknown" if sample is None else "stale" if age > STALE_AFTER or pull_age is None or pull_age > STALE_AFTER else "fresh" if value["pull_status"] == "ok" else "unknown"
    return {"sample": sample, "last_successful_pull_at": pulled, "last_attempt_at": value["last_attempt_at"], "pull_status": value["pull_status"], "freshness": freshness, "stale_after_seconds": STALE_AFTER, "target_interval_seconds": 600}
