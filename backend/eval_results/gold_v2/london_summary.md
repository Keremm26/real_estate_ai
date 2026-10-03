# Retrieval evaluation — normative RAG

**Scope:** UK / student_housing · embed `bge-m3` · 40 gold items / 40 runs · 933 chunks in scope

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.613 | 0.800 | 0.875 | 0.963 |
| hit@k | 0.675 | 0.825 | 0.900 | 0.975 |
| nDCG | 0.675 | 0.751 | 0.783 | 0.814 |
| precision* | 0.675 | 0.317 | 0.205 | 0.141 |

**MRR = 0.779**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |
| factual | 31 | 0.677 | 0.806 | 0.903 | 0.968 | 0.775 |
| lex_specialis | 1 | 0.500 | 0.500 | 0.500 | 0.500 | 1.000 |
| multi_article | 4 | 0.250 | 0.875 | 0.875 | 1.000 | 0.750 |
| temporal | 2 | 0.500 | 0.500 | 0.500 | 1.000 | 0.583 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 12 | 0.625 | 0.792 | 0.958 | 0.958 | 0.829 |
| national | 20 | 0.650 | 0.750 | 0.800 | 0.950 | 0.736 |
| regional | 8 | 0.500 | 0.938 | 0.938 | 1.000 | 0.812 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 5 | 0.400 | 0.800 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 933 in-scope chunks) | 452,458 |
| rag (top-8) | 5,475 |

**98.8% smaller context with RAG.**
