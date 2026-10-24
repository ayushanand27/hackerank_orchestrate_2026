"""Timing-based attribution of identifier-free records.

Some records never carry the subject's identifiers: a deleted profile answers ``404`` without
an e-mail, an anonymised analytics profile has no identifiers left, and a retained legal hold is
only referenced by the workflow.  The privacy workflow, however, leaves a reproducible timing
signature in ``system_activity.csv``, which is used here -- always *jointly* over all requests
that share a status report, so one event is never given to two cases.

1. :func:`attribute_primary_accounts` -- the primary profile is submitted 30-90 min before the
   workflow reports its status and the worker's result mirrors that status
   (COMPLETE->SUCCESS, PARTIALLY_COMPLETE->PARTIAL, NOT_STARTED->FAILED).  Workflows that name
   their target ("Target resource: X") are taken literally.
2. :func:`refine_accounts_by_batch_cadence` -- inside an audit batch consecutive requests have
   their submission offset advance by a constant step; the per-batch offset is fitted from the
   unambiguous cases and every reporting group is re-solved with the cadence distance as cost.
3. :func:`attribute_policy_actions` -- follow-up "Policy action completed" events share the
   seconds-of-minute phase of the case's primary submission, which ties anonymised analytics and
   retained legal holds to exactly one case.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .config import (CADENCE_BASE_S, CADENCE_MIN_BATCH, CADENCE_PERIOD_S, CADENCE_SEARCH_STEP_S,
                     CADENCE_STEP_S, CADENCE_TOL_S, CADENCE_TRAILING_SHIFT, DIRECT_REPORT_WINDOW_MIN,
                     POLICY_ACTION_WINDOW_MIN, POLICY_OPERATION_OFFSET_S, POLICY_PHASE_OFFSET_S,
                     POLICY_PHASE_TOL_S, POLICY_SLOT_ADVANCE_S, PRIMARY_WINDOW_MIN,
                     RETAINED_CATEGORIES, STATUS_RESULT, Features)
from .context import AuditData, CaseContext, minutes_between, rid_num, ts
from .evidence import system_for
from .state import effective_state, resolve_category


def is_primary_submission(a: dict) -> bool:
    return a["target_system"] == "account_service" and a["event_type"] == "DELETE_RECORD" \
        and "submitted primary profile" in a["details"]


def in_window(t: datetime, ref: datetime, window: tuple) -> bool:
    lo, hi = window
    return lo <= minutes_between(t, ref) <= hi


def owned_by_identifier(rid: str, all_cases: list[CaseContext]) -> bool:
    """Some deletion case already holds ``rid`` through an identifier link."""
    return any(rid in k.direct for k in all_cases if k.deletion)


def expected_result(c: CaseContext) -> str | None:
    return STATUS_RESULT.get(c.req["reported_status"])


def last_resort_account(data: AuditData, c: CaseContext) -> str | None:
    """The subject's only account observed between the request and the audit."""
    accts = sorted({o.resource_id for o in data.observations
                    if o.resource_id in c.direct and o.category == "USER_PROFILE"
                    and c.requested_at <= o.time <= c.audit_at})
    return accts[0] if len(accts) == 1 else None


# ----------------------------------------------------------------------------- 1. primary profile
def attribute_primary_accounts(data: AuditData, R: datetime, cases: list[CaseContext],
                               all_cases: list[CaseContext], features: Features):
    """Assign a primary profile to every case of one reporting group (reported at ``R``)."""
    primary, direct_report = [], []
    for a in data.activities:
        if is_primary_submission(a) and in_window(a["_t"], R, PRIMARY_WINDOW_MIN):
            if features.timing_attribution:
                primary.append(a)
        elif "Target resource:" in a["details"] and in_window(a["_t"], R, DIRECT_REPORT_WINDOW_MIN) \
                and a["event_type"] != "SYNC_RECORD":
            direct_report.append(a)

    # (a) workflow reports that name their target resource explicitly
    for a in direct_report:
        owners = [c for c in cases if a["resource_id"] in c.direct]
        if not owners and len(cases) == 1 and not owned_by_identifier(a["resource_id"], all_cases):
            owners = cases
        for c in owners[:1]:
            c.attributed[a["resource_id"]].add("report:" + a["activity_id"])
            if a["target_system"] == "account_service" and c.primary_account is None:
                c.primary_account = a["resource_id"]

    # (b) a submitted profile the case is already linked to by identifiers
    taken = {c.primary_account for c in cases if c.primary_account}
    for c in cases:
        if c.primary_account:
            continue
        for a in primary:
            if a["resource_id"] in c.direct:
                c.primary_account = a["resource_id"]
                taken.add(a["resource_id"])
                break

    # (c) unique status-compatible candidate (repeat until nothing changes)
    free = [a for a in primary if a["resource_id"] not in taken
            and not owned_by_identifier(a["resource_id"], all_cases)]
    todo = [c for c in cases if c.primary_account is None]
    changed = True
    while changed and todo and free:
        changed = False
        for c in list(todo):
            comp = [a for a in free if a["reported_result"] == expected_result(c)]
            if len(comp) == 1:
                rivals = [k for k in todo if k is not c and expected_result(k) == comp[0]["reported_result"]]
                if not rivals:
                    c.primary_account = comp[0]["resource_id"]
                    c.attributed[comp[0]["resource_id"]].add("timing:" + comp[0]["activity_id"])
                    todo.remove(c)
                    free.remove(comp[0])
                    changed = True

    # (d) remaining ties: case order <-> submission order (refined by the batch cadence later)
    for res in ("SUCCESS", "PARTIAL", "FAILED"):
        cs = [c for c in todo if expected_result(c) == res]
        fs = sorted([a for a in free if a["reported_result"] == res], key=lambda a: a["_t"])
        for c, a in zip(cs, fs):
            c.primary_account = a["resource_id"]
            c.attributed[a["resource_id"]].add("timing:" + a["activity_id"])

    # (e) last resort: the subject's own account observed after the request
    for c in cases:
        if c.primary_account is None:
            c.primary_account = last_resort_account(data, c)
    for c in cases:
        if c.primary_account and c.primary_account not in c.direct:
            c.attributed[c.primary_account].add("primary")


# ----------------------------------------------------------------------------- 2. batch cadence
def cadence_slot_times(A: int, R: datetime, idxs) -> list[datetime]:
    return [R - timedelta(seconds=CADENCE_BASE_S + (A + CADENCE_STEP_S * i) % CADENCE_PERIOD_S) for i in idxs]


def fit_batch_offset(info: list) -> int | None:
    """Best per-batch cadence offset A (most cases within tolerance, then least total error)."""
    def score(A):
        hit, err = 0, 0.0
        for _c, R, idxs, cands in info:
            best = min((abs((a["_t"] - t).total_seconds()) for a in cands for t in cadence_slot_times(A, R, idxs)),
                       default=None)
            if best is not None and best <= CADENCE_TOL_S:
                hit += 1
                err += best
        return hit, -err

    best_A = max(range(0, CADENCE_PERIOD_S, CADENCE_SEARCH_STEP_S), key=score)
    if score(best_A)[0] < max(CADENCE_MIN_BATCH, len(info) // 2):
        return None
    return best_A


def refine_accounts_by_batch_cadence(data: AuditData, all_cases: list[CaseContext]):
    """Re-solve the timing-attributed primary profiles of every reporting group with the batch
    cadence as cost.  Identifier-linked and explicitly reported accounts stay fixed."""
    prim = [a for a in data.activities if is_primary_submission(a)]
    cases_all = [c for c in all_cases if c.auditable]
    owned_direct = {c.primary_account for c in cases_all if c.primary_account and c.primary_account in c.direct}

    def window(c):
        R = ts(c.req["reported_at"])
        return [a for a in prim if a["reported_result"] == expected_result(c) and in_window(a["_t"], R, PRIMARY_WINDOW_MIN)
                and (a["resource_id"] not in owned_direct or a["resource_id"] == c.primary_account)]

    # 1) learn the cadence of every audit batch (same audit_at and same main evidence file)
    batches = defaultdict(list)
    for c in cases_all:
        be = data.batch_evidence(c)
        batches[(c.audit_at, be[0] if be else "")].append(c)
    expected: dict[str, list] = {}
    for cases in batches.values():
        if len(cases) < CADENCE_MIN_BATCH:
            continue
        t0 = max(ts(c.requested_at) for c in cases)
        info = []
        for c in cases:
            idx = round((t0 - ts(c.requested_at)).total_seconds() / 86400)
            info.append((c, ts(c.req["reported_at"]), (idx, idx + CADENCE_TRAILING_SHIFT), window(c)))
        best_A = fit_batch_offset(info)
        if best_A is None:
            continue
        for c, R, idxs, _cands in info:
            expected[c.case_id] = cadence_slot_times(best_A, R, idxs)

    # 2) re-solve every reporting group jointly (greedy on ascending cost = optimal here because
    #    in-tolerance distances are far apart)
    groups = defaultdict(list)
    for c in cases_all:
        groups[c.req["reported_at"]].append(c)

    def fixed(c):
        a = c.primary_account
        return bool(a) and (a in c.direct or any(w.startswith("report:") for w in c.attributed.get(a, ())))

    for cases in groups.values():
        free_cases = [c for c in cases if not fixed(c)]
        if not free_cases:
            continue
        for c in free_cases:
            old = c.primary_account
            if old and old not in c.direct:
                c.attributed.pop(old, None)
                c.primary_account = None
        pairs = []
        for c in free_cases:
            exp = expected.get(c.case_id)
            for order, a in enumerate(sorted(window(c), key=lambda a: a["_t"])):
                if exp:
                    d = min(abs((a["_t"] - t).total_seconds()) for t in exp)
                    cost = d if d <= CADENCE_TOL_S else 10_000 + d
                else:
                    cost = 20_000 + order * 100 + rid_num(c.case_id) / 1e6
                pairs.append((cost, rid_num(c.case_id), c, a))
        pairs.sort(key=lambda x: (x[0], x[1]))
        used_c, used_a = set(), set()
        for cost, _, c, a in pairs:
            if id(c) in used_c or a["resource_id"] in used_a:
                continue
            used_c.add(id(c))
            used_a.add(a["resource_id"])
            c.primary_account = a["resource_id"]
            c.attributed[a["resource_id"]].add(("cadence:" if cost < 10_000 else "timing:") + a["activity_id"])
        for c in free_cases:
            if c.primary_account is None:
                c.primary_account = last_resort_account(data, c)


# ----------------------------------------------------------------------------- 3. policy actions
def circular_distance(a: float, b: float, period: float = 60) -> float:
    d = (a - b) % period
    return min(d, period - d)


def case_phase(data: AuditData, c: CaseContext) -> int | None:
    """Seconds-of-minute of the case's primary-profile submission."""
    acct = c.primary_account
    if not acct:
        return None
    for a in data.activities:
        if a["resource_id"] == acct and "submitted primary profile" in a["details"]:
            return a["_t"].second
    return None


def has_preliminary_violation(data: AuditData, c: CaseContext, features: Features) -> bool:
    """Quick look: does any identifier-linked record still violate policy?  Used only to order
    the elimination fallback (compliant-looking cases first)."""
    for rid in c.direct:
        fresh = [o for o in data.by_resource.get(rid, []) + c.doc_obs
                 if o.resource_id == rid and c.requested_at <= o.time <= c.audit_at
                 and not data.foreign(c.subject, o)]
        if not fresh:
            continue
        last = max(fresh, key=lambda o: o.time)
        cat = resolve_category(data, rid, system_for(rid)[1], fresh, c.audit_at, features.worker_hint)
        st = effective_state(cat, last)
        if st in ("IDENTIFIABLE", "ACTIVE") or (st == "PRESENT" and cat not in RETAINED_CATEGORIES):
            return True
    return False


def attribute_policy_actions(data: AuditData, R: datetime, cases: list[CaseContext],
                             all_cases: list[CaseContext], features: Features):
    """Attribute identifier-free "Policy action completed" events (ANONYMIZE / RETAIN) of one
    reporting group to cases by their timing phase; leftovers by elimination."""
    acts = [a for a in data.activities
            if "Policy action completed" in a["details"] and in_window(a["_t"], R, POLICY_ACTION_WINDOW_MIN)]
    if not acts:
        return
    free = [a for a in acts if a["event_type"] in ("ANONYMIZE_RECORD", "RETAIN_RECORD")
            and not owned_by_identifier(a["resource_id"], all_cases)]
    phases = {}
    for c in cases:
        ph = case_phase(data, c)
        if ph is None:
            # fall back to the phase implied by the case's own identifier-bearing policy actions
            for a in acts:
                if a["resource_id"] in c.direct and a["event_type"] in POLICY_OPERATION_OFFSET_S:
                    off = POLICY_PHASE_OFFSET_S if a["target_system"] in ("identity_service", "legal_service") \
                        else POLICY_OPERATION_OFFSET_S[a["event_type"]]
                    ph = (a["_t"].second - off) % 60
                    break
        phases[c.case_id] = ph

    def has_linked_analytics(c):
        return any(r.startswith("analytics_") for r in c.direct)

    pairs = []
    for c in cases:
        ph = phases[c.case_id]
        if ph is None:
            continue
        # policy actions occupy consecutive slots (legal hold first); each slot advances the
        # seconds phase by POLICY_SLOT_ADVANCE_S
        has_hold = any(a["event_type"] == "RETAIN_RECORD" and a["resource_id"] in c.direct for a in acts)
        for a in free:
            if a["event_type"] == "ANONYMIZE_RECORD" and has_linked_analytics(c):
                continue
            k = 1 if (a["event_type"] == "ANONYMIZE_RECORD" and has_hold) else 0
            d = circular_distance(a["_t"].second, (ph + POLICY_PHASE_OFFSET_S + POLICY_SLOT_ADVANCE_S * k) % 60)
            if d <= POLICY_PHASE_TOL_S:
                pairs.append((d, c, a))
    pairs.sort(key=lambda x: (x[0], rid_num(x[1].case_id)))
    used_slots, used_a = set(), set()
    for _d, c, a in pairs:
        key = (id(c), a["event_type"])
        if key in used_slots or a["resource_id"] in used_a:
            continue
        used_slots.add(key)
        used_a.add(a["resource_id"])
        c.attributed[a["resource_id"]].add("policy-phase:" + a["activity_id"])
        c.policy_claims[a["resource_id"]] = a
    used_c = {k[0] for k in used_slots}

    # leftovers: elimination among cases without a known phase, compliant-looking cases first
    rest = sorted([a for a in free if a["resource_id"] not in used_a and a["event_type"] == "ANONYMIZE_RECORD"],
                  key=lambda a: a["_t"])
    need = [c for c in cases if id(c) not in used_c and phases[c.case_id] is None and not has_linked_analytics(c)]
    need.sort(key=lambda c: (has_preliminary_violation(data, c, features), rid_num(c.case_id)))
    for a in rest:
        for c in need:
            if any(o.time <= c.audit_at for o in data.by_resource.get(a["resource_id"], [])):
                c.attributed[a["resource_id"]].add("policy-order:" + a["activity_id"])
                need.remove(c)
                break
