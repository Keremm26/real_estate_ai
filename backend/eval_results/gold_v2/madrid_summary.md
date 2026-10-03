# Retrieval evaluation — normative RAG

**Scope:** ES / student_housing · embed `bge-m3` · 40 gold items / 80 runs · 2020 chunks in scope

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.425 | 0.650 | 0.775 | 0.854 |
| hit@k | 0.525 | 0.662 | 0.775 | 0.863 |
| nDCG | 0.525 | 0.588 | 0.641 | 0.669 |
| precision* | 0.525 | 0.279 | 0.195 | 0.133 |

**MRR = 0.623**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by lang

| lang | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| en | 40 | 0.400 | 0.562 | 0.725 | 0.800 | 0.583 |
| es | 40 | 0.450 | 0.738 | 0.825 | 0.908 | 0.663 |

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 4 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |
| factual | 64 | 0.422 | 0.609 | 0.750 | 0.844 | 0.557 |
| lex_specialis | 2 | 0.500 | 0.750 | 1.000 | 1.000 | 1.000 |
| multi_article | 10 | 0.400 | 0.750 | 0.800 | 0.833 | 0.817 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 38 | 0.500 | 0.763 | 0.895 | 1.000 | 0.774 |
| national | 24 | 0.333 | 0.500 | 0.708 | 0.764 | 0.476 |
| regional | 18 | 0.389 | 0.611 | 0.611 | 0.667 | 0.499 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 4 | 1.000 | 0.500 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 2020 in-scope chunks) | 919,134 |
| rag (top-8) | 6,896 |

**99.2% smaller context with RAG.**
