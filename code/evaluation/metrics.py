"""Scoring helpers shared by the evaluation workflow."""
from __future__ import annotations

import json

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def step_key(s: dict, with_evidence=True) -> tuple:
    ks = FIELDS if with_evidence else FIELDS[:-1]
    return tuple(s.get(k, "") for k in ks)


def lcs(a: list, b: list) -> int:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[n][m]


def compare(expected: dict, predicted: dict) -> dict:
    """expected/predicted: case_id -> (status, steps(list))"""
    res = {"cases": 0, "status_ok": 0, "exact_rows": 0, "exp_steps": 0, "pred_steps": 0, "tp_steps": 0,
           "tp_steps_noev": 0, "field_hits": 0, "field_total": 0, "seq_ok": 0, "lcs": 0, "per_case": {}}
    for cid, (est, esteps) in expected.items():
        pst, psteps = predicted.get(cid, ("MISSING", []))
        res["cases"] += 1
        res["status_ok"] += est == pst
        ek = [step_key(s) for s in esteps]
        pk = [step_key(s) for s in psteps]
        ekn = [step_key(s, False) for s in esteps]
        pkn = [step_key(s, False) for s in psteps]
        tp = len(set(ek) & set(pk))
        tpn = len(set(ekn) & set(pkn))
        res["exp_steps"] += len(ek)
        res["pred_steps"] += len(pk)
        res["tp_steps"] += tp
        res["tp_steps_noev"] += tpn
        # positional field accuracy
        for i in range(max(len(esteps), len(psteps))):
            e = esteps[i] if i < len(esteps) else {}
            p = psteps[i] if i < len(psteps) else {}
            for f in FIELDS:
                res["field_total"] += 1
                res["field_hits"] += (e.get(f) == p.get(f)) and f in e
        seq = ek == pk
        res["seq_ok"] += seq
        res["lcs"] += lcs(ek, pk)
        exact = est == pst and seq
        res["exact_rows"] += exact
        res["per_case"][cid] = {"status": (est, pst), "exact": exact,
                                "missing": [s for s in ek if s not in pk], "extra": [s for s in pk if s not in ek]}
    return res


def summary(res: dict) -> dict:
    p = res["tp_steps"] / res["pred_steps"] if res["pred_steps"] else 0.0
    r = res["tp_steps"] / res["exp_steps"] if res["exp_steps"] else 0.0
    pn = res["tp_steps_noev"] / res["pred_steps"] if res["pred_steps"] else 0.0
    rn = res["tp_steps_noev"] / res["exp_steps"] if res["exp_steps"] else 0.0
    return {
        "cases": res["cases"],
        "status_accuracy": res["status_ok"] / max(res["cases"], 1),
        "exact_row_accuracy": res["exact_rows"] / max(res["cases"], 1),
        "step_precision": p, "step_recall": r, "step_f1": 2 * p * r / (p + r) if p + r else 0.0,
        "step_f1_ignoring_evidence": 2 * pn * rn / (pn + rn) if pn + rn else 0.0,
        "field_accuracy": res["field_hits"] / max(res["field_total"], 1),
        "sequence_exact_accuracy": res["seq_ok"] / max(res["cases"], 1),
        "sequence_lcs_ratio": res["lcs"] / max(res["exp_steps"], 1),
    }


def load_output_csv(path: str) -> dict:
    import csv
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        out[r["case_id"]] = (r["deletion_status"], json.loads(r["steps_json"] or "[]"))
    return out
