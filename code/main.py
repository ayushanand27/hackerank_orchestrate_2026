#!/usr/bin/env python3
"""Ghost Data Still Exists - deterministic deletion-audit agent.

Usage (from the repository root):
    python3 code/main.py                      # audits dataset/deletion_requests.csv -> ./output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --out sample_pred.csv
    python3 code/main.py --explain case_44    # print the reasoning trail for one case
    python3 code/main.py --trail trail.jsonl  # reasoning trail for every case to another file
    python3 code/main.py --agent review       # + Claude review agent per case (needs ANTHROPIC_API_KEY)
    python3 code/main.py --agent review --agent-provider groq   # same agent via Groq (GROQ_API_KEY)
    python3 code/main.py --agent dry-run      # the same agent loop with a scripted offline model

By default there is no network access and no LLM call: every decision is derived from the
supplied files. The optional review agent (code/ghost_audit/agent) investigates each case with
read-only tools and can only confirm a row or flag it for human review; it never changes
output.csv.
A run over the evaluation requests (the default) also writes
  code/evaluation/audit_trail.jsonl  - one JSON line per case: intent, subject, primary profile,
                                       every linked resource with the reasons it was linked,
                                       its state / category / evidence, and the emitted steps
  code/evaluation/run_stats.json     - run statistics (rendered into usage_report.md)
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
from ghost_audit.output import case_sort_key, write_output  # noqa: E402

REPO = os.path.dirname(HERE)
DEFAULT_STATS = os.path.join(HERE, "evaluation", "run_stats.json")
DEFAULT_TRAIL = os.path.join(HERE, "evaluation", "audit_trail.jsonl")


def read_requests(path: str) -> list[dict]:
    """Rows of a requests CSV."""
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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
    """First line of a tool's version output (or 'not installed')."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:
        return "not installed"


def run_reviews(auditor: Auditor, rows: list, trails: list, requests: dict, mode: str, limit: int | None,
                model: str | None, provider: str = "anthropic") -> dict:
    """Run the review agent over deletion cases; adds ``review`` to each trail. Returns agent stats.

    With ``--agent-provider groq`` and several comma-separated keys in ``GROQ_API_KEYS``, one worker
    per key reviews its share of the cases in parallel (each key has its own rate limit)."""
    from ghost_audit.agent import AnthropicClient, OpenAICompatibleClient, ScriptedClient, UsageMeter, review_cases
    meter = UsageMeter()
    items = [(requests[cid], status, steps, trail) for (cid, status, steps), trail in zip(rows, trails)
             if status != "NOT_APPLICABLE"][:limit]
    if mode == "review" and provider == "groq":
        keys = [k.strip() for k in os.environ.get("GROQ_API_KEYS", "").split(",") if k.strip()] or [None]
        clients = [OpenAICompatibleClient(model=model, meter=meter, api_key=k) for k in keys]
    elif mode == "review":
        clients = [AnthropicClient(model=model, meter=meter)]
    else:  # dry-run: a scripted model that inspects the plan, then agrees
        def script(turn, _messages):
            if turn == 0:
                return {"stop_reason": "tool_use", "content": [
                    {"type": "tool_use", "id": "t0", "name": "get_case_plan", "input": {}}]}
            return {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": f"t{turn}", "name": "submit_review",
                 "input": {"verdict": "AGREE", "concerns": [], "summary": "dry run: plan inspected"}}]}

        class FreshScript(ScriptedClient):
            """A scripted client whose turn counter restarts for every case."""
            def create(self, system, messages, tools):
                if len(messages) == 1:
                    self.requests = []
                return super().create(system, messages, tools)
        clients = [FreshScript(script, meter=meter)]
    reviews, verdicts = review_cases(auditor, items, clients, log=lambda m: print(m, file=sys.stderr, flush=True))
    for (cid, _status, _steps), trail in zip(rows, trails):
        if cid in reviews:
            trail["review"] = reviews[cid]
    return {"mode": mode, "provider": provider if mode == "review" else "scripted", "workers": len(clients),
            "reviewed": len(reviews), "verdicts": dict(sorted(verdicts.items())),
            "flagged_for_human_review": sum(v for k, v in verdicts.items() if k != "AGREE"),
            "per_model": meter.as_dict()}


def run_stats(auditor: Auditor, rows: list, out_path: str, wall: float, n_context: int,
              agent: dict | None = None) -> dict:
    """Statistics of this run for usage_report.md (counts, timings, tool versions, output hash)."""
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
        "agent": agent or {"mode": "off"},
        "python": platform.python_version(),
        "tesseract": tool_version(["tesseract", "--version"]),
        "pdftotext": tool_version(["pdftotext", "-v"]),
        "output_sha256": digest,
    }


def main(argv=None):
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(REPO, "dataset"))
    ap.add_argument("--requests", default=None, help="requests CSV (default: dataset/deletion_requests.csv)")
    ap.add_argument("--out", default=os.path.join(REPO, "output.csv"))
    ap.add_argument("--cache", default=os.path.join(HERE, ".cache"), help="OCR cache directory")
    ap.add_argument("--explain", default=None, help="case_id to print a reasoning trail for")
    ap.add_argument("--trail", default=None, help="JSONL reasoning trail (default: written for the evaluation run)")
    ap.add_argument("--stats", default=None, help="run-statistics JSON (default: written for the evaluation run)")
    ap.add_argument("--agent", choices=("off", "review", "dry-run"), default="off",
                    help="optional review agent: Claude (review), scripted offline model (dry-run) or none")
    ap.add_argument("--agent-limit", type=int, default=None, help="review at most this many deletion cases")
    ap.add_argument("--agent-model", default=None, help="model id (default: $GHOST_AUDIT_MODEL or the provider default)")
    ap.add_argument("--agent-provider", choices=("anthropic", "groq"), default="anthropic",
                    help="anthropic (ANTHROPIC_API_KEY) or groq (GROQ_API_KEY, OpenAI-compatible)")
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
    agent_stats = None
    if args.agent != "off":
        requests = {r["case_id"]: r for r in read_requests(req_path)}
        try:
            agent_stats = run_reviews(auditor, rows, trails, requests, args.agent, args.agent_limit, args.agent_model,
                                      args.agent_provider)
        except (RuntimeError, ImportError) as exc:   # no key / SDK: the deterministic output is already written
            sys.exit(f"review agent unavailable: {exc}")
        print(f"review agent ({args.agent}): {agent_stats['reviewed']} cases, verdicts {agent_stats['verdicts']}")
    trail_path = args.trail or (DEFAULT_TRAIL if args.requests is None else None)
    if trail_path:
        with open(trail_path, "w", encoding="utf-8") as fh:
            for t in trails:
                fh.write(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n")
    wall = time.time() - t0
    stats_path = args.stats or (DEFAULT_STATS if args.requests is None else None)
    if stats_path:
        with open(stats_path, "w", encoding="utf-8") as fh:
            stats = run_stats(auditor, rows, args.out, wall, len(context), agent_stats)
            if args.agent == "review":
                per = agent_stats["per_model"].values()
                stats.update(llm_calls=sum(m["calls"] for m in per), input_tokens=sum(m["input_tokens"] for m in per),
                             output_tokens=sum(m["output_tokens"] for m in per))
            json.dump(stats, fh, indent=2)
            fh.write("\n")
    counts = Counter(s for _, s, _ in rows)
    print(f"wrote {len(rows)} rows to {args.out} in {wall:.1f}s  {dict(counts)}")


if __name__ == "__main__":
    main()
