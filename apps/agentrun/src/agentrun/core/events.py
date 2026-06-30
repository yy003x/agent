"""events.jsonl:单调 seq 追加 + 只读广播(见 design/02 §4)。"""
from __future__ import annotations

import uuid
from typing import Any

from agentrun.core.jsonio import append_jsonl, read_jsonl
from agentrun.core.run import utc_now
from agentrun.core.rundir import RunPaths

EVENT_SCHEMA_VERSION = 1


def next_seq(paths: RunPaths) -> int:
    records = read_jsonl(paths.events_file)
    if not records:
        return 1
    return int(records[-1].get("seq", len(records))) + 1


def append_event(paths: RunPaths, run_id: str, run_type: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """追加一条事件;data 由调用方保证已脱敏(不写完整 prompt/secret)。"""
    record = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "run_id": run_id,
        "run_type": run_type,
        "type": event_type,
        "ts": utc_now(),
        "seq": next_seq(paths),
        "data": data,
    }
    append_jsonl(paths.events_file, record)
    return record
