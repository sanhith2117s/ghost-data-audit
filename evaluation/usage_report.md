# Usage report

Scope: the final full-dataset run that produced `output.csv` (518 requests), plus the
one-time, out-of-band preparation of the screenshot transcription cache.

---

## Section A — final full-dataset run (the run that produced `output.csv`)

```bash
python code/main.py     # dataset/deletion_requests.csv -> output.csv
```

| Item | Value |
|---|---|
| Model provider | none (no model is called) |
| Model name | none |
| Model-call count | **0** |
| Input tokens | **0** |
| Output tokens | **0** |
| Total tokens | **0** |
| Average tokens per request | **0.0** over 518 requests |
| Estimated total cost | **$0.00** |
| Estimated cost per request | **$0.00** |
| Wall-clock time | ≈ 41 s for all 518 cases on a 4-core laptop |
| Network access | none |

The submitted agent is a deterministic, standard-library Python program: intent
classification, identifier resolution, evidence extraction, state reconstruction,
policy application (read from `dataset/deletion_rules.csv` at runtime) and plan
assembly are all rule-based. Nothing about a case is sent to a model at prediction
time, so the graded run has no token usage, no rate limits, no non-determinism and
no credential dependency.

Screenshots are handled through a cached transcription
(`code/evidence_cache/images.json`); the two images without a cache entry are
reported as extraction gaps rather than transcribed live. Cache hits make zero
model calls, which is why this run's totals are all zero.

---

## Section B — build-time vision transcription (one-time, out-of-band)

Eleven evidence artefacts are screenshots, which the standard library cannot parse.
**All 11 images were read by the development model (`deepseek-v4.1-flash-free`,
provider `opencode`) directly in the IDE during development.** Their contents were
transcribed once into `code/evidence_cache/images.json`, keyed by the SHA-256 of
each image. Token counts for those reads were not recorded (they are part of
ordinary conversation traffic), so no per-image token figure is claimed here.

| Item | Value |
|---|---|
| How transcription was done | The conversation model (`deepseek-v4.1-flash-free`, provider `opencode`) read the images directly as part of normal interaction |
| Separate vision API calls | **0** (no separate vision endpoint was called) |
| Provider | `opencode` |
| Model name | `deepseek-v4.1-flash-free` (DeepSeek V4.1 Flash Free) |
| Input tokens | not instrumented separately (part of conversation traffic) |
| Output tokens | not instrumented separately |
| Average tokens per request (518 requests) | **0** at run time; the transcription is a build-time cache |
| Estimated cost | **$0.00** at submission time (cached by content hash; never re-requested) |
| Re-run behaviour | a screenshot with a changed hash invalidates its entry and is reported as an extraction gap, never silently mis-read |

### Why "0 calls" is honest

The harness that ran this agent did not expose a separate vision API, and no
provider API key was present. The images were read by the conversation model in the
course of the session; those reads are part of ordinary conversation traffic and
are not separately countable as "vision calls." The cache therefore represents a
one-time human-plus-model reading, not an instrumented batch of API calls. Any
per-call token figure for this step would be a guess, so none is claimed.

If a provider key is configured, `code/tools/build_vision_cache.py` can regenerate
the cache live (idempotent, skips cached hashes); it logs each image it would
transcribe. Instrumentation for real input/output token counts can be added at the
router call site in `transcribe_image_live`, but no such router is wired in this
submission.

### Cost estimate assumptions (for the record only)

Should a live regeneration ever be run, the estimate below uses the assumptions
declared in `config/models.yaml`. These are assumptions, not measurements:

| Assumption | Value |
|---|---|
| Estimated input tokens per image | 900 |
| Estimated output tokens per image | 400 |
| Images | 11 |
| Estimated total input tokens | 9,900 |
| Estimated total output tokens | 4,400 |
| Estimated total tokens | 14,300 |
| Input price (per 1M tokens) | $0.00 (placeholder) |
| Output price (per 1M tokens) | $0.00 (placeholder) |
| Estimated total cost | $0.00 (at these placeholder prices) |

Update `config/models.yaml` with the provider's real pricing before using the
estimate for budgeting.

---

## Section C — cache-builder instrumentation

No cache-builder run with instrumentation was performed in this submission (no
provider key was available, and no router endpoint is wired), so there are no
measured input/output tokens or cost to report here. `code/tools/build_vision_cache.py`
prints each image it transcribes to stderr and is the place to add real token
logging (around the `transcribe_image_live` router call) when a provider is
configured.

| Item | Value |
|---|---|
| Instrumented cache-builder runs | 0 |
| Measured input tokens | n/a |
| Measured output tokens | n/a |
| Measured cost | n/a |

---

## Design choice

The pipeline is deterministic and uses a model only for non-parseable evidence
(screenshots), with results cached by content hash. This keeps every case decision
auditable against a named artefact, makes the graded run reproducible from a clean
checkout with zero model calls, and ensures that a changed screenshot invalidates
its cache entry by hash rather than being silently mis-read.

## Secrets

No credentials, tokens or keys are included in this archive or required to run it.
Configuration is environment-only and optional, as documented in `.env.example`.
