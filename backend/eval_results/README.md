# Chunk-detector ablation — gpt-5.4 vs gemma-4 (2026-09-22)

Same corpus (3 cities, 24 documents), same embedder (bge-m3), same retriever and
gold sets. The only difference is which model detected each document's section
structure at ingest (`RAG_CHUNK_MODEL`); specs are cached per model as
`data/rag/raw/<doc>.<model>.chunkspec.json`, so both stores are reproducible.

Matching is on the bare identifier of the top-level unit (`eval/run.py::_ident`:
'Art. 3' / '3' / 'Articolo 3' all match), so a detector that captures the number
without the unit word is not penalised. The gpt-5.4 numbers are invariant under
this rule (verified: no gold label changes match status).

| city (gold set) | chunks gpt-5.4 → gemma-4 | recall@1 | recall@3 | recall@8 | MRR | nDCG@8 |
|---|---|---|---|---|---|---|
| Turin (20 q, article-level) | 1987 → 1944 | .725 → .625 | .950 → .850 | .950 → .950 | .833 → .758 | .859 → .806 |
| London (12 q, doc-level) | 1255 → 709 | 1.000 → .833 | 1.000 → .917 | 1.000 → .917 | 1.000 → .875 | 1.000 → .886 |
| Madrid (12 q, doc-level) | 2305 → 2020 | .917 → .833 | 1.000 → 1.000 | 1.000 → 1.000 | .958 → .917 | .969 → .938 |

## What actually differs (from inspecting every regressed query)

Real detector defects (gemma-4):
- **GPDO 2015 Sch. 2 Pt. 3** — root regex `^\[?F\d*Class\s+([A-Z]{1,2})` is anchored to
  line start, but legislation.gov.uk prints amendment annotations inline before some
  class headings; only 8 of 28 classes became units, Class L was swallowed into Class I
  (uk04 miss).
- **DM 236/1989** — gemma chose the numbered sections (`8.0.2`, `8.1.11`, …) as the ROOT
  unit instead of `Art. N`, so the article never exists as a top-level ref (q16 miss);
  the hierarchy is inverted relative to the legal structure.
- **TH Local Plan 2031** — paragraph-level root (430 units vs 37) floods top-k with
  short supporting-text paragraphs (uk05 rank 1 → 2, uk03 rank 1 → 3 by similar noise).
- **London Plan 2021** — coarser than gpt-5.4 (174 vs ~1000 units).

Not detector defects:
- q04/q05/q06 (DM Sanità Art. 2 rank ↓): the municipal Reg. 381 `Art. 77 > 77.2`
  (9 / 14 m² rule) now outranks it — a correct answer the gold set does not label.
  Gold sets predate the Turin regional/municipal tiers; see the pending gold-set rework.

Where gemma-4 was better:
- **DPR 380/2001** — nesting articolo > comma > lettera (gpt-5.4 had lettera above comma).
- **DM 1256 Allegato A** — 26 clean `§ N.N` sections (gpt-5.4 had produced junk refs here).

Files: `chunker_gpt-5.4/<city>.json|_summary.md`, `chunker_gemma-4/<city>.json|_summary.md`.
Store at time of writing = gemma-4 chunking (4673 chunks). To rebuild the gpt-5.4 store:
`RAG_CHUNK_MODEL=gpt-5.4 python -m app.services.llm.rag.ingest --city <city>` (specs cached, embed only).
