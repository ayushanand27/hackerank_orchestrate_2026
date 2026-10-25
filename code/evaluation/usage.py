#!/usr/bin/env python3
"""Render ``usage_report.md`` from the statistics recorded by the final run.

``python3 code/main.py`` writes ``code/evaluation/run_stats.json`` (warm run, OCR cache present).
``run_stats_cold.json`` is the same run with an empty OCR cache (``--cache`` pointing at a new
directory), recorded once to show that OCR from scratch gives a byte-identical output.

Usage (from repository root):
    python3 code/evaluation/usage.py
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Statistics JSON from this folder, or ``None`` when it was not recorded."""
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# USD per 1M tokens (input, output). GroqCloud on-demand price for openai/gpt-oss-120b as listed in
# October 2026 (https://www.morphllm.com/groq-pricing, citing the GroqCloud model docs).
PRICES_PER_MTOK = {"openai/gpt-oss-120b": (0.15, 0.60)}
PROVIDER_NAMES = {"groq": "Groq (OpenAI-compatible API)", "anthropic": "Anthropic"}


def cost_usd(model: str, inp: int, out: int) -> float | None:
    """Estimated cost of a model's tokens, or None when its price is unknown."""
    price = PRICES_PER_MTOK.get(model)
    return None if price is None else (inp * price[0] + out * price[1]) / 1e6


def model_usage_section(warm: dict) -> list[str]:
    """Provider / model / calls / tokens / cost tables, per model and overall, plus per-request averages."""
    n = warm["requests_audited"]
    agent = warm.get("agent", {})
    live = agent.get("mode") == "review"
    per_model = agent.get("per_model", {}) if live else {}
    provider = PROVIDER_NAMES.get(agent.get("provider", ""), agent.get("provider", ""))
    rows, total_cost = [], 0.0
    for model, u in per_model.items():
        c = cost_usd(model, u["input_tokens"], u["output_tokens"])
        total_cost += c or 0.0
        rows.append(f"| {provider} | `{model}` | {u['calls']} | {u['input_tokens']} | {u['output_tokens']} | "
                    f"{u['input_tokens'] + u['output_tokens']} | {'%.4f' % c if c is not None else 'n/a'} |")
    calls = sum(u["calls"] for u in per_model.values())
    inp = sum(u["input_tokens"] for u in per_model.values())
    out = sum(u["output_tokens"] for u in per_model.values())
    reviewed = max(agent.get("reviewed", 0), 1)
    lines = [
        "## Model usage",
        "",
        "The audit engine that writes `output.csv` is **fully deterministic and makes no model calls**: every",
        "output field is derived from participant data by explicit rules, so it is reproducible byte for byte",
        "and cannot hallucinate resources, states or evidence ids.",
    ]
    if live:
        lines += ["", f"In the final run the **review agent ran live** on {agent.get('reviewed', 0)} of the deletion "
                  "cases (`--agent review --agent-limit`, the first cases in case order, which include the hand-made "
                  "single-artifact cases). All model calls below come from that agent; they cannot change `output.csv`."]
    lines += ["", "| provider | model | model calls | input tokens | output tokens | total tokens | est. cost (USD) |",
              "|---|---|---|---|---|---|---|"]
    lines += rows or ["| — (none) | — | 0 | 0 | 0 | 0 | 0.00 |"]
    lines += [f"| **overall** | | **{calls}** | **{inp}** | **{out}** | **{inp + out}** | **{total_cost:.4f}** |", "",
              "| average | per request audited (" + str(n) + ") | per agent-reviewed case (" + str(agent.get('reviewed', 0)) + ") |",
              "|---|---|---|",
              f"| model calls | {calls / n:.2f} | {calls / reviewed:.2f} |",
              f"| tokens (input + output) | {(inp + out) / n:.0f} | {(inp + out) / reviewed:.0f} |",
              f"| estimated cost (USD) | {total_cost / n:.5f} | {total_cost / reviewed:.5f} |", ""]
    return lines


def run_summary_section(warm: dict) -> list[str]:
    """What the run read and produced."""
    files = warm["evidence_files"]
    return [
        "## Run summary",
        "",
        "| item | value |",
        "|---|---|",
        f"| requests audited | {warm['requests_audited']} (joint attribution context: "
        f"{warm['requests_in_joint_context']} requests incl. the public sample) |",
        f"| status distribution | {', '.join(f'{k} {v}' for k, v in warm['status_distribution'].items())} |",
        f"| steps emitted | {warm['steps_total']} ({warm['steps_per_request']} per request) |",
        f"| evidence files parsed | {sum(files.values())} ({', '.join(f'{v} {k}' for k, v in files.items())}); "
        f"parse failures: {warm['parse_failures']} |",
        f"| normalised observations | {warm['observations']} (+ {warm['documents']} documents, "
        f"{warm['coverage_listings']} complete-snapshot listings) |",
        f"| activity events / identities | {warm['activities']} / {warm['identities']} |",
        f"| `output.csv` SHA-256 | `{warm['output_sha256']}` |",
        "",
    ]


def compute_section(warm: dict, cold: dict | None) -> list[str]:
    """Local tools used and wall time per stage (warm and cold OCR)."""
    n, files, t = warm["requests_audited"], warm["evidence_files"], warm["timings_s"]
    cold_ocr = f"; a cold run makes {cold['ocr_calls']}" if cold else ""
    lines = [
        "## Local compute actually used",
        "",
        "| component | what it does | calls in the final run |",
        "|---|---|---|",
        f"| {warm['tesseract']} (local OCR) | reads the {files.get('image', 0)} console screenshots (normal + "
        f"inverted polarity) | {warm['ocr_calls']} (cache hits: {warm['ocr_cache_hits']}){cold_ocr} |",
        f"| {warm['pdftotext']} | text of {files.get('pdf', 0)} PDFs (`pypdf` fallback) | {files.get('pdf', 0)} |",
        f"| sqlite3 (stdlib) | loads {files.get('sql', 0)} SQL snapshots in memory | {files.get('sql', 0)} |",
        f"| Python {warm['python']} | parsing, linking, attribution, planning | — |",
        "",
        "| stage | wall time (s) |",
        "|---|---|",
    ]
    for stage, label in (("load", "load evidence, identities, activity"), ("linking", "intent + identity + linking"),
                         ("attribution", "joint timing attribution"), ("audit", "per-case state + planning")):
        if stage in t:
            lines.append(f"| {label} | {t[stage]} |")
    lines.append(f"| **total (warm, OCR cached)** | **{warm['wall_time_s']}** "
                 f"(≈ {1000 * warm['wall_time_s'] / n:.0f} ms / request) |")
    if cold:
        same = "byte-identical" if cold.get("output_sha256") == warm["output_sha256"] else "DIFFERENT"
        lines.append(f"| total (cold, OCR from scratch) | {cold['wall_time_s']} — output {same} (SHA-256 compared) |")
    return lines + [""]


def _trail_reviews() -> list:
    """Reviews recorded in audit_trail.jsonl by the last run (empty when the agent was off)."""
    path = os.path.join(HERE, "audit_trail.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [(t["case_id"], t["review"]) for t in map(json.loads, fh) if "review" in t]


def agent_section(warm: dict, dry: dict | None) -> list[str]:
    """The review agent: live results of the final run (if any) and the offline dry-run measurement."""
    agent = warm.get("agent", {})
    mode = agent.get("mode", "off")
    lines = ["## Review agent (`code/ghost_audit/agent/`)", "",
             f"Mode in the final run: **{mode}**. The agent investigates a case through read-only tools and must "
             "submit a validated review; a deterministic gate lets it confirm a row or flag it for human review, "
             "never change `output.csv`.", ""]
    reviews = _trail_reviews() if mode == "review" else []
    if reviews:
        turns = [r.get("turns", 0) for _, r in reviews]
        tools = {}
        for _, r in reviews:
            for t in r.get("tool_calls", []):
                tools[t] = tools.get(t, 0) + 1
        flagged = [(cid, r) for cid, r in reviews if r.get("flagged")]
        rejected = sum(len(r.get("rejected", [])) for _, r in reviews)
        lines += ["| live review result | value |", "|---|---|",
                  f"| cases reviewed | {len(reviews)} |",
                  f"| verdicts | {', '.join(f'{k} {v}' for k, v in agent.get('verdicts', {}).items())} |",
                  f"| flagged for human review | {len(flagged)} |",
                  f"| average turns per case (cap 8) | {sum(turns) / len(turns):.1f} |",
                  f"| tool calls chosen by the model | {', '.join(f'{k} {v}' for k, v in sorted(tools.items()))} |",
                  f"| reviews rejected by validation and re-asked | {rejected} |",
                  "| rows of output.csv changed by the agent | 0 (gate) |", ""]
        if flagged:
            lines += ["Flagged cases (the model's own summary):", ""]
            lines += [f"- `{cid}` {r.get('verdict')}: {r.get('summary', '')[:200]}" for cid, r in flagged[:10]]
            lines.append("")
    if dry:
        a = dry["agent"]
        per = next(iter(a["per_model"].values()), {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        k = max(a["reviewed"], 1)
        lines += [
            "Offline dry run (`--agent dry-run`: the same loop, tools, validation and gate with a scripted model "
            "that reads the plan and agrees; token counts are estimates = characters / 4, no API call):",
            "",
            "| reviewed cases | model calls | est. input tokens | est. output tokens | calls / case | est. input tokens / case |",
            "|---|---|---|---|---|---|",
            f"| {a['reviewed']} | {per['calls']} | {per['input_tokens']} | {per['output_tokens']} | "
            f"{per['calls'] / k:.1f} | {per['input_tokens'] / k:.0f} |",
            "",
        ]
    return lines


def render(warm: dict, cold: dict | None = None, dry: dict | None = None) -> str:
    """Full usage_report.md text from the warm-run (and optional cold-run) statistics."""
    lines = [
        "# Usage report — final full-dataset run",
        "",
        "Generated by `python3 code/evaluation/usage.py` from `code/evaluation/run_stats.json`, which",
        "`python3 code/main.py` records while producing `output.csv`.",
        "",
        "```bash",
        "python3 code/main.py      # dataset/deletion_requests.csv -> output.csv",
        "```",
        "",
    ]
    lines += model_usage_section(warm) + agent_section(warm, dry) + run_summary_section(warm) + compute_section(warm, cold)
    lines += [
        "## Development-time AI usage (not part of the inference pipeline)",
        "",
        "The solution was written with an AI coding assistant (Claude, in the claude.ai app; see `log.txt`",
        "for the turn-by-turn record). The assistant read the dataset, formed hypotheses and wrote code;",
        "it is not called by `code/main.py`, so it contributes no inference-time calls, tokens or cost.",
        "",
    ]
    return "\n".join(lines)


def main():
    """Render usage_report.md next to this file and print it."""
    warm = _load("run_stats.json")
    if warm is None:
        raise SystemExit("run python3 code/main.py first (writes code/evaluation/run_stats.json)")
    text = render(warm, _load("run_stats_cold.json"), _load("agent_dry_run.json"))
    with open(os.path.join(HERE, "usage_report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
