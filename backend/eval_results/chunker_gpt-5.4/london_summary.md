# Retrieval evaluation — normative RAG

**Scope:** UK / student_housing · embed `bge-m3` · 12 gold queries · 1255 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 1.000 | 1.000 | 1.000 | 1.000 |
| hit@k | 1.000 | 1.000 | 1.000 | 1.000 |
| nDCG | 1.000 | 1.000 | 1.000 | 1.000 |
| precision* | 1.000 | 0.333 | 0.200 | 0.125 |

**MRR = 1.000**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 1255 in-scope chunks) | 461,913 |
| rag (top-8) | 6,908 |

**98.5% smaller context with RAG.**
