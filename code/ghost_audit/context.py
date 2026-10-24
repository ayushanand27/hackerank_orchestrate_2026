"""Shared state of an audit run.

* :class:`AuditData`   – everything loaded from ``dataset/`` (read-only after loading)
* :class:`CaseContext` – what the pipeline learns about one request (links, attributions)
"""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .evidence import EvidenceStore, Observation, load_all
from .identity import IdentityIndex, Subject
from .policy import load_rules


def ts(s: str) -> datetime:
    """Parse the dataset's UTC timestamps (``2026-09-14T08:03:11Z``)."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def rid_num(rid: str) -> int:
    """Trailing number of an id (``case_017`` -> 17); used for deterministic tie-breaking."""
    m = re.search(r"(\d+)$", rid)
    return int(m.group(1)) if m else 0


def minutes_between(t: datetime, ref: datetime) -> float:
    return (t - ref).total_seconds() / 60


@dataclass
class CaseContext:
    """Per-request working memory.  Every link carries its reasons (``"obs:evidence_12"``,
    ``"link:act_00042"``, ``"cadence:act_00731"`` ...), which become the audit trail."""
    req: dict
    subject: Subject | None
    deletion: bool
    intent_reason: str
    direct: dict = field(default_factory=lambda: defaultdict(set))       # rid -> reasons (identifier-based)
    attributed: dict = field(default_factory=lambda: defaultdict(set))   # rid -> reasons (timing-based)
    primary_account: str | None = None
    doc_obs: list = field(default_factory=list)       # observations derived from PDFs / screenshots
    policy_claims: dict = field(default_factory=dict)  # rid -> "Policy action completed" activity

    @property
    def case_id(self) -> str:
        return self.req["case_id"]

    @property
    def audit_at(self) -> str:
        return self.req["audit_at"]

    @property
    def requested_at(self) -> str:
        return self.req["requested_at"]

    @property
    def auditable(self) -> bool:
        """A deletion request whose subject could be resolved."""
        return self.deletion and self.subject is not None

    def reset_attribution(self):
        self.attributed = defaultdict(set)
        self.primary_account = None
        self.policy_claims = {}


@dataclass
class AuditData:
    dataset_dir: str
    identities: IdentityIndex
    rules: dict
    evidence: EvidenceStore
    activities: list
    by_resource: dict

    @classmethod
    def load(cls, dataset_dir: str, cache_dir: str | None = None, historical_identity: bool = True,
             evidence: EvidenceStore | None = None) -> "AuditData":
        """Load a dataset.  ``evidence`` may be passed in to share one parsed evidence store
        between several runs (used by the ablation study)."""
        evidence = evidence or load_all(dataset_dir, cache_dir)
        with open(os.path.join(dataset_dir, "system_activity.csv"), encoding="utf-8") as fh:
            activities = list(csv.DictReader(fh))
        for a in activities:
            a["_t"] = ts(a["event_time"])
        by_resource: dict[str, list[Observation]] = defaultdict(list)
        for o in evidence.observations:
            by_resource[o.resource_id].append(o)
        return cls(dataset_dir, IdentityIndex(dataset_dir, historical=historical_identity),
                   load_rules(dataset_dir), evidence, activities, by_resource)

    # ------------------------------------------------------------------ convenience views
    @property
    def observations(self) -> list[Observation]:
        return self.evidence.observations

    @property
    def documents(self) -> list:
        return self.evidence.documents

    @property
    def coverage(self) -> list:
        return self.evidence.coverage

    # ------------------------------------------------------------------ identity tests
    @staticmethod
    def obs_values(o: Observation) -> set:
        """Every identifier value an observation carries (lookup keys + PII fields)."""
        vals = set(o.keys)
        for v in o.pii.values():
            vals |= set(v)
        return vals

    def belongs(self, subj: Subject, o: Observation) -> bool:
        return any(subj.matches(v) for v in self.obs_values(o))

    def foreign(self, subj: Subject, o: Observation) -> bool:
        """The observation carries identifiers of some *other* known subject and none of ours."""
        vals = self.obs_values(o)
        if not vals or any(subj.matches(v) for v in vals):
            return False
        for v in vals:
            v = str(v)
            if "@" in v and self.identities.by_email.get(v.lower()):
                return True
            if re.fullmatch(r"user_\d+", v) and v in self.identities.subjects:
                return True
        return False

    def batch_evidence(self, c: CaseContext) -> list[str]:
        """Evidence files (captured by ``audit_at``) that carry the case's own identifiers for an
        in-window observation, most-used first.  These form the case's "evidence batch"."""
        cnt: dict[str, int] = defaultdict(int)
        for o in self.observations:
            if o.resource_id in c.direct and c.requested_at <= o.time <= c.audit_at \
                    and o.file_time <= c.audit_at and self.belongs(c.subject, o):
                cnt[o.evidence_id] += 1
        return [k for k, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]
