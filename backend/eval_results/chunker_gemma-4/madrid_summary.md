# Retrieval evaluation — normative RAG

**Scope:** ES / student_housing · embed `bge-m3` · 12 gold queries · 2020 chunks in scope

## Overall retrieval quality (mean over queries)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.833 | 1.000 | 1.000 | 1.000 |
| hit@k | 0.833 | 1.000 | 1.000 | 1.000 |
| nDCG | 0.833 | 0.938 | 0.938 | 0.938 |
| precision* | 0.833 | 0.333 | 0.200 | 0.125 |

**MRR = 0.917**

\* precision is capped at 1/k for single-relevant queries — reported, not headline.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 2020 in-scope chunks) | 906,004 |
| rag (top-8) | 8,037 |

**99.1% smaller context with RAG.**
