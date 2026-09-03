# Retrieval evaluation — normative RAG

**Scope:** ES / student_housing · embed `bge-m3` · 12 gold queries · 591 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.833 | 1.000 | 1.000 | 1.000 |
| hit@k | 0.833 | 1.000 | 1.000 | 1.000 |
| nDCG | 0.833 | 0.928 | 0.928 | 0.928 |
| precision* | 0.833 | 0.333 | 0.200 | 0.125 |

**MRR = 0.903**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 591 in-scope chunks) | 731,327 |
| rag (top-8) | 17,197 |

**97.6% smaller context with RAG.**
