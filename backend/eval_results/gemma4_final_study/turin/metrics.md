# RAG study — full pipeline, none vs rag (gemma-4)

Queries with results: {'none': 50, 'rag': 50} · generated 2026-10-22T12:09:16

## Regulatory output inside the pipeline (all trials)

| metric | none | rag |
|---|---|---|
| right regulatory outcome | 44% | 98% |
| activation (expected-true) | 0% | 100% |
| correct decline (expected-false) | 100% | 94% |
| value inside expected range | 0% | 79% |
| echo of a user-stated number ↓ | — | 0% |
| source article retrieved | 0% | 89% |
| precedence winner cited | 0% | 0% |
| citation names a retrieved doc | — | 100% |
| agent activation as expected | 42% | 88% |
| runs with zero results | 0% | 0% |
| mean latency (s) | 498.0 | 530.3 |

## Top-k legal compliance (activate=true items, mean over trials, paired per query)

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 28 | 80% | 99% | +19.0 pp | 11 / 17 / 0 | 0.001 |
| @3 | 28 | 88% | 100% | +11.9 pp | 4 / 24 / 0 | 0.125 |

## Ranking stability where no rule applies (activate=false items)

Top-10 IoU none vs rag: **1.000** (n=18) · noise floor (same arm, trial vs trial): none 1.000, rag 1.000

A cross-arm IoU close to the noise floor means retrieval does not perturb rankings when no rule applies.

## Fallback trials (SQL matched nothing → the 500 nearest buildings, all constraints dropped)

| | none | rag |
|---|---|---|
| fallback trials | 3/150 (2%) | 3/150 (2%) |
| on rule items (activate=true) | 0% | 0% |
| on no-rule items (activate=false) | 6% | 6% |
| judge's representative trial is a fallback | 2% | 2% |

### Compliance with fallback trials counted as no answer

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 28 | 80% | 99% | +19.0 pp | 11 / 17 / 0 | 0.001 |
| @3 | 28 | 88% | 100% | +11.9 pp | 4 / 24 / 0 | 0.125 |

### Stability with fallback trials excluded

Top-10 IoU none vs rag: **1.000** (n=17) · noise floor: none 1.000, rag 1.000
