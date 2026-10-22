# RAG study — full pipeline, none vs rag (gemma-4)

Queries with results: {'none': 40, 'rag': 40} · generated 2026-10-22T12:09:20

## Regulatory output inside the pipeline (all trials)

| metric | none | rag |
|---|---|---|
| right regulatory outcome | 40% | 90% |
| activation (expected-true) | 0% | 92% |
| correct decline (expected-false) | 100% | 85% |
| value inside expected range | 0% | 58% |
| echo of a user-stated number ↓ | — | 0% |
| source article retrieved | 0% | 70% |
| precedence winner cited | 0% | 100% |
| citation names a retrieved doc | — | 83% |
| agent activation as expected | 40% | 85% |
| runs with zero results | 0% | 0% |
| mean latency (s) | 420.3 | 543.0 |

## Top-k legal compliance (activate=true items, mean over trials, paired per query)

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 24 | 88% | 100% | +12.4 pp | 5 / 19 / 0 | 0.062 |
| @3 | 24 | 90% | 100% | +10.2 pp | 3 / 21 / 0 | 0.250 |

## Ranking stability where no rule applies (activate=false items)

Top-10 IoU none vs rag: **0.907** (n=13) · noise floor (same arm, trial vs trial): none 0.974, rag 0.991

A cross-arm IoU close to the noise floor means retrieval does not perturb rankings when no rule applies.

## Fallback trials (SQL matched nothing → the 500 nearest buildings, all constraints dropped)

| | none | rag |
|---|---|---|
| fallback trials | 0/120 (0%) | 0/120 (0%) |
| on rule items (activate=true) | 0% | 0% |
| on no-rule items (activate=false) | 0% | 0% |
| judge's representative trial is a fallback | 0% | 0% |

### Compliance with fallback trials counted as no answer

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 24 | 88% | 100% | +12.4 pp | 5 / 19 / 0 | 0.062 |
| @3 | 24 | 90% | 100% | +10.2 pp | 3 / 21 / 0 | 0.250 |

### Stability with fallback trials excluded

Top-10 IoU none vs rag: **0.907** (n=13) · noise floor: none 0.974, rag 0.991
