#!/usr/bin/env python3
"""Evaluation workflow for the Ghost Data deletion-audit agent.

Runs three checks and prints a Markdown summary (also written to evaluation/latest_metrics.md),
then re-renders usage_report.md from the statistics of the last ``code/main.py`` run:

1. Public sample  - agent output on dataset/sample_deletion_requests.csv vs dataset/sample_output.csv
                    (case status accuracy, step precision/recall/F1, field accuracy, sequence accuracy)
2. Custom cases   - synthetic scenarios from custom_cases.py with hand-written expected answers
3. Contract check - structural validation of the full output.csv (518 rows, ordering, vocabulary,
                    evidence-citation rules, no evidence captured after audit_at)

Related: ``ablation.py`` (strategy comparison, ~2 min) and the unit tests
(``python3 -m unittest discover -s code/tests``).

Usage (from repository root):
    python3 code/evaluation/main.py [--output output.csv]
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
REPO = os.path.dirname(CODE)
sys.path.insert(0, CODE)
sys.path.insert(0, HERE)

from ghost_audit.engine import Auditor  # noqa: E402
from contract import validate_output  # noqa: E402
from metrics import compare, summary, load_output_csv  # noqa: E402
import custom_cases  # noqa: E402
import usage  # noqa: E402


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--output", default=os.path.join(REPO, "output.csv"))
    ap.add_argument("--cache", default=os.path.join(CODE, ".cache"))
    args = ap.parse_args(argv)

    md = ["# Latest evaluation run\n"]
    # 1. public sample
    auditor = Auditor(args.dataset, args.cache)
    sample = read_csv(os.path.join(args.dataset, "sample_deletion_requests.csv"))
    allreq = sample + read_csv(os.path.join(args.dataset, "deletion_requests.csv"))
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
        rows = read_csv(args.output)
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
    with open(os.path.join(HERE, "latest_metrics.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    warm = usage._load("run_stats.json")
    if warm:
        with open(os.path.join(HERE, "usage_report.md"), "w", encoding="utf-8") as fh:
            fh.write(usage.render(warm, usage._load("run_stats_cold.json")))
        print("usage_report.md re-rendered from run_stats.json")


if __name__ == "__main__":
    main()
