"""Output-contract validator for a submission file (see AGENTS.md section 6.3-6.6)."""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ghost_audit.policy import OP_PHASE, OP_RANK, OP_STATE, PHASE_ORDER  # noqa: E402

STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def _read(path):
    """Rows of a CSV file as dicts."""
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _evidence_time(ev: str, ref: dict):
    """Capture / event time of a cited id, ``""`` for an audit-log id, ``None`` if unknown."""
    if ev in ref["manifest"]:
        return ref["manifest"][ev]["captured_at"]
    if ev in ref["acts"]:
        return ref["acts"][ev]["event_time"]
    return "" if ev in ref["audits"] else None


def check_step(cid: str, status: str, i: int, s: dict, prev, audit_at: str, ref: dict, errs: list):
    """Vocabulary, numbering, ordering and evidence rules for step ``i`` of a case; returns its
    sort key so the caller can check the order of the next step."""
    if set(s) != STEP_KEYS:
        errs.append(f"{cid}: bad keys {sorted(s)}")
    if s["step_no"] != i:
        errs.append(f"{cid}: step numbering")
    if OP_STATE.get(s["operation"]) != s["state"] or OP_PHASE.get(s["operation"]) != s["phase"]:
        errs.append(f"{cid}: bad op/state/phase {s}")
    key = (PHASE_ORDER[s["phase"]], OP_RANK[s["operation"]], s["system_id"], s["resource_id"])
    if prev and key < prev:
        errs.append(f"{cid}: steps out of order at {i}")
    ev = s["evidence_id"]
    future = s["phase"] == "REMEDIATION" or (status == "INCOMPLETE" and s["phase"] == "VERIFICATION")
    if future and ev:
        errs.append(f"{cid}: future step must not cite evidence")
    if s["phase"] == "FINDING" and not ev:
        errs.append(f"{cid}: finding without evidence")
    if ev and not future:
        t = _evidence_time(ev, ref)
        if t is None:
            errs.append(f"{cid}: unknown evidence id {ev}")
        elif t > audit_at:
            errs.append(f"{cid}: cites {ev} captured after audit_at")
    return key


def check_row(r: dict, audit_at: str, ref: dict, errs: list):
    """Status / step-list rules for one output row."""
    cid, st = r["case_id"], r["deletion_status"]
    try:
        steps = json.loads(r["steps_json"])
    except json.JSONDecodeError:
        errs.append(f"{cid}: invalid JSON")
        return
    if st not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        errs.append(f"{cid}: bad status {st}")
    if st == "NOT_APPLICABLE" and steps:
        errs.append(f"{cid}: NOT_APPLICABLE must have []")
    if st == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
        errs.append(f"{cid}: COMPLETE with non-verification steps")
    if st == "INCOMPLETE" and not any(s["phase"] == "FINDING" for s in steps):
        errs.append(f"{cid}: INCOMPLETE without findings")
    prev = None
    for i, s in enumerate(steps, 1):
        prev = check_step(cid, st, i, s, prev, audit_at, ref, errs)


def validate_output(path: str, dataset: str) -> list[str]:
    """Return a list of human-readable contract violations (empty = valid)."""
    errs: list[str] = []
    reqs = {r["case_id"]: r for r in _read(os.path.join(dataset, "deletion_requests.csv"))}
    ref = {"manifest": {r["file_id"]: r for r in _read(os.path.join(dataset, "evidence_files.csv"))},
           "acts": {r["activity_id"]: r for r in _read(os.path.join(dataset, "system_activity.csv"))},
           "audits": {r["audit_id"] for r in _read(os.path.join(dataset, "user_audit_log.csv"))}}
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip()
    if header != "case_id,deletion_status,steps_json":
        errs.append(f"bad header {header!r}")
    rows = _read(path)
    if len(rows) != len(reqs):
        errs.append(f"row count {len(rows)} != {len(reqs)}")
    ids = [r["case_id"] for r in rows]
    if ids != sorted(ids, key=lambda c: int(c.split("_")[1])):
        errs.append("rows not sorted by case_id")
    if set(ids) != set(reqs):
        errs.append("case ids differ from deletion_requests.csv")
    for r in rows:
        audit_at = reqs[r["case_id"]]["audit_at"] if r["case_id"] in reqs else ""
        check_row(r, audit_at, ref, errs)
    return errs
