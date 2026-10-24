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


def model_usage_section(warm: dict) -> list[str]:
    """Provider / model / calls / tokens / cost tables (overall and per request)."""
    n = warm["requests_audited"]
    tokens = warm["input_tokens"] + warm["output_tokens"]
    return [
        "## Model usage",
        "",
        "The agent is **fully deterministic and makes no LLM / VLM / API calls**. Every output field is",
        "derived from participant data by explicit rules, so the run is reproducible byte-for-byte and",
        "cannot hallucinate resources, states or evidence ids.",
        "",
        "| provider | model | model calls | input tokens | output tokens | total tokens | est. cost (USD) |",
        "|---|---|---|---|---|---|---|",
        f"| — (none) | — | {warm['llm_calls']} | {warm['input_tokens']} | {warm['output_tokens']} | "
        f"{tokens} | {warm['cost_usd']:.2f} |",
        f"| **overall** | | **{warm['llm_calls']}** | **{warm['input_tokens']}** | **{warm['output_tokens']}** | "
        f"**{tokens}** | **{warm['cost_usd']:.2f}** |",
        "",
        f"| per request ({n}) | value |",
        "|---|---|",
        f"| average model calls | {warm['llm_calls'] / n:g} |",
        f"| average tokens (input + output) | {tokens / n:g} |",
        f"| estimated cost per request | ${warm['cost_usd'] / n:.2f} |",
        "",
    ]


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


def render(warm: dict, cold: dict | None = None) -> str:
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
    lines += model_usage_section(warm) + run_summary_section(warm) + compute_section(warm, cold)
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
    text = render(warm, _load("run_stats_cold.json"))
    with open(os.path.join(HERE, "usage_report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
