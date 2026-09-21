# Downstream evaluation — regulatory agent under three retrieval arms

**Scope:** IT / student_housing · 12 production-shaped queries · 1 repeat(s) · extraction LLM per `.env` · retrieval flags: framing=False, quant_only=True, top_k=8

## Does the agent produce a usable, correct, grounded constraint?

| metric | none | rag |
|---|---|---|
| activation (expected-true queries) | 0% | 88% |
| false activation (negative controls) ↓ | 0% | 0% |
| activation on out-of-corpus use cases | 0% | 0% |
| value inside standard-derived range | 0% | 100% |
| target column correct | — | 100% |
| operator correct | — | 100% |
| doc grounding (cited doc was in context) | — | 100% |
| value grounding (quoted figures in context) | — | 100% |

## Cost

| metric | none | rag |
|---|---|---|
| ~context tokens / query | 0 | 7,275 |
| latency s / query | 1 | 3 |
| requirements produced (total) | 0 | 8 |

Notes: *none* is the pre-RAG production state (no documents reach the agent), so its grounding is
structurally 0 — any requirement it emits comes from model priors. *dump* holds the jurisdiction scope
constant and removes only the semantic ranking. Ranges follow the DM 1256/2021 All. A reading stated in
`downstream_queries.json` and are author-constructed.
