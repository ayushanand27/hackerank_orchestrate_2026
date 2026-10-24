#!/usr/bin/env python3
"""Strategy comparison / ablation study.

Every investigation technique of the agent has a switch in ``ghost_audit.config.Features``.
This script runs the full pipeline under several configurations and reports, for each one:

* public-sample metrics (vs ``dataset/sample_output.csv``): status accuracy, exact rows,
  step F1, field accuracy, sequence accuracy;
* custom scenarios solved exactly (``custom_cases.py``);
* on the 518 evaluation requests (no answers available): how many rows differ from the
  submitted configuration, and how many output-contract violations the configuration produces
  (e.g. citing evidence captured after ``audit_at``).

Two families of configurations are run: a cumulative build-up (techniques added one at a
time, in the order they were developed) and leave-one-out (everything on except one).

Usage (from repository root):
    python3 code/evaluation/ablation.py          # writes code/evaluation/ablation.md
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
REPO = os.path.dirname(CODE)
sys.path.insert(0, CODE)
sys.path.insert(0, HERE)

from ghost_audit.config import Features  # noqa: E402
from ghost_audit.engine import Auditor  # noqa: E402
from ghost_audit.evidence import load_all  # noqa: E402
from contract import validate_output  # noqa: E402
from metrics import compare, load_output_csv, summary  # noqa: E402
import custom_cases  # noqa: E402
from ghost_audit.output import write_output  # noqa: E402

# cumulative build-up: (label, features switched on in addition to the previous row)
BUILD_UP = [
    ("exact identifiers only (current snapshot)", []),
    ("+ historical identifiers (audit log)", ["historical_identity"]),
    ("+ post-request state only", ["post_request_only"]),
    ("+ catalogue links + documents (PDF/OCR)", ["catalog_links", "documents"]),
    ("+ lineage (connector / restore)", ["lineage"]),
    ("+ worker-operation category hint", ["worker_hint"]),
    ("+ timing attribution of the primary profile", ["timing_attribution"]),
    ("+ batch cadence re-solve", ["batch_cadence"]),
    ("+ policy-action phase", ["policy_phase"]),
    ("+ absence by omission  (= submitted)", ["omission"]),
]


def read(path):
    """Rows of a CSV file as dicts."""
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evaluate(features: Features, dataset: str, evidence, sample, test, expected, reference=None):
    """Run the full pipeline with ``features``; return sample / custom / evaluation-set measurements."""
    t0 = time.perf_counter()
    auditor = Auditor(dataset, None, features=features, evidence=evidence)
    auditor.prepare(sample + test)
    pred_sample = {r["case_id"]: auditor.audit(r)[:2] for r in sample}
    test_rows = []
    for r in sorted(test, key=lambda r: int(r["case_id"].split("_")[1])):
        st, steps, _ = auditor.audit(r)
        test_rows.append((r["case_id"], st, steps))
    s = summary(compare(expected, pred_sample))
    exp_c, pred_c = custom_cases.run(lambda d: Auditor(d, None, features=features))
    custom = summary(compare(exp_c, pred_c))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "output.csv")
        write_output(path, test_rows)
        violations = len(validate_output(path, dataset))
    test_pred = {cid: (st, steps) for cid, st, steps in test_rows}
    changed = None
    if reference is not None:
        changed = sum(1 for cid, v in test_pred.items() if reference.get(cid) != v)
    return {
        "status": s["status_accuracy"], "exact": s["exact_row_accuracy"], "f1": s["step_f1"],
        "field": s["field_accuracy"], "seq": s["sequence_exact_accuracy"],
        "custom": round(custom["exact_row_accuracy"] * custom["cases"]), "custom_n": custom["cases"],
        "violations": violations, "changed": changed, "seconds": time.perf_counter() - t0,
        "test_pred": test_pred,
    }


def row(label, r):
    """One Markdown table row for a configuration's results."""
    changed = "-" if r["changed"] is None else str(r["changed"])
    return (f"| {label} | {r['status']:.3f} | {r['exact']:.3f} | {r['f1']:.3f} | {r['field']:.3f} | "
            f"{r['seq']:.3f} | {r['custom']}/{r['custom_n']} | {changed} | {r['violations']} |")


HEADER = ("| configuration | status acc. | exact rows | step F1 | field acc. | sequence acc. | custom exact | "
          "test rows changed vs submitted | contract violations |\n|---|---|---|---|---|---|---|---|---|")


def main(argv=None):
    """Run the build-up and leave-one-out configurations and write ablation.md."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--cache", default=os.path.join(CODE, ".cache"))
    ap.add_argument("--out", default=os.path.join(HERE, "ablation.md"))
    args = ap.parse_args(argv)

    evidence = load_all(args.dataset, args.cache)
    sample = read(os.path.join(args.dataset, "sample_deletion_requests.csv"))
    test = read(os.path.join(args.dataset, "deletion_requests.csv"))
    expected = load_output_csv(os.path.join(args.dataset, "sample_output.csv"))

    full = evaluate(Features(), args.dataset, evidence, sample, test, expected)
    reference = full["test_pred"]
    full["changed"] = 0

    md = ["# Strategy comparison (ablation)\n",
          "Generated by `python3 code/evaluation/ablation.py`. Sample metrics are measured against the 32 "
          "public answers; *custom exact* counts the hand-written scenarios of `custom_cases.py`; the last two "
          "columns are measured on the 518 evaluation requests, where no answers are available: how many rows "
          "a configuration changes relative to the submitted one, and how many output-contract violations it "
          "produces.\n",
          "## Cumulative build-up\n", HEADER]
    on: list[str] = []
    for label, add in BUILD_UP:
        on += add
        f = Features().without(*[n for n in Features.names() if n not in on])
        r = full if set(on) == set(Features.names()) else evaluate(f, args.dataset, evidence, sample, test,
                                                                     expected, reference)
        md.append(row(label, r))
        print(row(label, r), flush=True)
    md += ["", "## Leave one out (everything else on)\n", HEADER, row("all techniques (submitted)", full)]
    for name in Features.names():
        r = evaluate(Features().without(name), args.dataset, evidence, sample, test, expected, reference)
        md.append(row(f"without `{name}`", r))
        print(row(f"without {name}", r), flush=True)
    md.append("")
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
