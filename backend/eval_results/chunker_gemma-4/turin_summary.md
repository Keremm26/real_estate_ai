# Retrieval evaluation — normative RAG

**Scope:** IT / student_housing · embed `bge-m3` · 20 gold queries · 1944 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.625 | 0.850 | 0.950 | 0.950 |
| hit@k | 0.650 | 0.850 | 0.950 | 0.950 |
| nDCG | 0.650 | 0.763 | 0.806 | 0.806 |
| precision* | 0.650 | 0.300 | 0.200 | 0.125 |

**MRR = 0.758**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 1944 in-scope chunks) | 493,948 |
| rag (top-8) | 2,926 |

**99.4% smaller context with RAG.**
