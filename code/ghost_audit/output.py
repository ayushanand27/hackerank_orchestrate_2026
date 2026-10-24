"""Submission file I/O: ``case_id,deletion_status,steps_json`` with compact, deterministic JSON."""
from __future__ import annotations

import csv
import json

HEADER = ["case_id", "deletion_status", "steps_json"]


def case_sort_key(cid: str):
    """``case_7`` < ``case_10`` (numeric order of the trailing number)."""
    try:
        return int(cid.split("_")[-1])
    except ValueError:
        return cid


def serialize_steps(steps: list[dict]) -> str:
    return json.dumps(steps, separators=(",", ":"), ensure_ascii=False)


def write_output(path: str, rows: list[tuple[str, str, list]]):
    """Write rows (case_id, status, steps) in the given order."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for cid, status, steps in rows:
            w.writerow([cid, status, serialize_steps(steps)])
