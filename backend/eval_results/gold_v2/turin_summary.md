# Retrieval evaluation — normative RAG

**Scope:** IT / student_housing · embed `bge-m3` · 42 gold items / 84 runs · 1944 chunks in scope

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.609 | 0.831 | 0.901 | 0.935 |
| hit@k | 0.810 | 0.940 | 0.964 | 0.976 |
| nDCG | 0.810 | 0.802 | 0.836 | 0.849 |
| precision* | 0.810 | 0.397 | 0.262 | 0.171 |

**MRR = 0.873**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by lang

| lang | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| en | 42 | 0.556 | 0.849 | 0.921 | 0.940 | 0.843 |
| it | 42 | 0.663 | 0.813 | 0.881 | 0.929 | 0.902 |

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 16 | 0.448 | 0.677 | 0.729 | 0.812 | 0.969 |
| factual | 42 | 0.738 | 0.881 | 0.929 | 0.952 | 0.805 |
| lex_specialis | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_article | 16 | 0.469 | 0.812 | 1.000 | 1.000 | 0.969 |
| temporal | 6 | 0.250 | 0.833 | 0.833 | 0.917 | 0.750 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 24 | 0.583 | 0.854 | 0.875 | 0.938 | 0.797 |
| national | 40 | 0.637 | 0.825 | 0.950 | 0.950 | 0.867 |
| regional | 20 | 0.583 | 0.817 | 0.833 | 0.900 | 0.975 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 22 | 0.682 | 0.955 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 1944 in-scope chunks) | 506,584 |
| rag (top-8) | 3,163 |

**99.4% smaller context with RAG.**
