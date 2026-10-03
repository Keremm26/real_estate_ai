# Retrieval evaluation — gold sets v2 (2026-09-22)

Reworked gold sets: every item is one information need with one label set, asked
in English **and** in the city's language (London is English-only), typed by what
it tests and by the tier of the prevailing answer. Conflict and temporal items
name both the prevailing and the overridden rule, so ranking order can be scored
directly.

Store at time of writing: **gemma-4 chunking, 5,003 chunks** (`RAG_CHUNK_MODEL=gemma-4`).
Embedder bge-m3, retrieval depth 8. These numbers are **not comparable** to
`../chunker_*/` — those ran the 20/12/12-item v1 sets with doc-level labels.

| city | items | runs | chunks in scope | recall@1 | recall@3 | recall@8 | MRR | nDCG@8 |
|---|---|---|---|---|---|---|---|---|
| Turin (IT, bilingual) | 42 | 84 | 1,944 | .609 | .831 | .935 | **.873** | .849 |
| London (UK, EN only) | 40 | 40 | 933 | .613 | .800 | .963 | **.779** | .814 |
| Madrid (ES, bilingual) | 40 | 80 | 2,020 | .425 | .650 | .854 | **.623** | .669 |

## Same-language vs cross-language (paired: identical needs, two phrasings)

| city | EN MRR | local MRR | EN recall@8 | local recall@8 |
|---|---|---|---|---|
| Turin | .843 | **.902** (it) | .940 | .929 |
| Madrid | .583 | **.663** (es) | .800 | .908 |

bge-m3 retrieves better in the source language in both cities, on the same 42/40
information needs. The gap is far larger in Madrid, where the misses are mostly
English queries about procedural articles of Ley 9/2001 (es26, es29, es30, es32
all miss in English and land at rank 1-3 in Spanish).

## By item type (MRR)

| city | factual | multi_article | lex_specialis | conflict | temporal |
|---|---|---|---|---|---|
| Turin | .805 (42) | .969 (16) | 1.000 (4) | .969 (16) | .750 (6) |
| London | .775 (31) | .750 (4) | 1.000 (1) | 1.000 (2) | .583 (2) |
| Madrid | .557 (64) | .817 (10) | 1.000 (2) | 1.000 (4) | — |

Counts are runs, not items. Madrid has no temporal stratum: its corpus is entirely
current consolidated text (see that set's caveats).

## By jurisdiction tier (MRR)

| city | national | regional | municipal |
|---|---|---|---|
| Turin | .867 (40) | .975 (20) | .797 (24) |
| London | .736 (20) | .812 (8) | .829 (12) |
| Madrid | .476 (24) | .499 (18) | .774 (38) |

Madrid's national tier is the weakest cell in the whole evaluation: the three CTE
documents are labelled by anchor phrase because their detected units are unstable
(DB-SI is a single 55-chunk "Artículo 11"; DB-HS repeats refs such as "2.1" across
unrelated sections), and retrieval frequently returns a neighbouring paragraph of
the right document instead of the one holding the value.

## Precedence — does the prevailing rule outrank the overridden one?

Only items that carry a `precedence` block are scored here. `pair_recall` = both
rules surfaced within depth; `winner_first` = the prevailing rule was retrieved
and ranks above the overridden one.

| run | runs scored | pair_recall | winner_first |
|---|---|---|---|
| Turin | 22 | .682 | **.955** |
| London (production scope) | 5 | .400 | **.800** |
| London (`--include-superseded`) | 5 | .600 | **.600** |
| Madrid | 4 | 1.000 | **.500** |

Failures worth naming:

- **q11-it** (Turin): the Italian phrasing of the change-of-use question ranks
  DPR 380 Art. 23-ter first and the prevailing regional Art. 8 eighth — even
  though Art. 23-ter says it does not apply in regions with their own rules.
- **uk13** (London): "in Tower Hamlets … minimum floor area of a room in an HMO"
  returns the national 6.51 m² at rank 1 and the borough's stricter 8.5 m² at
  rank 3. The city name in the query does not pull the municipal document up.
- **es22-en / es40-en** (Madrid): the general PGOUM article outranks the official
  interpretation that governs it, in English only — both are correct in Spanish.

## The temporal filter

One superseded document is ingested on purpose: `gpdo_2015_part3_asat_2021`, the
GPDO Part 3 as it stood on 1 Jan 2021, when offices converted to dwellings under
Class O (revoked 31 Jul 2021, replaced by Class MA). It is tagged
`status=superseded`, so the production cascade never returns it.

Running London with `--include-superseded` lets it compete, which is what the
filter is for:

- uk24 ("Can an office building be converted into flats … which class?") goes from
  **rank 1 to a complete miss**: the obsolete Class O text is a closer lexical
  match to "office building" than Class MA, and displaces it.
- `winner_first` over all precedence items drops .800 → .600, and overall MRR
  .779 → .744.

Embeddings carry no notion of validity, so the date filter — not the ranker — is
what keeps repealed law out of the answer.

## Known issues (kept deliberately)

- **uk04** labels GPDO `Class L`, which does not exist as a unit under the gemma-4
  detector (it swallows Class L into Class I — see `../README.md`). The label is
  legally right and the miss is a detector defect, so it stays. `python -m
  app.services.llm.rag.eval.validate` reports it as the single unmatched label
  across all three sets.
- Anchor labels (London plans, Madrid CTE) are lenient by construction: any chunk
  of the document containing the phrase counts. They are used only where detected
  units are not stable enough to name.
- Conflict items include the overridden rule in `relevant`, so a retriever that
  returns only the prevailing rule scores recall 0.5 on them by design.

## Reproducing

```bash
python -m app.services.llm.rag.eval.validate                       # labels still match the store?
python -m app.services.llm.rag.eval.run --gold app/services/llm/rag/eval/gold_queries_turin.json  --json eval_results/gold_v2/turin.json
python -m app.services.llm.rag.eval.run --gold app/services/llm/rag/eval/gold_queries_london.json --json eval_results/gold_v2/london.json
python -m app.services.llm.rag.eval.run --gold app/services/llm/rag/eval/gold_queries_madrid.json --json eval_results/gold_v2/madrid.json
python -m app.services.llm.rag.eval.run --gold app/services/llm/rag/eval/gold_queries_london.json --include-superseded \
       --json eval_results/gold_v2/london_incl_superseded.json
```

Still to do: re-run the whole thing on a gpt-5.4-chunked store for the detector
ablation (the three new documents have no gpt-5.4 chunkspec yet), and add the
conflict cases to the downstream evaluation.
