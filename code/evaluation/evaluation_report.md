# Evaluation report — Ghost Data deletion-audit agent

Reproduce everything below with:

```bash
python3 code/main.py                 # writes output.csv
python3 code/evaluation/main.py      # prints and saves code/evaluation/latest_metrics.md
```

The evaluation workflow has three parts: (1) the 32 public sample requests scored against
`dataset/sample_output.csv`, (2) five custom synthetic scenarios with hand-written expected
answers (`custom_cases.py`), and (3) a contract validator over the full 518-row `output.csv`.
The agent never reads `sample_output.csv`; only the evaluator does.

## 1. Metrics on the public sample (final version)

| metric | value |
|---|---|
| cases | 32 |
| **case-level status accuracy** | **1.000** (32/32) |
| exact row accuracy (status + full ordered step list) | 0.906 (29/32) |
| step precision (all 6 fields must match) | 1.000 |
| step recall | 0.984 |
| **step F1** | **0.992** |
| step F1 ignoring `evidence_id` | 0.992 |
| **field-level accuracy** (positional, 6 fields × max(len)) | **0.966** |
| **sequence accuracy** (ordered list identical) | **0.906** |
| sequence LCS ratio | 0.984 |

Definitions (see `metrics.py`): a step is a tuple of `phase, system_id, resource_id,
operation, state, evidence_id`; precision/recall are over those tuples per case;
field accuracy compares step *i* of the expected list with step *i* of the prediction field by
field; sequence accuracy requires the complete ordered list to match.

Remaining sample misses (all three are not recoverable from participant data):

| case | missing step | why |
|---|---|---|
| case_04 | `VERIFY_RECORD_ABSENT account_004` | `account_004` appears in no evidence file and no activity row |
| case_18 | `VERIFY_RECORD_PRESENT audit_record_6645` | id appears nowhere in `dataset/` |
| case_20 | `VERIFY_RECORD_PRESENT audit_record_5569` | id appears nowhere in `dataset/` |

## 2. Custom evaluation cases (created by us)

Built as a separate miniature dataset (identities, audit log, activity stream, a readback log
and a replica SQL snapshot) by `custom_cases.py`; expected answers are written by hand from the
problem statement, not from any model output.

| case | scenario | what it guards against | result |
|---|---|---|---|
| case_901 | request uses a **historical e-mail**; search entry keyed by the old address; a **regional replica** still holds a profile the primary deleted | identity resolution through `user_audit_log`, replication drift | exact |
| case_902 | "send me a copy… please **don't delete** anything" | intent classifier must return `NOT_APPLICABLE` + `[]` despite the word *delete* | exact |
| case_903 | compliant at `audit_at`; a connector **re-creates** the search entry two days *after* `audit_at`; a look-alike decoy `gamma.jr@…` | temporal cut-off, decoy rejection | exact |
| case_904 | active connector re-populating search + restorable backup | lineage linking, remediation order (disable → block → delete) | exact |
| case_905 | tombstone still exposing the e-mail; support message; Japanese text | `ACCOUNT_TOMBSTONE` → REDACT (keep reference), message REDACT not DELETE | exact |

Custom-case metrics: status accuracy 1.000, exact rows 5/5, step F1 1.000, field accuracy
1.000, sequence accuracy 1.000.

## 3. Contract validation of the submitted `output.csv`

`evaluation/main.py::validate_output` checks: exact header; 518 rows sorted by case number;
statuses in the allowed set; valid JSON; consecutive `step_no` from 1; operation ↔ phase ↔
state consistency; phase/operation/system/resource ordering; `NOT_APPLICABLE` ⇒ `[]`;
`COMPLETE` ⇒ verification only; `INCOMPLETE` ⇒ at least one finding; findings always cite
evidence; remediation and post-remediation verification cite `""`; every cited id exists
(`file_id` / `activity_id` / `audit_id`) and was captured at or before the case's `audit_at`.

Final run: **0 violations**; distribution 355 `INCOMPLETE`, 153 `COMPLETE`, 10 `NOT_APPLICABLE`.

### Unsupervised consistency check on the 518 hidden cases

Because the hidden answers are unavailable, we also verify internal consistency of the
attribution model on the full dataset: for every case whose already-compliant records carry
identifiers, the timing phase of its "policy action completed" events must agree with the
primary-profile submission we attributed to it. **246 of 247** such events agree (the single
disagreement is a slot-numbering artefact, not a different case), which is strong evidence
that the timing-based attribution generalises beyond the sample.

## 4. Failures found and improvements made

| # | version | sample status / exact / step-F1 / field acc. | failure observed | improvement |
|---|---|---|---|---|
| 0 | direct identifier linking only | 0.906 / 0.219 / 0.857 / 0.630 | deleted profiles return 404 **without** an e-mail, so primary accounts were never linked; replica evidence ignored when the file was exported after `audit_at` | link the primary profile through the workflow timing (submitted 45–75 min before the reported status, worker result mirrors the reported status); judge visibility per *log line / record time*, not per file export time |
| 1 | + timing attribution | 0.938 / 0.594 / 0.924 / 0.796 | PDF/screenshot resources had no state; support items deleted vs redacted mis-classified; stale accounts from an earlier episode of the same person verified | documents become observations (catalogue `LINK_RESOURCE_IDENTITY` aliases, single-resource pages); category precedence: explicit record type > worker operation (`DELETE_RECORD` on support ⇒ attachment) > collection; only observations after `requested_at` describe the audited state; non-primary absent accounts dropped |
| 2 | + documents / categories | 1.000 / 0.875 / 0.987 / 0.965 | several cases in the same batch reported at the same minute with the same worker result → account order guessed wrong (e.g. case_23) | learnt the **batch cadence**: consecutive requests advance the submission offset by a constant step; fit per batch and reassign ties to the closest slot |
| 3 | + cadence | 1.000 / 0.906 / 0.992 / 0.966 | 13 contract violations: some states were only provable from a log exported after `audit_at` | added **absence-by-omission** from complete snapshots (SQL `snapshot_scope`, bundle collections) |
| 4 | omission applied globally | 0.938 / 0.625 / 0.920 / 0.870 (regression) | snapshots belonging to *other* batches "proved" absence of everything they did not list | restrict omission to the case's own evidence batch (files carrying its identifiers); cite in-time files first → back to 0.906 exact, **0 violations** |
| 5 | + state normalisation | unchanged | a delete-category record shown "identifiable" in one file and "present" in another produced a late citation | any surviving copy of a delete-category record is `PRESENT` |
| 6 | + policy phase | unchanged on sample; test: 123/125 anonymised-analytics events attributed, 2 legal holds recovered | anonymised analytics (no identifiers) were assigned by order inside a reporting group | discovered that follow-up policy actions inherit the seconds-phase of the case's primary submission (slot *k*: +40.5 s + 29.5 s·k); attribute by phase, legal holds included |

Other defects fixed during development: pipe-separated logs with a leading `|`; free-text
support messages leaking an e-mail while structured fields were null (now scanned);
tombstone / legal-hold policy events use slot 0 (fixed the phase offset); OCR of dark-theme
screenshots (now OCR on both polarities at 2× scale).

## 5. Known limitations

* Records whose identifiers appear nowhere in participant data (three sample steps) cannot be
  produced.
* Hand-crafted scenarios with a single artifact (e.g. a screenshot) carry no account id before
  `audit_at`; we report only what the artifact proves.
* Evidence choice when two in-time files show the same state follows "latest observation";
  the hidden key might prefer a different file.
