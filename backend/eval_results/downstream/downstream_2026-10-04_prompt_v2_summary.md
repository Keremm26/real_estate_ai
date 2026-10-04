# Downstream evaluation — regulatory agent, none vs rag

**Scope:** IT · 43 items × 2 language(s) (it, en) · 1 repeat(s) · routing=router · extraction LLM = agents' model · retrieval: quant_only=True, top_k=8

| metric | none | rag |
|---|---|---|
| right outcome (activate / decline) | 44% | 83% |
| activation (expected-true items) | 0% | 92% |
| correct decline (expected-false items) | 100% | 68% |
| 'either' items handled correctly | 100% | 100% |
| value inside expected range | 0% | 70% |
| echo of a user-stated number ↓ | — | 0% |
| target column correct | — | 100% |
| operator correct | — | 100% |
| source article in retrieved context | 0% | 83% |
| precedence winner cited | 0% | 50% |
| router use case correct | — | 99% |
| main requirement cites a retrieved doc | — | 93% |
| quoted figures found in retrieved docs | — | 96% |
| ~context tokens / run | 0 | 3,036 |
| latency s / run | 1 | 2 |

## Right outcome by lang

| lang | none | rag |
|---|---|---|
| en | 44% | 74% |
| it | 44% | 91% |

## Right outcome by type

| type | none | rag |
|---|---|---|
| accessibility | 0% | 100% |
| ambiguous_use | 100% | 100% |
| code_switching | 0% | 100% |
| derogation | 0% | 75% |
| dimensioning | 0% | 100% |
| echo_trap | 67% | 83% |
| general_dwelling | 0% | 83% |
| long_query | 50% | 50% |
| mixed_use | 0% | 0% |
| negation | 100% | 0% |
| negative | 100% | 100% |
| no_capacity | 100% | 50% |
| non_surface_rule | 100% | 75% |
| number_in_words | 0% | 100% |
| out_of_corpus | 100% | 50% |
| precedence | 0% | 100% |
| procedural | 100% | 100% |
| retrieval_probe | 100% | 100% |
| room_mix | 0% | 100% |
| self_correction | 0% | 100% |
| service_rate | 0% | 100% |
| studio_minimum | 0% | 100% |
| typos | 0% | 100% |

Notes: *none* is the pre-RAG production state (no documents reach the agent): any requirement it
emits comes from model priors, so its grounding is structurally 0. Items and ranges are defined in
`downstream_queries_turin.json` (author-constructed; see its meta.caveats).
