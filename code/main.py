#!/usr/bin/env python3
"""Ghost Data Still Exists - deterministic deletion-audit agent.

Usage (from the repository root):
    python3 code/main.py                      # audits dataset/deletion_requests.csv -> ./output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --out sample_pred.csv
    python3 code/main.py --explain case_44    # print the reasoning trail for one case

No network access and no LLM calls: every decision is derived from the supplied files.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ghost_audit.engine import Auditor  # noqa: E402

REPO = os.path.dirname(HERE)


def case_sort_key(cid: str):
    try:
        return int(cid.split("_")[-1])
    except ValueError:
        return cid


def read_requests(path: str) -> list[dict]:
    return list(csv.DictReader(open(path, encoding="utf-8")))


def write_output(path: str, rows: list[tuple[str, str, list]]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in rows:
            w.writerow([cid, status, json.dumps(steps, separators=(",", ":"), ensure_ascii=False)])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: dataset/deletion_requests.csv)")
    ap.add_argument("--out", default=os.path.join(REPO, "output.csv"))
    ap.add_argument("--cache", default=os.path.join(HERE, ".cache"), help="OCR cache directory")
    ap.add_argument("--explain", default=None, help="case_id to print a reasoning trail for")
    args = ap.parse_args(argv)

    req_path = args.requests or os.path.join(args.dataset, "deletion_requests.csv")
    t0 = time.time()
    auditor = Auditor(args.dataset, args.cache)
    # Joint context: every request the organisation holds (evaluation + public sample) is
    # loaded so that shared workflow events can be attributed consistently across cases.
    context = read_requests(req_path)
    seen = {r["case_id"] for r in context}
    for extra in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        p = os.path.join(args.dataset, extra)
        if os.path.exists(p):
            context += [r for r in read_requests(p) if r["case_id"] not in seen]
            seen |= {r["case_id"] for r in context}
    auditor.prepare(context)

    targets = read_requests(req_path)
    rows = []
    for r in sorted(targets, key=lambda r: case_sort_key(r["case_id"])):
        status, steps, dbg = auditor.audit(r)
        rows.append((r["case_id"], status, steps))
        if args.explain == r["case_id"]:
            c = auditor.ctx[r["case_id"]]
            print(json.dumps({"case": r, "intent": c.intent_reason,
                              "subject": c.subject.user_id if c.subject else None,
                              "primary_account": c.primary_account,
                              "direct_links": {k: sorted(v) for k, v in c.direct.items()},
                              "attributed_links": {k: sorted(v) for k, v in c.attributed.items()},
                              "status": status, "steps": steps}, indent=2, default=str))
    write_output(args.out, rows)
    counts = {}
    for _, s, _ in rows:
        counts[s] = counts.get(s, 0) + 1
    print(f"wrote {len(rows)} rows to {args.out} in {time.time() - t0:.1f}s  {counts}")


if __name__ == "__main__":
    main()
