# Retrieval evaluation — normative RAG

**Scope:** IT / student_housing · embed `bge-m3` · 42 gold items / 84 runs · 2001 chunks in scope

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.597 | 0.843 | 0.901 | 0.935 |
| hit@k | 0.798 | 0.952 | 0.964 | 0.976 |
| nDCG | 0.798 | 0.802 | 0.831 | 0.844 |
| precision* | 0.798 | 0.401 | 0.262 | 0.171 |

**MRR = 0.866**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by lang

| lang | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| en | 42 | 0.532 | 0.849 | 0.921 | 0.940 | 0.831 |
| it | 42 | 0.663 | 0.837 | 0.881 | 0.929 | 0.900 |

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 16 | 0.448 | 0.677 | 0.729 | 0.812 | 0.958 |
| factual | 42 | 0.714 | 0.905 | 0.929 | 0.952 | 0.795 |
| lex_specialis | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_article | 16 | 0.469 | 0.812 | 1.000 | 1.000 | 0.969 |
| temporal | 6 | 0.250 | 0.833 | 0.833 | 0.917 | 0.750 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 24 | 0.542 | 0.854 | 0.875 | 0.938 | 0.769 |
| national | 40 | 0.637 | 0.850 | 0.950 | 0.950 | 0.869 |
| regional | 20 | 0.583 | 0.817 | 0.833 | 0.900 | 0.975 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 22 | 0.682 | 0.955 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 2001 in-scope chunks) | 511,916 |
| rag (top-8) | 3,111 |

**99.4% smaller context with RAG.**
