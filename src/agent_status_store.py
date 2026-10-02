"""Credential-free state validator/store; call only from a trusted pull adapter."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 65536
STALE_AFTER = 1800  # Three approved 10-minute publisher intervals.
STATES = {"running": "active", "waiting": "waiting", "done": "completed", "failed": "failed", "unknown": "unknown"}
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
    # Labels must also be chosen from the publisher's approved public-name catalog.
    return isinstance(value, str) and 0 < len(value) <= 80 and not any(ord(c) < 32 for c in value) and not any(c in value for c in "\\/:<>")

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidSample("duplicate_field")
        result[key] = value
    return result

def validate(raw, now):
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
        keys(task, ["name", "state", "model"])
        if not label(task["name"]) or task["name"] in names or not label(task["model"]):
            raise InvalidSample("labels")
        names.add(task["name"])
        if not isinstance(task["state"], str) or task["state"] not in STATES:
            raise InvalidSample("state")
        counts[STATES[task["state"]]] += 1
        if task['state']=='waiting':counts['active']+=1 # Scheduler-running includes waits.
    keys(sample["statistics"], counts)
    if any(not integer(v) for v in sample["statistics"].values()) or sample["statistics"] != counts:
        raise InvalidSample("statistics")
    return sample

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
        keys(value, ["sample", "last_successful_pull_at", "last_attempt_at", "pull_status", "error_code"])
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
    value["last_attempt_at"] = utc(now)
    try:
        if raw is None:
            raise InvalidSample("transport")
        sample = validate(raw, now)
        if old is not None:
            if sample["sequence"] < old["sequence"] or timestamp(sample["observed_at"]) < timestamp(old["observed_at"]):
                raise InvalidSample("regression")
            if sample["sequence"] == old["sequence"] and sample != old:
                raise InvalidSample("sequence_conflict")
        value.update(sample=sample, last_successful_pull_at=utc(now), pull_status="ok", error_code=None)
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
