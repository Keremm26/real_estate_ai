# Schema-grounding repair: offline replay on the final study

Each trial's first raw SQL and the SQL the pipeline ran were re-executed on the city dataset (DuckDB, no LLM calls). Source: `tests/replay_schema_repair.py`.

| city | arm | trials | repaired by the pipeline | raw SQL fails without repair | repaired SQL changed rows of a raw query that ran | trials with SQL retries | replay = pipeline count |
|---|---|---|---|---|---|---|---|
| turin | none | 150 | 112/150 (75%) | 115/150 (77%) | 0/112 (0%) | 0 | 147/147 (100%) |
| turin | rag | 150 | 132/150 (88%) | 135/150 (90%) | 0/132 (0%) | 0 | 147/147 (100%) |
| london | none | 120 | 54/120 (45%) | 54/120 (45%) | 0/54 (0%) | 0 | 120/120 (100%) |
| london | rag | 120 | 96/120 (80%) | 96/120 (80%) | 0/96 (0%) | 0 | 120/120 (100%) |
| madrid | none | 120 | 75/120 (62%) | 78/120 (65%) | 0/75 (0%) | 0 | 117/117 (100%) |
| madrid | rag | 120 | 104/120 (87%) | 107/120 (89%) | 0/104 (0%) | 0 | 114/114 (100%) |
| **all** | | 780 | 573/780 (73%) | 585/780 (75%) | 0/573 (0%) | 0 | 765/765 (100%) |

Raw-SQL error kinds: {'Binder Error': 585}
Columns renamed by the repair (agent name -> times): {'surface_area': 340, 'education': 301, 'energy_class': 150, 'mobility': 73, 'green': 45, 'cadastral_units_count': 28, 'commercial': 18, 'healthcare': 15, 'construction_period': 12}
Repairs that added TRY_CAST: 12; repairs that changed string literals: 0
Raw failures in trials not marked repaired (repaired SQL ran but matched nothing -> generic fallback): {('turin', 'none', 'generic_fallback'): 3, ('turin', 'rag', 'generic_fallback'): 3, ('madrid', 'none', 'generic_fallback'): 3, ('madrid', 'rag', 'generic_fallback'): 3}

Without the repair, a failing query enters the pipeline's retry loop (up to three new SQL-agent calls) and then the generic fallback; the replay cannot say whether those retries would have succeeded.
