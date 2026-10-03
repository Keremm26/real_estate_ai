# Retrieval evaluation — normative RAG

**Scope:** IT / student_housing · embed `bge-m3` · 20 gold queries · 1987 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.725 | 0.950 | 0.950 | 0.950 |
| hit@k | 0.750 | 0.950 | 0.950 | 0.950 |
| nDCG | 0.750 | 0.859 | 0.859 | 0.859 |
| precision* | 0.750 | 0.333 | 0.200 | 0.125 |

**MRR = 0.833**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 1987 in-scope chunks) | 499,239 |
| rag (top-8) | 3,720 |

**99.3% smaller context with RAG.**
