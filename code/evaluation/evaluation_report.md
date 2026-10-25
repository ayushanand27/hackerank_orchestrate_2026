# Evaluation report — Ghost Data deletion-audit agent

Everything below is reproducible from the repository root:

```bash
python3 code/main.py                              # output.csv (+ audit trail, run statistics)
python3 code/evaluation/main.py                   # sample + custom metrics, contract check -> latest_metrics.md
python3 code/evaluation/ablation.py               # strategy comparison (~2 min)          -> ablation.md
python3 code/evaluation/consistency.py            # unsupervised check on all requests     -> consistency.md
python3 -m unittest discover -s code/tests        # 69 unit / integration tests
```

The evaluation has five parts: (1) the 32 public sample requests scored against
`dataset/sample_output.csv`; (2) eleven custom synthetic scenarios with hand-written expected
answers; (3) a strategy comparison that switches every technique off in turn; (4) a contract
validator plus an unsupervised consistency check on the 518 evaluation requests, whose answers
are hidden; (5) a unit-test suite. The agent never reads `sample_output.csv`; only the
evaluator does.

## 1. Metrics on the public sample (submitted configuration)

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

Definitions (`metrics.py`): a step is the tuple `phase, system_id, resource_id, operation,
state, evidence_id`; precision / recall are over those tuples per case; field accuracy compares
step *i* of the expected list with step *i* of the prediction field by field; sequence accuracy
requires the complete ordered list to match. The per-case table is in `latest_metrics.md`.

The agent makes **no false-positive step** on the sample (precision 1.000). The three misses
are records whose identifiers appear nowhere in participant data, so no evidence-based agent
can produce them:

| case | missing step | why |
|---|---|---|
| case_04 | `VERIFY_RECORD_ABSENT account_004` | `account_004` appears in no evidence file and no activity row |
| case_18 | `VERIFY_RECORD_PRESENT audit_record_6645` | id appears nowhere in `dataset/` |
| case_20 | `VERIFY_RECORD_PRESENT audit_record_5569` | id appears nowhere in `dataset/` |

## 2. Custom evaluation cases (created by us)

`custom_cases.py` writes a miniature dataset (identities, audit log, activity stream, two
readback logs, two SQL snapshots, a lookup ledger) and runs the real pipeline on it. The
expected answers are written by hand from the problem statement. Cases 906–911 were designed so
that **each one is solved by exactly one technique** — the unit tests assert that switching that
technique off breaks that case.

| case | scenario | guards | result |
|---|---|---|---|
| case_901 | request from a **historical e-mail**; search entry keyed by that address; a regional **replica** still holds the deleted profile under the current address | identity history, replication drift | exact |
| case_902 | "send me a copy… please **don't delete** anything" | intent: `NOT_APPLICABLE` + `[]` despite the word *delete* | exact |
| case_903 | compliant at `audit_at`; a connector **re-creates** the entry two days *after* `audit_at`; look-alike decoy `gamma.jr@…` | temporal cut-off, decoy rejection | exact |
| case_904 | active connector re-populating search + restorable backup | remediation order: disable → block → delete | exact |
| case_905 | tombstone still exposing the e-mail; support message; Japanese text | `ACCOUNT_TOMBSTONE` → REDACT (keep reference); message REDACT not DELETE | exact |
| case_906 | search copy with **no identifiers**, re-created by the subject's active connector; profile only findable by timing | lineage, timing attribution | exact |
| case_907 + 908 | two requests reported together, each with an **identifier-free anonymised analytics** profile | policy-action phase gives each case its own record | exact |
| case_909 | search entry seen after the request, absent from a later **complete snapshot** of the case's batch | absence by omission → `COMPLETE`, redacted tombstone kept | exact |
| case_910 | support item the deletion worker tried to **DELETE** | worker hint: attachment → delete, not redact | exact |
| case_911 | marketing profile only in an identifier-free lookup ledger; reported `COMPLETE` | catalogue link (`LINK_RESOURCE_IDENTITY`) | exact |

Custom-case metrics: status accuracy 1.000, exact rows 11/11, step F1 1.000, field accuracy
1.000, sequence accuracy 1.000.

## 3. Strategy comparison (ablation)

Every technique has a switch in `ghost_audit/config.py::Features`. `ablation.py` re-runs the
whole pipeline per configuration. Sample metrics use the public answers; on the 518 evaluation
requests (no answers) we report how many rows change relative to the submitted configuration and
how many contract violations the configuration produces.

### Cumulative build-up (techniques added in the order they were developed)

| configuration | status acc. | exact rows | step F1 | field acc. | custom exact | test rows changed | contract violations |
|---|---|---|---|---|---|---|---|
| exact identifiers only (current snapshot) | 0.906 | 0.438 | 0.887 | 0.699 | 3/11 | 371 | 12 |
| + historical identifiers (audit log) | 0.938 | 0.469 | 0.914 | 0.727 | 4/11 | 332 | 12 |
| + post-request state only | 0.938 | 0.469 | 0.914 | 0.727 | 4/11 | 332 | 12 |
| + catalogue links + documents (PDF/OCR) | 0.969 | 0.500 | 0.928 | 0.748 | 5/11 | 320 | 12 |
| + lineage (connector / restore) | 0.969 | 0.500 | 0.928 | 0.748 | 5/11 | 320 | 12 |
| + worker-operation category hint | 1.000 | 0.531 | 0.942 | 0.768 | 6/11 | 320 | 12 |
| + timing attribution of the primary profile | 1.000 | 0.688 | 0.971 | 0.914 | 8/11 | 162 | 12 |
| + batch cadence re-solve | 1.000 | 0.719 | 0.976 | 0.915 | 8/11 | 124 | 12 |
| + policy-action phase | 1.000 | 0.906 | 0.992 | 0.966 | 10/11 | 12 | 12 |
| **+ absence by omission (= submitted)** | **1.000** | **0.906** | **0.992** | **0.966** | **11/11** | **0** | **0** |

### Leave one out (everything else on)

| configuration | status acc. | exact rows | step F1 | field acc. | custom exact | test rows changed | contract violations |
|---|---|---|---|---|---|---|---|
| all techniques (submitted) | 1.000 | 0.906 | 0.992 | 0.966 | 11/11 | 0 | 0 |
| without historical identity | 0.969 | 0.812 | 0.968 | 0.910 | 10/11 | 73 | 1 |
| without catalogue links | 1.000 | 0.906 | 0.992 | 0.966 | 10/11 | 9 | 0 |
| without documents (PDF/OCR) | 0.969 | 0.875 | 0.979 | 0.946 | 11/11 | 12 | 0 |
| without lineage | 1.000 | 0.906 | 0.992 | 0.966 | 10/11 | 0 | 0 |
| without timing attribution | 1.000 | 0.594 | 0.960 | 0.792 | 9/11 | 262 | 0 |
| without batch cadence | 1.000 | 0.875 | 0.987 | 0.965 | 11/11 | 57 | 0 |
| without policy-action phase | 1.000 | 0.719 | 0.976 | 0.915 | 9/11 | 120 | 0 |
| without omission | 1.000 | 0.906 | 0.992 | 0.966 | 10/11 | 12 | 12 |
| without worker hint | 0.969 | 0.875 | 0.979 | 0.946 | 10/11 | 7 | 0 |
| without post-request filter | 1.000 | 0.875 | 0.984 | 0.940 | 11/11 | 1 | 0 |

What the comparison shows:

* **Identifier matching alone reaches 0.44 exact rows.** The biggest gains come from
  attributing records that carry no identifiers: timing attribution of the primary profile
  (+0.16 exact; 262 evaluation rows depend on it) and the policy-action phase (+0.19 exact; 120
  rows), because deleted profiles answer 404 without an e-mail and anonymised analytics have no
  identifiers left.
* **Absence by omission does not change the sample but removes all 12 contract violations** on
  the evaluation set: without it, some states are only provable from files exported after
  `audit_at`, which must not be cited.
* **Historical identifiers** matter on 73 evaluation rows (requests made with an old address, or
  evidence still keyed by one).
* **Lineage has no measurable effect on this dataset** (re-created copies also carry the
  subject's identifiers); it is kept because the problem statement describes identifier-free
  re-creation, and custom case 906 guards it.
* Every technique except lineage changes at least one evaluation row, and no single technique is
  responsible for the result: the techniques are complementary, not redundant.

## 4. Unit and integration tests

`python3 -m unittest discover -s code/tests` — 69 tests, ~10 s, all passing.

| file | covers |
|---|---|
| `test_logparse.py` | all seven log layouts (bracket, pipe with leading `\|`, logfmt, JSON, syslog, nested cloud `message="…"`), quoted values |
| `test_evidence.py` | snapshot bundles (+ coverage), free-text e-mail/phone leaks, backups/tombstones, lookup ledgers, hand-over notes, trace-id key join, SQL replicas + `snapshot_scope`, OCR id repair |
| `test_identity.py` | historical e-mail / phone / hash matching, reused address → current owner, `.jr` decoys, history switch |
| `test_intent.py` | deletion in 8 languages, refusals win over a deletion verb, opt-out / correction / access are not deletion |
| `test_policy.py` | rules file vs fallback, operation ↔ phase ↔ state table, disable/block before delete, deterministic tie-break |
| `test_state_and_planner.py` | effective-state rules per category, COMPLETE plans cite evidence, INCOMPLETE plans (findings → remediation → verification, empty evidence for future steps) |
| `test_attribution.py` | circular phase distance, time windows, cadence formula, batch-offset fitting recovers a planted offset and rejects noise |
| `test_contract.py` | the output-contract validator itself: a valid file passes; late / unknown evidence, evidence on future steps, findings without evidence, wrong states, numbering, order, extra keys, status rules and missing rows are each reported (mutation tests) |
| `test_agent.py` | the review agent with a scripted model (no network): OpenAI-format conversion for Groq, tool loop, review accepted only when valid, unknown / late evidence and foreign resources rejected, text without a tool call and the turn cap end in UNCERTAIN, the gate never changes a plan, request text is passed as data, tools never reveal identifier values |
| `test_engine_scenarios.py` | the 11 custom scenarios end to end; each technique is load-bearing for its scenario; audit-trail content |
| `test_submission.py` | (needs `dataset/`) fresh run passes the contract, reproduces `output.csv` byte for byte, never cites evidence captured after `audit_at`, sample status accuracy, determinism across runs |

## 5. Contract validation and unsupervised checks on the 518 evaluation requests

`contract.py::validate_output` checks: exact header; 518 rows sorted by case number; statuses in
the allowed set; valid JSON; consecutive `step_no` from 1; operation ↔ phase ↔ state
consistency; phase / operation / system / resource ordering; `NOT_APPLICABLE` ⇒ `[]`;
`COMPLETE` ⇒ verification only; `INCOMPLETE` ⇒ at least one finding; findings always cite
evidence; remediation and post-remediation verification cite `""`; every cited id exists
(`file_id` / `activity_id` / `audit_id`) and was captured at or before the case's `audit_at`.

Final run: **0 violations**; 355 `INCOMPLETE`, 153 `COMPLETE`, 10 `NOT_APPLICABLE`.

**Unsupervised consistency** (`consistency.py`, all 550 requests). The timing model makes a
falsifiable prediction about events it never used: a "Policy action completed" event on a record
linked to a case *by identifiers* must sit at the seconds phase of the primary-profile
submission that timing attribution chose for that case.

| check | result |
|---|---|
| identifier-linked policy events whose phase matches the attributed primary submission | **121/121** (100 %) |
| same events against another case's submission (chance level) | 17/121 (14 %) |
| identifier-free anonymisation events attributed to exactly one case | 124/125 |
| identifier-free legal-hold retention events attributed to exactly one case | 2/2 |

100 % agreement against a 14 % chance level is strong evidence that the account attribution
generalises beyond the 32 sample cases.

## 6. Failures found and improvements made

| # | version | sample status / exact / step-F1 / field acc. | failure observed | improvement |
|---|---|---|---|---|
| 0 | direct identifier linking only | 0.906 / 0.219 / 0.857 / 0.630 | deleted profiles return 404 **without** an e-mail, so primary accounts were never linked; replica evidence ignored when the file was exported after `audit_at` | link the primary profile through the workflow timing (submitted 30–90 min before the reported status, worker result mirrors the reported status); judge visibility per *log line / record time*, not per file export time |
| 1 | + timing attribution | 0.938 / 0.594 / 0.924 / 0.796 | PDF/screenshot resources had no state; support items deleted vs redacted mis-classified; stale accounts from an earlier episode verified | documents become observations (catalogue aliases, single-resource pages); category precedence: explicit record type > worker operation > collection; only post-request observations describe the audited state; non-primary absent accounts dropped |
| 2 | + documents / categories | 1.000 / 0.875 / 0.987 / 0.965 | several cases reported at the same minute with the same worker result → account order guessed wrong (e.g. case_23) | learnt the **batch cadence**: consecutive requests advance the submission offset by a constant step; fit per batch, reassign ties to the closest slot |
| 3 | + cadence | 1.000 / 0.906 / 0.992 / 0.966 | 13 contract violations: some states only provable from a log exported after `audit_at` | **absence by omission** from complete snapshots (SQL `snapshot_scope`, bundle collections) |
| 4 | omission applied globally | 0.938 / 0.625 / 0.920 / 0.870 (regression) | snapshots of *other* batches "proved" absence of everything they did not list | restrict omission to the case's own evidence batch; cite in-time files first → 0.906 exact, **0 violations** |
| 5 | + state normalisation | unchanged | a delete-category record shown "identifiable" in one file and "present" in another produced a late citation | any surviving copy of a delete-category record is `PRESENT` |
| 6 | + joint re-solve | unchanged on sample; every evaluation case gets a primary profile | the first cadence pass could release a slot without re-assigning the case that lost it (case_95 ended with no account); a batch's trailing request is dated later than its index suggests | re-solve each reporting group jointly with cadence distance as cost (index and index+20); identifier-linked and explicitly reported accounts stay fixed |
| 7 | + policy phase | unchanged on sample; 124/125 anonymised analytics attributed | anonymised analytics (no identifiers) were assigned by order inside a reporting group | follow-up policy actions inherit the seconds phase of the case's primary submission (slot *k*: +40.5 s + 29.5 s·k) |
| 8 | refactor + ablation | unchanged (output byte-identical, `cmp`) | the ablation exposed an empty `INCOMPLETE` plan (a contract violation) whenever a requester e-mail matched no identity; custom case 901 did not really need the identity history; lineage was never exercised | unknown requester e-mails are audited by the e-mail itself; case 901 strengthened; cases 906–911 added so every technique has a scenario that only it solves |
| 9 | + review agent | unchanged (output byte-identical; dry run over 508 deletion cases: every review validated, 0 rows changed) | the engine had no place for a model to check its work, and nothing marked rows a human should look at | optional Claude reviewer with read-only tools, turn and retry caps, code-side validation of every review, and a gate that can only flag (`code/ghost_audit/agent/`) |

Other defects fixed during development: pipe-separated logs with a leading `|`; free-text
support messages leaking an e-mail while structured fields were null (now scanned); tombstone /
legal-hold policy events use slot 0; OCR of dark-theme screenshots (OCR on both polarities at
2× scale; a cold OCR run reproduces the cached text and `output.csv` byte for byte).

## 7. Known limitations

* Records whose identifiers appear nowhere in participant data (three sample steps) cannot be
  produced.
* Eleven hand-crafted single-artifact requests (e.g. one screenshot or PDF) name no account
  before `audit_at`; for them we report only what the artifacts prove.
* When two in-time files show the same state, the latest observation is cited; the hidden key
  might prefer another file of the same batch.
* The timing constants (windows, cadence, phase offsets) were learnt from this dataset's
  workflow; they live in `config.py` and would need re-fitting for a different workflow. If the
  timing signals are absent, the agent degrades to identifier-only linking rather than guessing.
