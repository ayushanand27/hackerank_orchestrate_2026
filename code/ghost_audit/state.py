"""Point-in-time state of every linked resource at ``audit_at``.

Rules (all deterministic):

* only observations dated at or before ``audit_at`` count; observations of another known
  subject (and none of ours) are ignored;
* the audit concerns the state *after* the request, so only observations from
  ``requested_at`` on count -- except for retained records, which persist anyway;
* a complete snapshot from the case's own evidence batch that does not list a resource proves
  it absent ("omission");
* the latest observation wins; its evidence is the latest in-time file showing the same state
  (files captured after ``audit_at`` are never cited when an in-time one exists);
* workflow results are claims, never states -- except a retained legal hold confirmed by a
  "Policy action completed" event attributed to the case.
"""
from __future__ import annotations

from .config import (ANONYMIZE_LIKE_CATEGORIES, DELETE_CATEGORIES, PROCESS_CATEGORIES,
                     REDACT_LIKE_CATEGORIES, RETAINED_CATEGORIES, Features)
from .context import AuditData, CaseContext
from .evidence import Observation, system_for
from .policy import required_action

SNAPSHOT_KINDS = ("snapshot", "sql", "service_export")
EXPLICIT_KINDS = ("lookup", "handover")


def effective_state(category: str | None, o: Observation) -> str | None:
    """Map an observation's raw state to the state that matters for its category's policy."""
    st = o.state
    has_direct = any(o.pii.get(k) for k in ("email", "phone", "email_hash", "name"))
    has_ref = bool(o.pii.get("user_ref"))
    if st in ("ABSENT", "ACTIVE", "DISABLED", "BLOCKED"):
        return st
    if category in DELETE_CATEGORIES:
        return "PRESENT"          # any surviving copy of a delete-category record is "present"
    if category in REDACT_LIKE_CATEGORIES:
        if st == "IDENTIFIABLE":
            return "IDENTIFIABLE"
        if st in ("REDACTED", "ANONYMIZED"):
            return "REDACTED"
        if o.kind in SNAPSHOT_KINDS:
            return "IDENTIFIABLE" if has_direct else "REDACTED"
        return "IDENTIFIABLE" if has_direct else st
    if category in ANONYMIZE_LIKE_CATEGORIES:
        if st in ("IDENTIFIABLE", "ANONYMIZED"):
            return st
        if o.kind in SNAPSHOT_KINDS:
            # a stable user reference still links the record to the person
            return "IDENTIFIABLE" if (has_direct or has_ref) else "ANONYMIZED"
        return st
    return st


def activity_category_hint(data: AuditData, rid: str, audit_at: str) -> str | None:
    """The deletion worker's chosen operation reveals how policy classifies the record."""
    for a in data.activities:
        if a["resource_id"] != rid or a["event_time"] > audit_at:
            continue
        system, op = a["target_system"], a["event_type"]
        if system == "support_platform" and op == "DELETE_RECORD":
            return "SUPPORT_ATTACHMENT"
        if system == "support_platform" and op == "REDACT_RECORD":
            return "SUPPORT_MESSAGE"
        if system == "analytics_platform" and op == "DELETE_RECORD":
            return "SECONDARY_IDENTIFIER"
        if system == "analytics_platform" and op == "ANONYMIZE_RECORD":
            return "ANALYTICS_PROFILE"
    return None


def resolve_category(data: AuditData, rid: str, default_cat: str | None, cands: list[Observation],
                     audit_at: str, worker_hint: bool = True) -> str | None:
    """Category precedence: hashed contact lookup > explicit record_type of a lookup ledger >
    worker operation hint > collection/event-specific category > resource-id prefix default."""
    for o in cands:
        if o.table == "contact_lookup_completed":
            return "SECONDARY_IDENTIFIER"
    explicit = [o.category for o in cands if o.kind in EXPLICIT_KINDS and o.category]
    if explicit:
        return explicit[-1]
    hint = activity_category_hint(data, rid, audit_at) if worker_hint else None
    if hint:
        return hint
    specific = [o.category for o in cands if o.category and o.category != default_cat]
    if specific:
        return specific[-1]
    return default_cat


def omission_absences(data: AuditData, rid: str, system: str, start: str, end: str,
                      files: set) -> list[Observation]:
    """Complete snapshots of ``system`` taken in [start, end] that do not list ``rid``.  Only the
    case's own evidence batch counts as complete for it (other batches list other people)."""
    out = []
    for cv in data.coverage:
        if cv.evidence_id in files and cv.system == system and start <= cv.time <= end \
                and rid not in cv.resource_ids:
            o = Observation(evidence_id=cv.evidence_id, file_time=cv.file_time, time=cv.time,
                            resource_id=rid, system=system, state="ABSENT", kind="omission")
            out.append(o.finish())
    return out


def snapshot_absence_evidence(data: AuditData, rid: str, audit_at: str) -> str:
    """Latest SQL account snapshot captured by ``audit_at`` that does not contain ``rid``."""
    best = None
    for o in data.observations:
        if o.kind == "sql" and o.category == "USER_PROFILE" and o.file_time <= audit_at:
            if best is None or o.file_time > best[0]:
                best = (o.file_time, o.evidence_id)
    if best and not any(o.evidence_id == best[1] for o in data.by_resource.get(rid, [])):
        return best[1]
    return ""


def linked_resources(c: CaseContext) -> dict:
    """rid -> reasons, identifier links first, then timing attributions (stable order)."""
    linked: dict = {}
    for src in (c.direct, c.attributed):
        for k, v in src.items():
            linked.setdefault(k, set()).update(v)
    return linked


def build_views(data: AuditData, c: CaseContext, features: Features) -> dict:
    """rid -> {system, category, state, evidence} for every resource linked to the case."""
    subj, audit_at = c.subject, c.audit_at
    views = {}
    batch_files = data.batch_evidence(c)
    for rid in linked_resources(c):
        system, cat = system_for(rid)
        pool = data.by_resource.get(rid, []) + [o for o in c.doc_obs if o.resource_id == rid]
        cands = [o for o in pool if o.time <= audit_at and not data.foreign(subj, o)]
        for o in cands:
            if o.system:
                system = o.system
        cat = resolve_category(data, rid, cat, cands, audit_at, features.worker_hint)
        if features.omission and required_action(data.rules, cat) != "RETAIN" and cat not in PROCESS_CATEGORIES:
            cands = cands + omission_absences(data, rid, system, c.requested_at, audit_at, set(batch_files))
        fresh = [o for o in cands
                 if not features.post_request_only or o.time >= c.requested_at or cat in RETAINED_CATEGORIES]
        state, ev = None, ""
        if fresh:
            last = max(fresh, key=lambda o: (o.time, o.file_time <= audit_at, o.file_time))
            state = effective_state(cat, last)
            same = [o for o in fresh if effective_state(cat, o) == state]
            cited = [o for o in same if o.file_time <= audit_at] or same
            ev = max(cited, key=lambda o: (o.time, o.file_time)).evidence_id
        elif rid in c.policy_claims and cat == "LEGAL_HOLD_RECORD":
            state, ev = "PRESENT", c.policy_claims[rid]["activity_id"]
        elif rid == c.primary_account:
            # the primary profile is deleted by the workflow and no copy was observed afterwards
            state = "ABSENT"
            ev = batch_files[0] if batch_files else snapshot_absence_evidence(data, rid, audit_at)
        if cat == "USER_PROFILE" and rid != c.primary_account and state != "PRESENT":
            continue  # stale account ids from other episodes are only interesting if still live
        views[rid] = dict(system=system, category=cat, state=state, evidence=ev)
    return views
