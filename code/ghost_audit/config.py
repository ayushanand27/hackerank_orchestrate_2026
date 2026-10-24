"""Feature switches and every tunable constant of the audit pipeline, in one place.

All values were derived by reading participant-facing data only (the activity stream, the
evidence files and the public sample); none of them encodes a case id or an expected answer.
The :class:`Features` switches exist so that the evaluation workflow can measure what each
investigation technique contributes (``code/evaluation/ablation.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import timedelta


@dataclass(frozen=True)
class Features:
    """One switch per investigation technique.  The submitted run uses the defaults (all on)."""

    #: also match identifiers that were changed earlier (``user_audit_log.csv``)
    historical_identity: bool = True
    #: ``LINK_RESOURCE_IDENTITY`` catalogue events tie a resource (and its alias) to a person
    catalog_links: bool = True
    #: PDFs and OCR'd screenshots become observations of the resources they show
    documents: bool = True
    #: an active connector / restore that "repopulated" a copy links the copy to the source's owner
    lineage: bool = True
    #: primary profile found through the workflow's submission window + mirrored worker result
    timing_attribution: bool = True
    #: per-batch submission cadence resolves ties between identical candidates (needs timing)
    batch_cadence: bool = True
    #: identifier-free "policy action completed" events attributed by their seconds-phase
    policy_phase: bool = True
    #: a complete snapshot from the case's own evidence batch proves absence by omission
    omission: bool = True
    #: the deletion worker's operation (DELETE vs REDACT) disambiguates the data category
    worker_hint: bool = True
    #: only observations made after ``requested_at`` describe the audited state
    post_request_only: bool = True

    def without(self, *names: str) -> "Features":
        """Copy of these switches with the named techniques turned off."""
        return replace(self, **{n: False for n in names})

    @classmethod
    def names(cls) -> list[str]:
        """Names of all switches, in declaration order."""
        return [f.name for f in fields(cls)]


# ----------------------------------------------------------------------------- policy vocabulary
#: categories whose every surviving copy is a violation (policy action DELETE)
DELETE_CATEGORIES = frozenset({
    "USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "SUPPORT_ATTACHMENT",
    "USER_EXPORT", "MARKETING_PROFILE",
})
#: records that must survive the deletion; they are judged regardless of ``requested_at``
RETAINED_CATEGORIES = frozenset({"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"})
#: processes (not records): judged as ACTIVE / DISABLED / BLOCKED
PROCESS_CATEGORIES = frozenset({"DATA_FLOW", "BACKUP_ARCHIVE"})
#: categories whose identifiers must be removed while the record stays (REDACT)
REDACT_LIKE_CATEGORIES = frozenset({"SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE", "EMAIL_DELIVERY_LOG"})
#: categories that must be irreversibly unlinked (ANONYMIZE)
ANONYMIZE_LIKE_CATEGORIES = frozenset({"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"})

#: the privacy workflow's reported status and the worker result it mirrors
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}

# ----------------------------------------------------------------------------- workflow timing
#: primary-profile deletions are submitted 30-90 min before the workflow reports its status
PRIMARY_WINDOW_MIN = (-90, -30)
#: workflows that name their target ("Target resource: X") report within +-20 min
DIRECT_REPORT_WINDOW_MIN = (-20, 20)
#: follow-up "Policy action completed" events land in the 35 min before the report
POLICY_ACTION_WINDOW_MIN = (-35, 0)

# Batch cadence: inside one audit batch, request *i* (counted back from the newest request, one
# calendar day apart) has its primary profile submitted at
#     reported_at - (BASE + (A + STEP * i) mod PERIOD)   seconds,
# where the per-batch offset A is fitted from the unambiguous cases.
CADENCE_BASE_S = 2700
CADENCE_STEP_S = 674
CADENCE_PERIOD_S = 1890
CADENCE_TOL_S = 75
CADENCE_SEARCH_STEP_S = 3
#: the trailing request of a batch is sometimes dated later; its slot is then index + 20
CADENCE_TRAILING_SHIFT = 20
CADENCE_MIN_BATCH = 3

# Policy-action phase: follow-up actions of a case occupy consecutive slots whose seconds-of-minute
# is   primary_second + OFFSET + ADVANCE * k   (slot k=0 first; a legal hold takes slot 0).
POLICY_PHASE_OFFSET_S = 40.5
POLICY_SLOT_ADVANCE_S = 29.5
POLICY_PHASE_TOL_S = 1.5
#: per-operation seconds offset used to recover a case's phase from its own policy events
POLICY_OPERATION_OFFSET_S = {
    "ANONYMIZE_RECORD": 40.5, "DISABLE_DATA_FLOW": 39.5, "REDACT_RECORD": 10, "BLOCK_RESTORE": 9,
    "RETAIN_RECORD": 40.5,
}

# ----------------------------------------------------------------------------- documents
#: the catalogue registers an artifact shortly after it was captured
DOC_LINK_WINDOW = timedelta(hours=2)
#: an alias shorter than this (after squashing) is too generic to match inside OCR text
MIN_ALIAS_LEN = 6
