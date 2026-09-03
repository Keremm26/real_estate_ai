# Retrieval evaluation — normative RAG

**Scope:** IT / student_housing · embed `bge-m3` · 20 gold queries · 184 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.675 | 0.950 | 0.950 | 1.000 |
| hit@k | 0.700 | 0.950 | 0.950 | 1.000 |
| nDCG | 0.700 | 0.851 | 0.851 | 0.868 |
| precision* | 0.700 | 0.333 | 0.200 | 0.131 |

**MRR = 0.824**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 184 in-scope chunks) | 96,828 |
| rag (top-8) | 8,395 |

**91.3% smaller context with RAG.**
