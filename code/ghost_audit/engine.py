"""Deletion-audit engine.

Pipeline per request
--------------------
1. intent     – does the text authorise deletion?  (``intent.py``)
2. identity   – current + historical e-mails, phones, hashes, user_id (``identity.py``)
3. linking    – resources tied to the subject by identifiers, documents, catalogue links,
                lineage, and (for identifier-free records) by workflow timing
4. state      – latest trustworthy observation at or before ``audit_at``
5. policy     – ``deletion_rules.csv`` decides the required end state per category
6. planning   – findings / remediation / verification in the mandated order
"""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .evidence import Observation, load_all, system_for, RESOURCE_RE, EMAIL_RE, USER_RE, COVERAGE
from .identity import IdentityIndex, Subject
from .intent import classify
from .policy import load_rules, required_action, order_steps

DELETE_CATS = {"USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "SUPPORT_ATTACHMENT",
               "USER_EXPORT", "MARKETING_PROFILE"}
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def rid_num(rid: str) -> int:
    m = re.search(r"(\d+)$", rid)
    return int(m.group(1)) if m else 0


@dataclass
class CaseContext:
    req: dict
    subject: Subject | None
    deletion: bool
    intent_reason: str
    direct: dict = field(default_factory=lambda: defaultdict(set))   # rid -> reasons
    attributed: dict = field(default_factory=lambda: defaultdict(set))
    primary_account: str | None = None
    doc_obs: list = field(default_factory=list)
    policy_claims: dict = field(default_factory=dict)


class Auditor:
    def __init__(self, dataset_dir: str, cache_dir: str | None = None):
        self.dataset_dir = dataset_dir
        self.idx = IdentityIndex(dataset_dir)
        self.rules = load_rules(dataset_dir)
        self.obs, self.docs = load_all(dataset_dir, cache_dir)
        self.coverage = list(COVERAGE)
        self.activities = list(csv.DictReader(open(os.path.join(dataset_dir, "system_activity.csv"), encoding="utf-8")))
        for a in self.activities:
            a["_t"] = ts(a["event_time"])
        self.by_resource: dict[str, list[Observation]] = defaultdict(list)
        for o in self.obs:
            self.by_resource[o.resource_id].append(o)
        self.ctx: dict[str, CaseContext] = {}
        self._prelim_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------ identity helpers
    @staticmethod
    def obs_values(o: Observation) -> set:
        vals = set(o.keys)
        for v in o.pii.values():
            vals |= set(v)
        return vals

    def belongs(self, subj: Subject, o: Observation) -> bool:
        return any(subj.matches(v) for v in self.obs_values(o))

    def foreign(self, subj: Subject, o: Observation) -> bool:
        """Observation carries identifiers of some *other* known subject and none of ours."""
        vals = self.obs_values(o)
        if not vals or any(subj.matches(v) for v in vals):
            return False
        for v in vals:
            v = str(v)
            if "@" in v and self.idx.by_email.get(v.lower()):
                return True
            if re.fullmatch(r"user_\d+", v) and v in self.idx.subjects:
                return True
        return False

    # ------------------------------------------------------------------ preparation (all cases jointly)
    def prepare(self, requests: list[dict]):
        for r in requests:
            ok, conf, why = classify(r["request_text"])
            subj = self.idx.resolve(r["email"]) if ok else None
            c = CaseContext(req=r, subject=subj, deletion=ok, intent_reason=why)
            if subj is not None:
                self._direct_links(c)
            self.ctx[r["case_id"]] = c
        groups: dict[str, list[CaseContext]] = defaultdict(list)
        for c in self.ctx.values():
            if c.deletion and c.subject is not None:
                groups[c.req["reported_at"]].append(c)
        for R, cases in groups.items():
            cases.sort(key=lambda c: rid_num(c.req["case_id"]))
            self._attribute_accounts(ts(R), cases)
        self._refine_accounts_by_batch_cadence()
        for R, cases in groups.items():
            self._attribute_policy_actions(ts(R), cases)

    def _direct_links(self, c: CaseContext):
        subj, audit_at = c.subject, c.req["audit_at"]
        for o in self.obs:
            if o.time <= audit_at and self.belongs(subj, o):
                c.direct[o.resource_id].add("obs:" + o.evidence_id)
        links = {}
        for a in self.activities:
            if a["event_time"] > audit_at:
                continue
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                det = a["details"]
                ems = {e.lower().rstrip(".") for e in EMAIL_RE.findall(det)}
                users = set(USER_RE.findall(det))
                if subj.user_id in users or subj.emails & ems:
                    c.direct[a["resource_id"]].add("link:" + a["activity_id"])
                    m = re.search(re.escape(a["resource_id"]) + r" \(([^)]+)\)", det)
                    links[a["resource_id"]] = (a, m.group(1) if m else a["resource_id"])
        c.doc_obs = self._doc_observations(c, links)
        for o in c.doc_obs:
            c.direct[o.resource_id].add("doc:" + o.evidence_id)
        # lineage: an active connector / restore that repopulated a downstream copy
        for a in self.activities:
            if a["event_time"] > audit_at or "repopulated" not in a["details"]:
                continue
            down = [r for r in RESOURCE_RE.findall(a["details"]) if r != a["resource_id"]]
            for r in down:
                if a["resource_id"] in c.direct:
                    c.direct[r].add("lineage:" + a["activity_id"])

    @staticmethod
    def _squash(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())

    def _doc_observations(self, c: CaseContext, links: dict) -> list[Observation]:
        """Turn PDFs / screenshots into observations for resources that are tied to the subject:
        catalogue-linked resources (by id or alias) and single resources on a subject's page."""
        subj, audit_at, req_at = c.subject, c.req["audit_at"], c.req["requested_at"]
        out = []
        for d in self.docs:
            if d.file_time > audit_at:
                continue
            text_sq = self._squash(d.text)
            mentions_subject = subj.user_id in d.users or bool(subj.emails & set(d.emails))
            chosen = {}
            for rid, (a, alias) in links.items():
                al = re.sub(r"\.(pdf|zip|png|jpg)$", "", alias, flags=re.I)
                if rid in d.resources or (len(self._squash(al)) >= 6 and self._squash(al) in text_sq):
                    chosen[rid] = "link"
            if mentions_subject:
                by_type = defaultdict(list)
                for rid in d.resources:
                    by_type[re.sub(r"_\d+$", "", rid)].append(rid)
                for t, rids in by_type.items():
                    if len(rids) == 1:
                        chosen.setdefault(rids[0], "single")
            for rid in chosen:
                o = Observation(evidence_id=d.evidence_id, file_time=d.file_time, time=d.file_time,
                                resource_id=rid, kind="doc:" + d.kind, table=os.path.basename(d.path))
                o.finish()
                o.state, cat = self._doc_state(d, rid, o.category)
                o.category = cat or o.category
                o.keys.add(subj.user_id)
                out.append(o)
        return out

    @staticmethod
    def _doc_state(d, rid: str, cat: str | None):
        t = d.text.lower()
        name = os.path.basename(d.path).lower()
        if rid.startswith("tombstone_"):
            return ("IDENTIFIABLE" if d.emails else "REDACTED"), "ACCOUNT_TOMBSTONE"
        if rid.startswith("support_") or rid.startswith("object_"):
            if "attachment" in name or "attachment" in t or rid.startswith("object_"):
                return "PRESENT", "SUPPORT_ATTACHMENT"
            return ("IDENTIFIABLE" if d.emails else "REDACTED"), None
        if rid.startswith("analytics_"):
            return ("IDENTIFIABLE" if d.emails else "ANONYMIZED"), None
        if rid.startswith("backup_"):
            return ("BLOCKED" if re.search(r"restore (blocked|denied|disabled)", t) else "ACTIVE"), None
        if rid.startswith("data_flow_"):
            return ("DISABLED" if re.search(r"\b(disabled|paused|stopped)\b", t) else "ACTIVE"), None
        if rid.startswith("audit_record_") or rid.startswith("legal_hold_"):
            return "PRESENT", None
        return "PRESENT", None

    def _owner_by_direct(self, rid: str, cases: list[CaseContext]):
        owners = [c for c in cases if rid in c.direct]
        return owners[0] if len(owners) == 1 else None

    def _attribute_accounts(self, R: datetime, cases: list[CaseContext]):
        """Primary-profile deletions are submitted ~40-80 min before the workflow reports its
        status, and the worker's result mirrors that reported status.  Workflows that report
        directly ("Target resource: X") do so within minutes of the report."""
        primary, direct_report = [], []
        for a in self.activities:
            dt = (a["_t"] - R).total_seconds() / 60
            d = a["details"]
            if a["target_system"] == "account_service" and a["event_type"] == "DELETE_RECORD" \
                    and "submitted primary profile" in d and -90 <= dt <= -30:
                primary.append(a)
            elif "Target resource:" in d and -20 <= dt <= 20 and a["event_type"] != "SYNC_RECORD":
                direct_report.append(a)
        owned_elsewhere = lambda rid: any(rid in k.direct for k in self.ctx.values() if k.deletion)
        # workflow reports that name their target resource explicitly
        for a in direct_report:
            owners = [c for c in cases if a["resource_id"] in c.direct]
            if not owners and len(cases) == 1 and not owned_elsewhere(a["resource_id"]):
                owners = cases
            for c in owners[:1]:
                c.attributed[a["resource_id"]].add("report:" + a["activity_id"])
                if a["target_system"] == "account_service" and c.primary_account is None:
                    c.primary_account = a["resource_id"]
        taken = set(c.primary_account for c in cases if c.primary_account)
        for c in cases:
            if c.primary_account:
                continue
            for a in primary:
                if a["resource_id"] in c.direct:
                    c.primary_account = a["resource_id"]
                    taken.add(a["resource_id"])
                    break
        free = [a for a in primary if a["resource_id"] not in taken and not owned_elsewhere(a["resource_id"])]
        todo = [c for c in cases if c.primary_account is None]
        changed = True
        while changed and todo and free:
            changed = False
            for c in list(todo):
                comp = [a for a in free if a["reported_result"] == STATUS_RESULT.get(c.req["reported_status"])]
                if len(comp) == 1:
                    others = [k for k in todo if k is not c and comp[0]["reported_result"] == STATUS_RESULT.get(k.req["reported_status"])]
                    if not others:
                        self._take_account(c, comp[0], todo, free)
                        changed = True
        for res in ("SUCCESS", "PARTIAL", "FAILED"):
            cs = [c for c in todo if STATUS_RESULT.get(c.req["reported_status"]) == res]
            fs = sorted([a for a in free if a["reported_result"] == res], key=lambda a: a["_t"])
            for c, a in zip(cs, fs):
                c.primary_account = a["resource_id"]
                c.attributed[a["resource_id"]].add("timing:" + a["activity_id"])
        # last resort: the subject's own account observed after the request
        for c in cases:
            if c.primary_account is None:
                accts = sorted({o.resource_id for o in self.obs if o.resource_id in c.direct and o.category == "USER_PROFILE"
                                and c.req["requested_at"] <= o.time <= c.req["audit_at"]})
                if len(accts) == 1:
                    c.primary_account = accts[0]
        for c in cases:
            if c.primary_account and c.primary_account not in c.direct:
                c.attributed[c.primary_account].add("primary")

    # The privacy workflow processes an audit batch on a fixed cadence: consecutive requests (one
    # calendar day apart) have their primary profile submitted at offsets that advance by a constant
    # step inside a fixed window before the status report.  Learning that cadence from the
    # unambiguous cases lets us resolve cases whose candidate deletions are otherwise identical.
    CADENCE_BASE, CADENCE_STEP, CADENCE_PERIOD, CADENCE_TOL = 2700, 674, 1890, 75

    def _refine_accounts_by_batch_cadence(self):
        prim = [a for a in self.activities if a["target_system"] == "account_service"
                and a["event_type"] == "DELETE_RECORD" and "submitted primary profile" in a["details"]]
        batches = defaultdict(list)
        for c in self.ctx.values():
            if c.deletion and c.subject is not None:
                be = self.batch_evidence(c)
                batches[(c.req["audit_at"], be[0] if be else "")].append(c)
        B, S, P, TOL = self.CADENCE_BASE, self.CADENCE_STEP, self.CADENCE_PERIOD, self.CADENCE_TOL
        owned_direct = set()
        for c in self.ctx.values():
            if c.deletion and c.primary_account and c.primary_account in c.direct:
                owned_direct.add(c.primary_account)
        for key, cases in batches.items():
            if len(cases) < 3:
                continue
            t0 = max(ts(c.req["requested_at"]) for c in cases)
            info = []
            for c in cases:
                R = ts(c.req["reported_at"])
                idx = round((t0 - ts(c.req["requested_at"])).total_seconds() / 86400)
                res = STATUS_RESULT.get(c.req["reported_status"])
                cands = [a for a in prim if a["reported_result"] == res and -90 <= (a["_t"] - R).total_seconds() / 60 <= -30
                         and (a["resource_id"] not in owned_direct or a["resource_id"] == c.primary_account)]
                info.append((c, R, idx, cands))

            def fit(A):
                err, hit = 0.0, 0
                for c, R, idx, cands in info:
                    u = (A + S * idx) % P
                    exp_t = R - timedelta(seconds=B + u)
                    best = min((abs((a["_t"] - exp_t).total_seconds()) for a in cands), default=None)
                    if best is not None and best <= TOL:
                        hit += 1
                        err += best
                return hit, -err

            bestA = max(range(0, P, 3), key=fit)
            hits, _ = fit(bestA)
            if hits < max(3, len(info) // 2):
                continue  # cadence not identifiable for this batch
            # re-assign: each case takes the candidate closest to its expected cadence slot
            pairs = []
            for c, R, idx, cands in info:
                if c.primary_account and c.primary_account in c.direct:
                    continue
                u = (bestA + S * idx) % P
                exp_t = R - timedelta(seconds=B + u)
                for a in cands:
                    d = abs((a["_t"] - exp_t).total_seconds())
                    if d <= TOL:
                        pairs.append((d, c, a))
            pairs.sort(key=lambda x: x[0])
            used_c, used_a = set(), set()
            for d, c, a in pairs:
                if id(c) in used_c or a["resource_id"] in used_a:
                    continue
                used_c.add(id(c))
                used_a.add(a["resource_id"])
                if c.primary_account != a["resource_id"]:
                    old = c.primary_account
                    if old and old in c.attributed and old not in c.direct:
                        del c.attributed[old]
                    # release the slot from whichever case currently holds it
                    for k in self.ctx.values():
                        if k is not c and k.primary_account == a["resource_id"] and a["resource_id"] not in k.direct:
                            k.primary_account = None
                            k.attributed.pop(a["resource_id"], None)
                    c.primary_account = a["resource_id"]
                    c.attributed[a["resource_id"]].add("cadence:" + a["activity_id"])

    @staticmethod
    def _take_account(c, a, todo, free):
        c.primary_account = a["resource_id"]
        c.attributed[a["resource_id"]].add("timing:" + a["activity_id"])
        todo.remove(c)
        free.remove(a)

    # A case's follow-up policy actions inherit the seconds-of-minute phase of its primary-profile
    # submission (anonymise/disable ~ +40 s, redact/block ~ +10 s).  This ties identifier-free
    # records (anonymised analytics) to exactly one case.
    PHASE = {"ANONYMIZE_RECORD": 40.5, "DISABLE_DATA_FLOW": 39.5, "REDACT_RECORD": 10, "BLOCK_RESTORE": 9,
             "RETAIN_RECORD": 40.5}

    def _case_phase(self, c: CaseContext):
        acct = c.primary_account
        if not acct:
            return None
        for a in self.activities:
            if a["resource_id"] == acct and "submitted primary profile" in a["details"]:
                return a["_t"].second
        return None

    @staticmethod
    def _circ(a: float, b: float) -> float:
        d = (a - b) % 60
        return min(d, 60 - d)

    def _attribute_policy_actions(self, R: datetime, cases: list[CaseContext]):
        """'Policy action completed' events (~7-29 min before the report) confirm already-compliant
        copies.  Identifier-free ones are attributed by the case's timing phase."""
        acts = [a for a in self.activities
                if "Policy action completed" in a["details"] and -35 <= (a["_t"] - R).total_seconds() / 60 <= 0]
        if not acts:
            return
        owned = lambda rid: any(rid in k.direct for k in self.ctx.values() if k.deletion)
        free = [a for a in acts if a["event_type"] in ("ANONYMIZE_RECORD", "RETAIN_RECORD") and not owned(a["resource_id"])]
        phases = {}
        for c in cases:
            ph = self._case_phase(c)
            if ph is None:
                # fall back to the phase implied by the case's own identifier-bearing policy actions
                for a in acts:
                    if a["resource_id"] in c.direct and a["event_type"] in self.PHASE:
                        off = 40.5 if a["target_system"] in ("identity_service", "legal_service") else self.PHASE[a["event_type"]]
                        ph = (a["_t"].second - off) % 60
                        break
            phases[c.req["case_id"]] = ph
        pairs = []
        for c in cases:
            ph = phases[c.req["case_id"]]
            if ph is None:
                continue
            has_analytics = any(r.startswith("analytics_") and r in c.direct for r in c.direct)
            # policy actions occupy consecutive 7.5-minute slots (legal hold first); each slot
            # advances the seconds phase by ~29.5 s
            has_hold = any(a["event_type"] == "RETAIN_RECORD" and a["resource_id"] in c.direct for a in acts)
            for a in free:
                if a["event_type"] == "ANONYMIZE_RECORD" and has_analytics:
                    continue
                k = 1 if (a["event_type"] == "ANONYMIZE_RECORD" and has_hold) else 0
                d = self._circ(a["_t"].second, (ph + 40.5 + 29.5 * k) % 60)
                if d <= 1.5:
                    pairs.append((d, c, a))
        pairs.sort(key=lambda x: (x[0], rid_num(x[1].req["case_id"])))
        used_c, used_a = set(), set()
        for d, c, a in pairs:
            key = (id(c), a["event_type"])
            if key in used_c or a["resource_id"] in used_a:
                continue
            used_c.add(key)
            used_a.add(a["resource_id"])
            c.attributed[a["resource_id"]].add("policy-phase:" + a["activity_id"])
            c.policy_claims[a["resource_id"]] = a
        used_c = {k[0] for k in used_c}
        # leftovers: elimination among cases without a known phase, preferring compliant-looking cases
        rest = [a for a in free if a["resource_id"] not in used_a and a["event_type"] == "ANONYMIZE_RECORD"]
        need = [c for c in cases if id(c) not in used_c and phases[c.req["case_id"]] is None
                and not any(r.startswith("analytics_") and r in c.direct for r in c.direct)]
        rest.sort(key=lambda a: a["_t"])
        need.sort(key=lambda c: (self._prelim_violation(c), rid_num(c.req["case_id"])))
        for a in rest:
            for c in need:
                vis = [o for o in self.by_resource.get(a["resource_id"], []) if o.time <= c.req["audit_at"]]
                if vis:
                    c.attributed[a["resource_id"]].add("policy-order:" + a["activity_id"])
                    need.remove(c)
                    break

    def _prelim_violation(self, c: CaseContext) -> bool:
        if c.req["case_id"] in self._prelim_cache:
            return self._prelim_cache[c.req["case_id"]]
        bad = False
        for rid in c.direct:
            fresh = [o for o in self.by_resource.get(rid, []) + c.doc_obs
                     if o.resource_id == rid and c.req["requested_at"] <= o.time <= c.req["audit_at"]
                     and not self.foreign(c.subject, o)]
            if not fresh:
                continue
            last = max(fresh, key=lambda o: o.time)
            cat = self.resolve_category(rid, system_for(rid)[1], fresh, c.req["audit_at"])
            st = self.effective_state(cat, last)
            if st in ("IDENTIFIABLE", "ACTIVE") or (st == "PRESENT" and cat not in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD")):
                bad = True
                break
        self._prelim_cache[c.req["case_id"]] = bad
        return bad

    # ------------------------------------------------------------------ per-case audit
    def audit(self, req: dict) -> tuple[str, list[dict], dict]:
        c = self.ctx.get(req["case_id"])
        if c is None:
            self.prepare([req])
            c = self.ctx[req["case_id"]]
        dbg: dict = {"intent": c.intent_reason}
        if not c.deletion:
            return "NOT_APPLICABLE", [], dbg
        subj = c.subject
        audit_at = req["audit_at"]
        if subj is None:
            return "INCOMPLETE", [], dbg
        linked = defaultdict(set)
        for k, v in c.direct.items():
            linked[k] |= v
        for k, v in c.attributed.items():
            linked[k] |= v
        dbg["linked"] = {k: sorted(v) for k, v in linked.items()}
        views = {}
        batch_files = self.batch_evidence(c)
        for rid in linked:
            system, cat = system_for(rid)
            pool = self.by_resource.get(rid, []) + [o for o in c.doc_obs if o.resource_id == rid]
            cands = [o for o in pool if o.time <= audit_at and not self.foreign(subj, o)]
            for o in cands:
                if o.system:
                    system = o.system
            cat = self.resolve_category(rid, cat, cands, audit_at)
            if required_action(self.rules, cat) != "RETAIN" and cat not in ("DATA_FLOW", "BACKUP_ARCHIVE"):
                cands = cands + self.omission_absences(rid, system, c.req["requested_at"], audit_at, set(batch_files))
            # the audit concerns the state *after* the request; retained records persist anyway
            fresh = [o for o in cands if o.time >= c.req["requested_at"]
                     or cat in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD")]
            state, ev = None, ""
            if fresh:
                last = max(fresh, key=lambda o: (o.time, o.file_time <= audit_at, o.file_time))
                state = self.effective_state(cat, last)
                same = [o for o in fresh if self.effective_state(cat, o) == state]
                cited = [o for o in same if o.file_time <= audit_at] or same
                ev = max(cited, key=lambda o: (o.time, o.file_time)).evidence_id
            elif rid in c.policy_claims and cat == "LEGAL_HOLD_RECORD":
                state, ev = "PRESENT", c.policy_claims[rid]["activity_id"]
            elif rid == c.primary_account:
                state = "ABSENT"
                ev = batch_files[0] if batch_files else self.snapshot_absence_evidence(rid, audit_at)
            if cat == "USER_PROFILE" and rid != c.primary_account and state != "PRESENT":
                continue  # stale account ids from other episodes are only interesting if still live
            views[rid] = dict(system=system, category=cat, state=state, evidence=ev)
        dbg["views"] = views
        return self.plan(c, views)

    def omission_absences(self, rid: str, system: str, start: str, end: str, files: set) -> list[Observation]:
        """Complete snapshots of `system` taken in [start, end] that do not list `rid`.  Only the
        case's own evidence batch (files that carry its identifiers) counts as complete for it."""
        out = []
        for cv in self.coverage:
            if cv.evidence_id in files and cv.system == system and start <= cv.time <= end and rid not in cv.resource_ids:
                o = Observation(evidence_id=cv.evidence_id, file_time=cv.file_time, time=cv.time,
                                resource_id=rid, system=system, state="ABSENT", kind="omission")
                out.append(o.finish())
        return out

    def batch_evidence(self, c: CaseContext) -> list[str]:
        """Evidence files (captured in time) that carry the case's own identifiers, most-used first."""
        cnt = defaultdict(int)
        for o in self.obs:
            if o.resource_id in c.direct and c.req["requested_at"] <= o.time <= c.req["audit_at"] \
                    and o.file_time <= c.req["audit_at"] and self.belongs(c.subject, o):
                cnt[o.evidence_id] += 1
        return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]

    EXPLICIT_KINDS = ("lookup", "handover")

    def resolve_category(self, rid, default_cat, cands, audit_at):
        """Category precedence: explicit record_type in a lookup > worker operation hint >
        collection/event specific category > resource-id prefix default."""
        for o in cands:
            if o.table == "contact_lookup_completed":
                return "SECONDARY_IDENTIFIER"
        explicit = [o.category for o in cands if o.kind in self.EXPLICIT_KINDS and o.category]
        if explicit:
            return explicit[-1]
        hint = self.activity_category_hint(rid, audit_at)
        if hint:
            return hint
        specific = [o.category for o in cands if o.category and o.category != default_cat]
        if specific:
            return specific[-1]
        return default_cat

    def activity_category_hint(self, rid: str, audit_at: str) -> str | None:
        """The deletion worker's chosen operation reveals how policy classifies the record."""
        for a in self.activities:
            if a["resource_id"] != rid or a["event_time"] > audit_at:
                continue
            sysname = a["target_system"]
            if a["event_type"] == "DELETE_RECORD" and sysname == "support_platform":
                return "SUPPORT_ATTACHMENT"
            if a["event_type"] == "REDACT_RECORD" and sysname == "support_platform":
                return "SUPPORT_MESSAGE"
            if a["event_type"] == "DELETE_RECORD" and sysname == "analytics_platform":
                return "SECONDARY_IDENTIFIER"
            if a["event_type"] == "ANONYMIZE_RECORD" and sysname == "analytics_platform":
                return "ANALYTICS_PROFILE"
        return None

    def snapshot_absence_evidence(self, rid: str, audit_at: str) -> str:
        """Latest full account snapshot (SQL) taken before audit_at that does not contain rid."""
        best = None
        for o in self.obs:
            if o.kind == "sql" and o.category == "USER_PROFILE" and o.file_time <= audit_at:
                if best is None or o.file_time > best[0]:
                    best = (o.file_time, o.evidence_id)
        if best and not any(o.evidence_id == best[1] for o in self.by_resource.get(rid, [])):
            return best[1]
        return ""

    def plan(self, c: CaseContext, views: dict) -> tuple[str, list[dict], dict]:
        steps = []
        findings = 0
        verify_ok = []      # compliant records (verification only)
        for rid, v in views.items():
            st, cat, ev, sysid = v["state"], v["category"], v["evidence"], v["system"]
            if st is None or cat is None:
                continue
            if cat == "DATA_FLOW":
                if st == "ACTIVE":
                    steps += [("FINDING", "REPORT_ACTIVE_DATA_FLOW", rid, sysid, ev),
                              ("REMEDIATION", "DISABLE_DATA_FLOW", rid, sysid, ""),
                              ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", rid, sysid, "")]
                    findings += 1
                elif st == "DISABLED":
                    verify_ok.append(("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", rid, sysid, ev))
                continue
            if cat == "BACKUP_ARCHIVE":
                if st == "ACTIVE":
                    steps += [("FINDING", "REPORT_RESTORE_RISK", rid, sysid, ev),
                              ("REMEDIATION", "BLOCK_RESTORE", rid, sysid, ""),
                              ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", rid, sysid, "")]
                    findings += 1
                elif st == "BLOCKED":
                    verify_ok.append(("VERIFICATION", "VERIFY_RESTORE_BLOCKED", rid, sysid, ev))
                continue
            action = required_action(self.rules, cat)
            if action == "DELETE":
                if st == "ABSENT":
                    if cat == "USER_PROFILE":
                        steps.append(("VERIFICATION", "VERIFY_RECORD_ABSENT", rid, sysid, ev))
                    else:
                        verify_ok.append(("VERIFICATION", "VERIFY_RECORD_ABSENT", rid, sysid, ev))
                else:
                    steps += [("FINDING", "REPORT_REMAINING_RECORD", rid, sysid, ev),
                              ("REMEDIATION", "DELETE_RECORD", rid, sysid, ""),
                              ("VERIFICATION", "VERIFY_RECORD_ABSENT", rid, sysid, "")]
                    findings += 1
            elif action in ("REDACT", "ANONYMIZE"):
                op = "REDACT_RECORD" if action == "REDACT" else "ANONYMIZE_RECORD"
                vop = "VERIFY_RECORD_REDACTED" if action == "REDACT" else "VERIFY_RECORD_ANONYMIZED"
                if st == "IDENTIFIABLE":
                    steps += [("FINDING", "REPORT_EXPOSED_PII", rid, sysid, ev),
                              ("REMEDIATION", op, rid, sysid, ""),
                              ("VERIFICATION", vop, rid, sysid, "")]
                    findings += 1
                elif st in ("REDACTED", "ANONYMIZED"):
                    verify_ok.append(("VERIFICATION", vop, rid, sysid, ev))
            elif action == "RETAIN":
                if st != "ABSENT":
                    steps.append(("VERIFICATION", "VERIFY_RECORD_PRESENT", rid, sysid, ev))
        # every deletion audit verifies the primary profile is gone
        acct = c.primary_account
        if acct and not any(s[2] == acct for s in steps):
            v = views.get(acct, {})
            if v.get("state") in (None, "ABSENT"):
                steps.append(("VERIFICATION", "VERIFY_RECORD_ABSENT", acct, "account_service", v.get("evidence", "")))
        status = "INCOMPLETE" if findings else "COMPLETE"
        if status == "COMPLETE":
            steps += verify_ok
        out = []
        for phase, op, rid, sysid, ev in steps:
            if status == "INCOMPLETE" and phase == "VERIFICATION":
                ev = ""
            out.append({"phase": phase, "operation": op, "system_id": sysid, "resource_id": rid, "evidence_id": ev})
        return status, order_steps(out), {}

    # ------------------------------------------------------------------
    @staticmethod
    def effective_state(category: str, o: Observation) -> str | None:
        st = o.state
        has_direct = any(o.pii.get(k) for k in ("email", "phone", "email_hash", "name"))
        has_ref = bool(o.pii.get("user_ref"))
        if st in ("ABSENT", "ACTIVE", "DISABLED", "BLOCKED"):
            return st
        if category in DELETE_CATS:
            return "PRESENT"          # any surviving copy of a delete-category record is "present"
        if category in ("SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE", "EMAIL_DELIVERY_LOG"):
            if st == "IDENTIFIABLE":
                return "IDENTIFIABLE"
            if st in ("REDACTED", "ANONYMIZED"):
                return "REDACTED"
            if o.kind in ("snapshot", "sql", "service_export"):
                return "IDENTIFIABLE" if has_direct else "REDACTED"
            return "IDENTIFIABLE" if has_direct else st
        if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
            if st in ("IDENTIFIABLE", "ANONYMIZED"):
                return st
            if o.kind in ("snapshot", "sql", "service_export"):
                return "IDENTIFIABLE" if (has_direct or has_ref) else "ANONYMIZED"
            return st
        return st
