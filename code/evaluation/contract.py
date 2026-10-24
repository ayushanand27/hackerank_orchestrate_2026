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
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def validate_output(path: str, dataset: str) -> list[str]:
    """Return a list of human-readable contract violations (empty = valid)."""
    errs = []
    reqs = {r["case_id"]: r for r in _read(os.path.join(dataset, "deletion_requests.csv"))}
    manifest = {r["file_id"]: r for r in _read(os.path.join(dataset, "evidence_files.csv"))}
    acts = {r["activity_id"]: r for r in _read(os.path.join(dataset, "system_activity.csv"))}
    audits = {r["audit_id"] for r in _read(os.path.join(dataset, "user_audit_log.csv"))}
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
        cid, st = r["case_id"], r["deletion_status"]
        try:
            steps = json.loads(r["steps_json"])
        except json.JSONDecodeError:
            errs.append(f"{cid}: invalid JSON")
            continue
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
            if set(s) != STEP_KEYS:
                errs.append(f"{cid}: bad keys {sorted(s)}")
            if s["step_no"] != i:
                errs.append(f"{cid}: step numbering")
            if OP_STATE.get(s["operation"]) != s["state"] or OP_PHASE.get(s["operation"]) != s["phase"]:
                errs.append(f"{cid}: bad op/state/phase {s}")
            key = (PHASE_ORDER[s["phase"]], OP_RANK[s["operation"]], s["system_id"], s["resource_id"])
            if prev and key < prev:
                errs.append(f"{cid}: steps out of order at {i}")
            prev = key
            ev = s["evidence_id"]
            needs = s["phase"] == "FINDING" or (st == "COMPLETE" and s["phase"] == "VERIFICATION")
            if s["phase"] == "REMEDIATION" or (st == "INCOMPLETE" and s["phase"] == "VERIFICATION"):
                if ev:
                    errs.append(f"{cid}: future step must not cite evidence")
            if needs and ev:
                at = reqs[cid]["audit_at"]
                if ev in manifest:
                    if manifest[ev]["captured_at"] > at:
                        errs.append(f"{cid}: cites {ev} captured after audit_at")
                elif ev in acts:
                    if acts[ev]["event_time"] > at:
                        errs.append(f"{cid}: cites {ev} after audit_at")
                elif ev not in audits:
                    errs.append(f"{cid}: unknown evidence id {ev}")
            if s["phase"] == "FINDING" and not ev:
                errs.append(f"{cid}: finding without evidence")
    return errs
