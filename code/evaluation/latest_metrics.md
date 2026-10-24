# Latest evaluation run

## 1. Public sample (32 cases)

#### Sample metrics

| metric | value |
|---|---|
| cases | 32 |
| status_accuracy | 1.0000 |
| exact_row_accuracy | 0.9062 |
| step_precision | 1.0000 |
| step_recall | 0.9845 |
| step_f1 | 0.9922 |
| step_f1_ignoring_evidence | 0.9922 |
| field_accuracy | 0.9663 |
| sequence_exact_accuracy | 0.9062 |
| sequence_lcs_ratio | 0.9845 |

| case | expected | predicted | exact | missing steps | extra steps |
|---|---|---|---|---|---|
| case_01 | COMPLETE | COMPLETE | yes |  |  |
| case_02 | COMPLETE | COMPLETE | yes |  |  |
| case_03 | COMPLETE | COMPLETE | yes |  |  |
| case_04 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_004 (evidence_54) |  |
| case_05 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_06 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_07 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_08 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_09 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_10 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_11 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_12 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_13 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_14 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_15 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_16 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_17 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_18 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_6645 (-) |  |
| case_19 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_20 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_5569 (-) |  |
| case_21 | COMPLETE | COMPLETE | yes |  |  |
| case_22 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_23 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_24 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_25 | COMPLETE | COMPLETE | yes |  |  |
| case_26 | COMPLETE | COMPLETE | yes |  |  |
| case_27 | COMPLETE | COMPLETE | yes |  |  |
| case_28 | COMPLETE | COMPLETE | yes |  |  |
| case_29 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_30 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_31 | COMPLETE | COMPLETE | yes |  |  |
| case_32 | INCOMPLETE | INCOMPLETE | yes |  |  |

## 2. Custom scenarios

- **case_901** - Request sent from a historical e-mail; the search entry is keyed by that old address while a regional replica still holds the deleted profile under the current address (replication drift); only the identity history links both.
- **case_902** - Access/portability request that explicitly refuses deletion -> NOT_APPLICABLE with [].
- **case_903** - Compliant at audit_at; a connector re-creates the search entry two days AFTER audit_at (must be ignored) and a look-alike decoy e-mail (gamma.jr@...) must not be attributed.
- **case_904** - Active connector re-populating search + restorable backup: disable/block must precede the downstream delete; lineage from the connector links the copy.
- **case_905** - Tombstone still exposing the e-mail (REDACT, keep stable reference) and a support message that must be redacted rather than deleted; Japanese request text.
- **case_906** - Search copy with no identifiers at all, re-created by the subject's active connector: only lineage links it; the primary profile is found by workflow timing alone.
- **case_907** - Two requests reported together, each with an identifier-free anonymised analytics profile: the policy-action timing phase must give each case its own profile (pair with case_908).
- **case_908** - Second half of the joint policy-phase scenario (German / Spanish request texts).
- **case_909** - Search entry seen after the request; a later complete snapshot of the search service from the case's own batch no longer lists it -> absent by omission -> COMPLETE; redacted tombstone kept.
- **case_910** - Support item the deletion worker tried to DELETE (an attachment): must be reported as a remaining record and deleted, not redacted like a message.
- **case_911** - Marketing profile visible only in an identifier-free lookup ledger; the activity catalogue links it to the subject. Reported COMPLETE but actually incomplete.

#### Custom-case metrics

| metric | value |
|---|---|
| cases | 11 |
| status_accuracy | 1.0000 |
| exact_row_accuracy | 1.0000 |
| step_precision | 1.0000 |
| step_recall | 1.0000 |
| step_f1 | 1.0000 |
| step_f1_ignoring_evidence | 1.0000 |
| field_accuracy | 1.0000 |
| sequence_exact_accuracy | 1.0000 |
| sequence_lcs_ratio | 1.0000 |

| case | expected | predicted | exact | missing steps | extra steps |
|---|---|---|---|---|---|
| case_901 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_902 | NOT_APPLICABLE | NOT_APPLICABLE | yes |  |  |
| case_903 | COMPLETE | COMPLETE | yes |  |  |
| case_904 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_905 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_906 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_907 | COMPLETE | COMPLETE | yes |  |  |
| case_908 | COMPLETE | COMPLETE | yes |  |  |
| case_909 | COMPLETE | COMPLETE | yes |  |  |
| case_910 | INCOMPLETE | INCOMPLETE | yes |  |  |
| case_911 | INCOMPLETE | INCOMPLETE | yes |  |  |

## 3. Contract validation of output.csv

- rows: 518; status distribution: {'INCOMPLETE': 355, 'COMPLETE': 153, 'NOT_APPLICABLE': 10}
- contract violations: 0
