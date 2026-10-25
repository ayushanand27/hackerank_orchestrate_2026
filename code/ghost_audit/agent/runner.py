"""Run the review agent over many cases, optionally with several API keys in parallel.

Each worker owns one model client (one API key, so one rate-limit budget) and reviews its share of
the cases one after another; all workers share one thread-safe usage meter. Reviews only touch the
audit trail, so the order in which they finish cannot change any output.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .investigator import CaseInvestigator

MAX_CONSECUTIVE_API_ERRORS = 3


def review_shard(auditor, client, items: list, log=None) -> list:
    """Review ``items`` = [(request, status, steps, trail)] with one client; returns [(case_id, review)]."""
    out, errors = [], 0
    investigator = CaseInvestigator(auditor, client)
    for req, status, steps, trail in items:
        if errors >= MAX_CONSECUTIVE_API_ERRORS:
            out.append((req["case_id"], {"verdict": "UNCERTAIN", "flagged": True,
                                         "summary": "not reviewed: model API unavailable"}))
            continue
        try:
            review = investigator.review(req, status, steps, trail).as_dict()
            errors = 0
        except (RuntimeError, OSError) as exc:   # API unavailable: flag the case, keep the row
            errors += 1
            review = {"verdict": "UNCERTAIN", "flagged": True, "summary": f"model API error: {exc}"[:300]}
        out.append((req["case_id"], review))
        if log:
            log(f"[agent] {req['case_id']} {review['verdict']} ({review.get('turns', 0)} turns)")
    return out


def review_cases(auditor, items: list, clients: list, log=None) -> tuple[dict, Counter]:
    """Split ``items`` round-robin over ``clients`` (one worker each); returns ({case_id: review}, verdicts)."""
    shards = [items[i::len(clients)] for i in range(len(clients))]
    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        parts = list(pool.map(lambda pair: review_shard(auditor, pair[0], pair[1], log), zip(clients, shards)))
    reviews = {cid: r for part in parts for cid, r in part}
    return reviews, Counter(r["verdict"] for r in reviews.values())
