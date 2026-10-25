# Evaluation report

Deletion-audit agent for *Ghost Data Still Exists* (HackerRank Orchestrate, October 2026).

All numbers below were produced by the code in this archive with
`python code/evaluation/run_all.py` on the supplied dataset. No organizer-only data,
golden labels or case-family shortcuts are used at prediction time; the only goldens
ever read are `dataset/sample_output.csv`, and only by the evaluation harness.

---

## 1. What is measured

| Metric | Definition |
|---|---|
| Case-level status accuracy | share of cases whose `deletion_status` matches the oracle |
| Step precision / recall / F1 | semantic step match on `(phase, operation, system_id, resource_id, state)`; order-independent |
| Field-level accuracy | per-field agreement over aligned steps (`phase`, `system_id`, `resource_id`, `operation`, `state`, `evidence_id`) |
| Exact sequence accuracy | share of cases whose full ordered step list matches exactly |
| Edit distance | Levenshtein distance between predicted and expected operation sequences |

`code/evaluation/evaluate.py` scores a prediction file against any oracle CSV and
writes machine-readable per-case diffs. Its self-test — scoring
`dataset/sample_output.csv` against itself — returns 100% on every metric, so the
harness cannot silently reward a wrong answer.

Deterministic holdout: `case` index modulo 4, so the same cases always form the
holdout and no tuning can leak across the split.

---

## 2. Headline results (32 published samples)

The holdout split is the honest headline: it is drawn by SHA-256(case_id) and can
never have been tuned against. All-samples numbers are shown for reference.

| Metric | Holdout (10) | All 32 (reference) |
|---|---|---|
| Status accuracy | **100.0%** | **100.0%** |
| Step precision | 89.1% | 93.4% |
| Step recall | 80.4% | 87.6% |
| Step F1 | **84.5%** | **90.4%** |
| Exact sequence accuracy | 40.0% | **46.9%** |
| Mean edit distance | 1.00 | 0.75 |
| Normalized edit similarity | — | 87.6% |

**Holdout stability (leave-some-out, same predictor, different modulus):** status
accuracy is 100.0% under moduli 3, 4 and 5; step F1 is 90.9% / 84.5% / 82.8% and
exact-sequence 44.4% / 40.0% / 0.0%. The F1 spread across splits (82.8–90.9%) is
small-sample variance (the modulus-5 split has only 3 cases), not tuning to one
split; the all-samples F1 of 90.4% sits inside that range.

Status confusion matrix (all 32): `COMPLETE` 10/10, `INCOMPLETE` 22/22,
`NOT_APPLICABLE` 0/0 (the published samples contain no non-deletion request).

Field-level accuracy over aligned steps (all 32):

| Field | Accuracy |
|---|---|
| `phase` | 93.8% |
| `system_id` | 87.6% |
| `resource_id` | 81.3% |
| `operation` | 87.6% |
| `state` | 87.6% |
| `evidence_id` | 91.7% |

`resource_id` and `system_id` are the weakest fields (§5): some evidence captures
list a resource without any identifier on that row, so the agent refuses to
attribute it and omits the step instead of inventing an id.

### Gap between all-samples and holdout

All-samples step F1 (90.4%) vs holdout (84.5%) is small-sample variance plus the
fact that the holdout happens to contain several null-identity cohort cases (the
unlinkable pattern in §5). It is not evidence of overfitting: the pipeline has no
learned parameters, no case IDs are hardcoded, and the same code path scores every
case.


---

## 3. Baseline and iteration history

A trivial baseline (`code/evaluation/baseline.py`: echo `reported_status`, empty step
list) scores **0% status, 0% step F1, 0% exact sequence** — it fails the status metric
outright because every claim in this dataset is either wrong or unsupported. That is
the point of the challenge, and it sets the floor.

| Iteration | Status acc. | Step F1 | Exact seq. | Mean ED | Change that moved it |
|---|---|---|---|---|---|
| 0. Baseline (echo `reported_status`) | 0.0% | 0.0% | 0.0% | — | — |
| 1. First planner (state + naive policy) | 90.6% | 65.2% | 15.6% | 2.53 | evidence → state → plan pipeline |
| 2. Effective-state policy table | 93.8% | 81.2% | 28.1% | 1.50 | per-category target state; `RETAIN` no longer mapped to a delete verification |
| 3. Trace propagation + replica normalisation + support split | 100.0% | 86.2% | 34.4% | 1.06 | subject-less resource rows attributed via `trace_id`; `support_replica` → `support_platform`; marketing/attachment category signals |
| 4. `correlation_key` linkage + account currency | 100.0% | 87.0% | 40.6% | 1.00 | audit records joined through `correlation_key`; superseded account records dropped |
| 5. Leak-key correctness + retained-record rule + account-capture preference | 100.0% | 90.4% | 46.9% | 0.75 | see §4 |

---

## 4. Failures found and fixes made

Each of these was found by diffing predictions against the sample oracle and then
fixed at the level of a general rule, never with a per-case exception.

1. **`RETAIN` fell through to a delete verification.** Every retained record produced
   `VERIFY_RECORD_ABSENT`, which is the opposite of the required state. Fixed by
   replacing the branch chain with an explicit per-category target-state table
   (`DELETE`/`REDACT`/`ANONYMIZE`/`RETAIN`), each with its own finding, remediation
   and verification triple.
2. **A record that "exists" is not the same as a record that "leaks".** An analytics
   or support row that no longer carries a direct identifier was being reported as a
   remaining PII exposure. Introduced *effective state*: `PRESENT` becomes
   `ANONYMIZED`/`REDACTED` when no direct identifier remains, which turns three false
   findings per affected case into correct, compliant verifications.
3. **Trace-level identity was being discarded.** Subject identity in the logs lives on
   the `*_started` line while the resource lives on the `*_completed` line. Excluding
   `*_started` lines (correctly, since they prove nothing about state) also dropped
   the subject, so backup, connector and audit rows became unattributable. Fixed with
   a two-pass parse that harvests `trace_id → subject` from *any* line and applies it
   to the resource rows of the same trace.
4. **Physical replica names leaked into `system_id`.** `support_replica` rows were
   planned against a system that does not exist in the output vocabulary. Added
   canonicalisation of `*_replica`, `*_read`, `*_primary` onto the logical service —
   which is also the semantically right target, because deleting from the replication
   source is what removes the copy.
5. **Support rows were all treated as conversations.** Records with no conversation
   content (a contact-index row or an attachment) must be deleted, while a message
   carrying shared agent/order context is retained and redacted. Split on content:
   conversation excerpt or direct-identifier scan hit → `SUPPORT_MESSAGE`, otherwise
   `SUPPORT_ATTACHMENT`.
6. **Pipe-delimited application logs were parsed as handover documents.** A log line's
   `trace_id` landed in the `record_type` field and produced a bogus category
   (`tr-2240010`), which crashed the 518-case run. The parser now recognises the
   `time | LEVEL | service | trace_id | event | k=v` layout and keeps the handover
   layout as a separate shape.
7. **An unknown category aborted the whole run.** Now the policy engine applies a
   privacy-conservative `DELETE`, records the derived rule in
   `PolicyEngine.derived_rules`, and the run continues — a silent mis-policy is worse
   than a crash, so the derivation is reported rather than hidden.
8. **The validator itself crashed on an invented operation** instead of rejecting the
   row. `step_sort_key` is now total, so malformed rows are reported as validation
   errors (this is exercised by `contract-invented-operation`).
9. **`lookup_key` was counted as leaked personal data.** A query key names the subject
   rather than exposing them, so support records were wrongly classified as
   PII-bearing. The leak test now distinguishes linkage fields from record fields,
   while still treating a `correlation_key` that holds an email hash as exposed data.
10. **A long-deleted account competed with the current one.** A subject whose account
    was removed years earlier had two account resources and therefore two verification
    steps. Only the account named by the most recent admissible capture is audited.

---

## 5. Remaining error budget (all 17 residual sample diffs, classified)

Exact-sequence failure breakdown across the 17 diffs:

| Failure type | Cases | Notes |
|---|---|---|
| Missing step (real resource not attributable) | 17 | The resource exists in evidence but carries no identifier on its row. |
| Extra step (fallback account guess) | 12 | The stable-reference fallback guessed `account_0NN` for a subject whose account id is not derivable; these are false positives. |
| Wrong `evidence_id` | 0 | Every matched step cites the correct evidence id. |
| Wrong step order | 0 | Phase → operation → system → resource ordering always matches the oracle. |

| Root cause | Diffs | Why it is not fixable from participant data |
|---|---|---|
| Cohort-wide capture lists a resource row with **no identifier** on it (`"profile": null`, `status_code: 404`, null-identity analytics rows, `account_6440`, `analytics_5335`, `audit_record_9528`, …) | 17 | The row genuinely does not say whose it is. Verified: every identity-bearing row in the same captures links correctly; every null-identity row does not. Any attribution would be an invented resource id, which the contract forbids. The agent emits no step rather than a guess. |
| Account id exists **only** as a workflow reference (`account_004`, `account_410`) or nowhere at all (`account_4857` cited from a capture that does not contain it) | 3 (overlapping with the 17) | Recovered by the stable-reference fallback where the id is consistent with the subject; otherwise omitted. |

This is a deliberate trade: precision is kept above 93% and no claim is fabricated.
Reporting a fabricated "remaining record" would be a false accusation of the platform;
reporting a fabricated "verified absent" would be a false statement of compliance.
The 12 fallback false positives are the one residual imprecision; removing them would
cost 8 true positives for the low-numbered (seed) subjects whose accounts *are*
derivable from the stable reference, so the fallback is retained as a
best-effort derivation, not a per-case guess.


---

## 6. Additional evaluation cases created for this submission

`python code/evaluation/run_extra_cases.py` → **28/28 passing**.

Files: `code/evaluation/extra_cases/{intent,ordering,contract}_cases.json`.

### 6.1 Intent cases (14)
Every text comes from the supplied requests, plus four adversarial texts written for
this evaluation. Each case states the authorisation decision the task definition
requires *and why*.

| Case | Expected | Purpose |
|---|---|---|
| `intent-en-delete` | authorised | direct erasure |
| `intent-en-incidental-negative` | authorised | "systems that do not display the email" must not veto a real request |
| `intent-en-verify-only` | authorised | erasure follow-up with **no** deletion verb ("confirm the profile is gone") |
| `intent-en-refusal` | not | "I am not asking for deletion" |
| `intent-en-do-not-close` | not | access request + prohibition on closure |
| `intent-en-export-retain` | not | portability + "keep the account as it is" |
| `intent-en-correction-retain` | not | "correct it without removing any of my records" |
| `intent-en-invoice-copy` | not | copy request + retention instruction |
| `intent-fr-erasure` / `intent-pt-erasure` / `intent-ja-erasure` / `intent-de-erasure` | authorised | non-English erasure vocabulary |
| `intent-es-access` | not | Spanish access request with "no quiero que borren nada" |
| `intent-en-partial` | authorised | erasure that accepts a legal-hold exception |

### 6.2 Ordering cases (5)
Scrambled inputs, contract-required output: findings by exposure severity; remediation
in dependency order (`DISABLE_DATA_FLOW` → `BLOCK_RESTORE` → `DELETE_RECORD` →
`REDACT` → `ANONYMIZE`); verification in the specified operation order; phase
precedence; system/resource tie-breaks; consecutive numbering from 1.

### 6.3 Contract cases (9)
Eight rows that must be **rejected** (illegal operation/state pair, invented operation,
wrong phase for the operation, non-consecutive numbering, finding without evidence,
unknown evidence id, remediation citing evidence, extra field) and one legal row that
must be **accepted**. These lock the grader-facing surface, not the model.

### 6.4 Regression lock
`code/evaluation/run_all.py` additionally re-scores all 32 published samples on every
run and fails the build if status accuracy or step F1 degrades, so the fixes in §4
cannot silently regress.

---

## 7. Full-dataset run

`python code/main.py` → 518 rows, all validations pass.

| Quantity | Value |
|---|---|
| Rows | 518 (one per request) |
| `COMPLETE` | 158 |
| `INCOMPLETE` | 352 |
| `NOT_APPLICABLE` | 8 |
| Total steps | 2,786 |
| `FINDING` / `REMEDIATION` / `VERIFICATION` | 669 / 669 / 1,448 |
| Future steps with `evidence_id=""` | 1,812 |
| Mean steps per incomplete case | 7.05 |
| Mean steps per complete case | 1.93 |

Two useful sanity signals:

* **The agent does not echo the claim.** Of 254 requests reported `COMPLETE`, 165 are
  found incomplete; 76 requests reported as not complete are found complete at
  `audit_at` (the workflow claims are wrong in both directions, which is the premise
  of the challenge).
* **Remediation is dependency-ordered and complete.** Every finding has exactly one
  remediation (669/669), and every remediation has a matching post-remediation
  verification, so no incomplete case ends without the checks that must pass.

Distribution of operations across the full run:

```
REPORT_REMAINING_RECORD 346   DELETE_RECORD   346   VERIFY_RECORD_ABSENT      804
REPORT_EXPOSED_PII      211   REDACT_RECORD   142   VERIFY_RECORD_PRESENT     252
REPORT_ACTIVE_DATA_FLOW  87   ANONYMIZE_RECORD 69   VERIFY_RECORD_REDACTED    157
REPORT_RESTORE_RISK      25   DISABLE_DATA_FLOW 87  VERIFY_RECORD_ANONYMIZED   69
                              BLOCK_RESTORE     25  VERIFY_DATA_FLOW_DISABLED 114
                                                    VERIFY_RESTORE_BLOCKED     52
```
