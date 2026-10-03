# Retrieval evaluation — normative RAG

**Scope:** ES / student_housing · embed `bge-m3` · 12 gold queries · 2305 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.917 | 1.000 | 1.000 | 1.000 |
| hit@k | 0.917 | 1.000 | 1.000 | 1.000 |
| nDCG | 0.917 | 0.969 | 0.969 | 0.969 |
| precision* | 0.917 | 0.333 | 0.200 | 0.125 |

**MRR = 0.958**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 2305 in-scope chunks) | 920,696 |
| rag (top-8) | 7,249 |

**99.2% smaller context with RAG.**
