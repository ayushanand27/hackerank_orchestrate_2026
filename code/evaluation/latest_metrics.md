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

- **case_901** - Request sent from a historical e-mail; search entry keyed by the old address and a regional replica still holding a profile the primary already deleted (replication drift).
- **case_902** - Access/portability request that explicitly refuses deletion -> NOT_APPLICABLE with [].
- **case_903** - Compliant at audit_at; a connector re-creates the search entry two days AFTER audit_at (must be ignored) and a look-alike decoy e-mail (gamma.jr@...) must not be attributed.
- **case_904** - Active connector re-populating search + restorable backup: disable/block must precede the downstream delete; lineage from the connector links the copy.
- **case_905** - Tombstone still exposing the e-mail (REDACT, keep stable reference) and a support message that must be redacted rather than deleted; Japanese request text.

#### Custom-case metrics

| metric | value |
|---|---|
| cases | 5 |
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

## 3. Contract validation of output.csv

- rows: 518; status distribution: {'INCOMPLETE': 355, 'COMPLETE': 153, 'NOT_APPLICABLE': 10}
- contract violations: 0
