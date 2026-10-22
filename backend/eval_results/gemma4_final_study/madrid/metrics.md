# RAG study — full pipeline, none vs rag (gemma-4)

Queries with results: {'none': 40, 'rag': 40} · generated 2026-10-22T12:09:23

## Regulatory output inside the pipeline (all trials)

| metric | none | rag |
|---|---|---|
| right regulatory outcome | 38% | 93% |
| activation (expected-true) | 0% | 95% |
| correct decline (expected-false) | 100% | 93% |
| value inside expected range | 0% | 55% |
| echo of a user-stated number ↓ | — | 20% |
| source article retrieved | 0% | 93% |
| precedence winner cited | — | — |
| citation names a retrieved doc | — | 96% |
| agent activation as expected | 32% | 86% |
| runs with zero results | 0% | 0% |
| mean latency (s) | 385.7 | 478.5 |

## Top-k legal compliance (activate=true items, mean over trials, paired per query)

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 25 | 73% | 91% | +18.4 pp | 6 / 18 / 1 | 0.031 |
| @3 | 25 | 73% | 91% | +17.8 pp | 6 / 19 / 0 | 0.031 |

## Ranking stability where no rule applies (activate=false items)

Top-10 IoU none vs rag: **1.000** (n=5) · noise floor (same arm, trial vs trial): none 1.000, rag 1.000

A cross-arm IoU close to the noise floor means retrieval does not perturb rankings when no rule applies.

## Fallback trials (SQL matched nothing → the 500 nearest buildings, all constraints dropped)

| | none | rag |
|---|---|---|
| fallback trials | 3/120 (2%) | 6/120 (5%) |
| on rule items (activate=true) | 4% | 8% |
| on no-rule items (activate=false) | 0% | 0% |
| judge's representative trial is a fallback | 2% | 5% |

### Compliance with fallback trials counted as no answer

| k | n | none | rag | mean diff | rag better / tie / none better | Wilcoxon p |
|---|---|---|---|---|---|---|
| @10 | 23 | 74% | 90% | +16.5 pp | 5 / 17 / 1 | 0.062 |
| @3 | 23 | 74% | 90% | +16.4 pp | 5 / 18 / 0 | 0.062 |

### Stability with fallback trials excluded

Top-10 IoU none vs rag: **1.000** (n=5) · noise floor: none 1.000, rag 1.000
