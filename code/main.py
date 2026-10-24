#!/usr/bin/env python3
"""Ghost Data Still Exists - deterministic deletion-audit agent.

Usage (from the repository root):
    python3 code/main.py                      # audits dataset/deletion_requests.csv -> ./output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --out sample_pred.csv
    python3 code/main.py --explain case_44    # print the reasoning trail for one case
    python3 code/main.py --trail audit_trail.jsonl   # reasoning trail for every case

No network access and no LLM calls: every decision is derived from the supplied files.
A run over the evaluation requests also records run statistics in
code/evaluation/run_stats.json (rendered into code/evaluation/usage_report.md by
code/evaluation/main.py).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ghost_audit.engine import Auditor  # noqa: E402

REPO = os.path.dirname(HERE)
DEFAULT_STATS = os.path.join(HERE, "evaluation", "run_stats.json")


def case_sort_key(cid: str):
    try:
        return int(cid.split("_")[-1])
    except ValueError:
        return cid


def read_requests(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_output(path: str, rows: list[tuple[str, str, list]]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in rows:
            w.writerow([cid, status, json.dumps(steps, separators=(",", ":"), ensure_ascii=False)])


def joint_context(dataset: str, req_path: str) -> list[dict]:
    """Every request the organisation holds (evaluation + public sample), so that shared
    workflow events are attributed consistently across cases."""
    context = read_requests(req_path)
    seen = {r["case_id"] for r in context}
    for extra in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        p = os.path.join(dataset, extra)
        if os.path.exists(p):
            context += [r for r in read_requests(p) if r["case_id"] not in seen]
            seen |= {r["case_id"] for r in context}
    return context


def tool_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:
        return "not installed"


def run_stats(auditor: Auditor, rows: list, out_path: str, wall: float, n_context: int) -> dict:
    ops, statuses, n_steps = Counter(), Counter(), 0
    for _, status, steps in rows:
        statuses[status] += 1
        n_steps += len(steps)
        ops.update(s["operation"] for s in steps)
    with open(out_path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    ev = auditor.data.evidence.stats
    return {
        "requests_audited": len(rows),
        "requests_in_joint_context": n_context,
        "status_distribution": dict(sorted(statuses.items())),
        "steps_total": n_steps,
        "steps_per_request": round(n_steps / max(len(rows), 1), 2),
        "operations": dict(sorted(ops.items())),
        "evidence_files": dict(sorted(ev.get("files", {}).items())),
        "observations": ev.get("observations", 0),
        "documents": ev.get("documents", 0),
        "coverage_listings": ev.get("coverage_listings", 0),
        "ocr_calls": ev.get("ocr_calls", 0),
        "ocr_cache_hits": ev.get("ocr_cache_hits", 0),
        "parse_failures": ev.get("parse_failures", 0),
        "activities": len(auditor.data.activities),
        "identities": len(auditor.data.identities.subjects),
        "timings_s": {k: round(v, 2) for k, v in auditor.timings.items()},
        "wall_time_s": round(wall, 2),
        "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        "python": platform.python_version(),
        "tesseract": tool_version(["tesseract", "--version"]),
        "pdftotext": tool_version(["pdftotext", "-v"]),
        "output_sha256": digest,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: dataset/deletion_requests.csv)")
    ap.add_argument("--out", default=os.path.join(REPO, "output.csv"))
    ap.add_argument("--cache", default=os.path.join(HERE, ".cache"), help="OCR cache directory")
    ap.add_argument("--explain", default=None, help="case_id to print a reasoning trail for")
    ap.add_argument("--trail", default=None, help="write a JSONL reasoning trail for every case")
    ap.add_argument("--stats", default=None, help="run-statistics JSON (default: written for the evaluation run)")
    args = ap.parse_args(argv)

    req_path = args.requests or os.path.join(args.dataset, "deletion_requests.csv")
    t0 = time.time()
    auditor = Auditor(args.dataset, args.cache)
    context = joint_context(args.dataset, req_path)
    auditor.prepare(context)

    rows, trails = [], []
    for r in sorted(read_requests(req_path), key=lambda r: case_sort_key(r["case_id"])):
        status, steps, trail = auditor.audit(r)
        rows.append((r["case_id"], status, steps))
        trails.append(dict(trail, steps=steps))
        if args.explain == r["case_id"]:
            print(json.dumps(dict(trail, request=r, steps=steps), indent=2, ensure_ascii=False))
    write_output(args.out, rows)
    if args.trail:
        with open(args.trail, "w", encoding="utf-8") as fh:
            for t in trails:
                fh.write(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n")
    wall = time.time() - t0
    stats_path = args.stats or (DEFAULT_STATS if args.requests is None else None)
    if stats_path:
        with open(stats_path, "w", encoding="utf-8") as fh:
            json.dump(run_stats(auditor, rows, args.out, wall, len(context)), fh, indent=2)
            fh.write("\n")
    counts = Counter(s for _, s, _ in rows)
    print(f"wrote {len(rows)} rows to {args.out} in {wall:.1f}s  {dict(counts)}")


if __name__ == "__main__":
    main()
