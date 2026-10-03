# Retrieval evaluation — normative RAG

**Scope:** UK / student_housing · embed `bge-m3` · 12 gold queries · 709 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.833 | 0.917 | 0.917 | 0.917 |
| hit@k | 0.833 | 0.917 | 0.917 | 0.917 |
| nDCG | 0.833 | 0.886 | 0.886 | 0.886 |
| precision* | 0.833 | 0.306 | 0.183 | 0.115 |

**MRR = 0.875**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 709 in-scope chunks) | 402,179 |
| rag (top-8) | 5,657 |

**98.6% smaller context with RAG.**
