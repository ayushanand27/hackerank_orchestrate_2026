#!/usr/bin/env python3
"""Evaluation workflow for the Ghost Data deletion-audit agent.

Runs three checks and prints a Markdown summary (also written to evaluation/latest_metrics.md):

1. Public sample  - agent output on dataset/sample_deletion_requests.csv vs dataset/sample_output.csv
                    (case status accuracy, step precision/recall/F1, field accuracy, sequence accuracy)
2. Custom cases   - synthetic scenarios from custom_cases.py with hand-written expected answers
3. Contract check - structural validation of the full output.csv (518 rows, ordering, vocabulary,
                    evidence-citation rules, no evidence captured after audit_at)

Usage (from repository root):
    python3 code/evaluation/main.py [--output output.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
REPO = os.path.dirname(CODE)
sys.path.insert(0, CODE)
sys.path.insert(0, HERE)

from ghost_audit.engine import Auditor  # noqa: E402
from ghost_audit.policy import OP_STATE, OP_PHASE, PHASE_ORDER, OP_RANK  # noqa: E402
from metrics import compare, summary, load_output_csv  # noqa: E402
import custom_cases  # noqa: E402


def fmt_summary(name: str, s: dict) -> str:
    rows = [f"| {k} | {v:.4f} |" if isinstance(v, float) else f"| {k} | {v} |" for k, v in s.items()]
    return f"#### {name}\n\n| metric | value |\n|---|---|\n" + "\n".join(rows) + "\n"


def per_case_table(res: dict) -> str:
    out = ["| case | expected | predicted | exact | missing steps | extra steps |", "|---|---|---|---|---|---|"]
    for cid, pc in res["per_case"].items():
        miss = "<br>".join(f"{m[3]} {m[2]} ({m[5] or '-'})" for m in pc["missing"]) or ""
        extra = "<br>".join(f"{m[3]} {m[2]} ({m[5] or '-'})" for m in pc["extra"]) or ""
        out.append(f"| {cid} | {pc['status'][0]} | {pc['status'][1]} | {'yes' if pc['exact'] else 'no'} | {miss} | {extra} |")
    return "\n".join(out) + "\n"


def validate_output(path: str, dataset: str) -> list[str]:
    errs = []
    reqs = {r["case_id"]: r for r in csv.DictReader(open(os.path.join(dataset, "deletion_requests.csv"), encoding="utf-8"))}
    manifest = {r["file_id"]: r for r in csv.DictReader(open(os.path.join(dataset, "evidence_files.csv"), encoding="utf-8"))}
    acts = {r["activity_id"]: r for r in csv.DictReader(open(os.path.join(dataset, "system_activity.csv"), encoding="utf-8"))}
    audits = {r["audit_id"] for r in csv.DictReader(open(os.path.join(dataset, "user_audit_log.csv"), encoding="utf-8"))}
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip()
    if header != "case_id,deletion_status,steps_json":
        errs.append(f"bad header {header!r}")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
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
            if set(s) != {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}:
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--output", default=os.path.join(REPO, "output.csv"))
    ap.add_argument("--cache", default=os.path.join(CODE, ".cache"))
    args = ap.parse_args(argv)

    md = ["# Latest evaluation run\n"]
    # 1. public sample
    auditor = Auditor(args.dataset, args.cache)
    sample = list(csv.DictReader(open(os.path.join(args.dataset, "sample_deletion_requests.csv"), encoding="utf-8")))
    allreq = sample + list(csv.DictReader(open(os.path.join(args.dataset, "deletion_requests.csv"), encoding="utf-8")))
    auditor.prepare(allreq)
    pred = {}
    for r in sample:
        st, steps, _ = auditor.audit(r)
        pred[r["case_id"]] = (st, steps)
    exp = load_output_csv(os.path.join(args.dataset, "sample_output.csv"))
    res = compare(exp, pred)
    s1 = summary(res)
    md.append("## 1. Public sample (32 cases)\n")
    md.append(fmt_summary("Sample metrics", s1))
    md.append(per_case_table(res))

    # 2. custom scenarios
    exp2, pred2 = custom_cases.run(lambda d: Auditor(d, None))
    res2 = compare(exp2, pred2)
    s2 = summary(res2)
    md.append("## 2. Custom scenarios\n")
    for cid, desc in custom_cases.DESCRIPTIONS.items():
        md.append(f"- **{cid}** - {desc}")
    md.append("")
    md.append(fmt_summary("Custom-case metrics", s2))
    md.append(per_case_table(res2))

    # 3. contract validation of the submission file
    md.append("## 3. Contract validation of output.csv\n")
    if os.path.exists(args.output):
        errs = validate_output(args.output, args.dataset)
        rows = list(csv.DictReader(open(args.output, encoding="utf-8")))
        dist = {}
        for r in rows:
            dist[r["deletion_status"]] = dist.get(r["deletion_status"], 0) + 1
        md.append(f"- rows: {len(rows)}; status distribution: {dist}")
        md.append(f"- contract violations: {len(errs)}")
        for e in errs[:20]:
            md.append(f"  - {e}")
    else:
        md.append("- output.csv not found (run `python3 code/main.py` first)")
    text = "\n".join(md) + "\n"
    print(text)
    open(os.path.join(HERE, "latest_metrics.md"), "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    main()
