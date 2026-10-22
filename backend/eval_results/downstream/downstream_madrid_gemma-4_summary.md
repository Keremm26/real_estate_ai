# Downstream evaluation — regulatory agent, none vs rag

**Scope:** ES · 40 items × 2 language(s) (es, en) · 3 repeat(s) · routing=router · extraction LLM = agents' model · retrieval: quant_only=True, top_k=8

| metric | none | rag |
|---|---|---|
| right outcome (activate / decline) | 42% | 92% |
| activation (expected-true items) | 0% | 96% |
| correct decline (expected-false items) | 100% | 89% |
| 'either' items handled correctly | 100% | 88% |
| value inside expected range | 0% | 55% |
| echo of a user-stated number ↓ | — | 0% |
| target column correct | — | 100% |
| operator correct | — | 100% |
| source article in retrieved context | 0% | 88% |
| precedence winner cited | — | — |
| router use case correct | — | 99% |
| main requirement cites a retrieved doc | — | 98% |
| quoted figures found in retrieved docs | — | 100% |
| ~context tokens / run | 0 | 7,596 |
| latency s / run | 1 | 16 |
| identical value across repeats | 100% | 81% |

## Right outcome by lang

| lang | none | rag |
|---|---|---|
| en | 42% | 90% |
| es | 42% | 95% |

## Right outcome by type

| type | none | rag |
|---|---|---|
| ambiguous_use | 100% | 67% |
| code_switching | 0% | 50% |
| dimensioning | 0% | 100% |
| echo_trap | 0% | 100% |
| general_dwelling | 0% | 100% |
| long_query | 0% | 100% |
| negation | 0% | 100% |
| negative | 100% | 100% |
| no_capacity | 100% | 100% |
| non_surface_rule | 100% | 90% |
| number_in_words | 0% | 100% |
| out_of_corpus | 100% | 50% |
| precedence | 0% | 100% |
| procedural | 100% | 100% |
| room_mix | 0% | 100% |
| self_correction | 0% | 100% |
| studio_minimum | 0% | 100% |
| typos | 0% | 50% |

Notes: *none* is the pre-RAG production state (no documents reach the agent): any requirement it
emits comes from model priors, so its grounding is structurally 0. Items and ranges are defined in
`downstream_queries_madrid.json` (author-constructed; see its meta.caveats).
