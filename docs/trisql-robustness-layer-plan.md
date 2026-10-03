# TriSQL-Inspired Robustness Layer for MURENA SQLAgent

> **Status:** design / framing document (no implementation yet)
> **Goal:** make MURENA's constraint-to-SQL stage more robust, adapting TriSQL's
> control-flow principles to a flat-table, training-free, multi-agent setting.

---

## 1. Corrected Framing

This work should **not** be presented as a direct implementation of TriSQL.

TriSQL targets classic Text-to-SQL over complex schemas: multiple tables, joins,
nested queries, trained schema selectors, trained structure-aware generators, and
learned (SFT + RL) refiners.

MURENA is different. Property filtering happens mostly over a **single flat DuckDB
table** `ESTATES`, built from a dynamic dataframe / Parquet dataset. The generated
SQL is almost always:

```sql
SELECT * FROM ESTATES
WHERE ...
ORDER BY ...
```

There are no joins, no nesting, no multi-table schema. TriSQL's table-selection and
complex skeleton-generation components are therefore only partially transferable.

**Thesis framing (the defensible position):**

> We adapt TriSQL's *execution-validated, complexity-aware control-flow principles*
> to MURENA's flat-table, multi-agent constraint-to-SQL setting. Instead of
> reproducing TriSQL's trained schema selector, generator, and refiner, we implement
> a **training-free** robustness layer built on a structured constraint
> representation (ConstraintIR), layered (static + execution) SQL validation,
> complexity-aware routing, and constraint-aware fallback/relaxation. Because MURENA
> is flat-table and training-free, the bulk of the implementation is **novel
> infrastructure TriSQL does not contain**; TriSQL contributes the control-flow
> philosophy, not the components.

---

## 2. What Transfers from TriSQL and What Does Not

### 2.1 Does Not Transfer Strongly

**A. Table selection.** TriSQL selects relevant tables from large multi-table
schemas. MURENA queries one table, `ESTATES`. Table selection is moot.

**B. Complex SQL skeleton discovery.** TriSQL's structure-aware generator predicts
joins, nesting, `GROUP BY`, `HAVING`, subqueries, relational dependencies. In MURENA
the skeleton is **nearly fixed** — reduced to `SELECT * FROM ESTATES WHERE … ORDER BY
…`, plus a small deterministic slice (distance-vs-id ordering, constraint
consolidation). Skeleton-first generation is **not** a main contribution here, but
it is not *eliminated* either — it collapses to a fixed template plus deterministic
ordering/consolidation.

**C. Trained components.** TriSQL trains its schema selector (focal loss), its
generator (dual-objective), and its refiner (SFT + RL with execution reward). MURENA
reproduces none of these. The adaptation is **training-free**: deterministic rules,
runtime schema validation, LLM assistance where genuinely ambiguous, and execution
feedback.

### 2.2 Transfers Partially — Column-Level Schema Grounding

Even with one table, column correctness matters. The SQLAgent already receives the
live schema and `db_metadata` as prompt context, but the generated SQL is **not
deterministically validated** against them.

Reframe TriSQL's schema-selection idea as **column-level schema grounding and
schema-linking validation**. The goal is not search-space reduction across tables,
but ensuring:

* generated SQL uses real dataframe columns,
* categorical values are within each column's valid domain,
* agent constraints map to existing columns,
* wrong-column hallucinations are detected (e.g. `classe_target_ape` vs the real
  column name seen in traces).

### 2.3 Transfers Strongly (TriSQL control-flow principles)

These are independent of table count and apply directly to MURENA:

1. **Complexity-aware effort routing** — cheap path for simple queries, expensive
   LLM only for hard ones.
2. **Execution-validated acceptance (run-and-check)** — accept a query based on
   whether it executes and returns sensible results (TriSQL Eq. 9).
3. **Escalation when simple repair fails** — increase effort on failure (TriSQL
   Eq. 11, `z → z+1`).
4. **Rollback / do-not-return-worse-query** — never emit a query worse than the one
   you started with; fail loud at the ceiling rather than return garbage.

> **Correction vs. earlier drafts:** *static* "semantic validation before accepting
> SQL" is **not** a TriSQL transfer. TriSQL validates by **execution**, never by
> static column/value checks. Static semantic validation is part of *our* novel
> layer (see §6.1), not an import.

Even flat-table SQL can fail by: using wrong columns, omitting constraints, adding
unauthorized constraints, returning too few results, losing typology, relaxing hard
constraints, or failing silently after malformed agent output. The principles above
address exactly these.

---

## 3. Current MURENA SQL Flow

1. User writes a natural-language query.
2. Filtering agents extract SQL-relevant constraints: **BuildingAgent, EnergyAgent,
   LocationAgent, ProximityAgent, RegulatoryAgent**.
3. **RankingAgent** produces ranking *weights* — it does **not** feed `WHERE`
   constraints into `all_requirements`.
4. Filtering agents output JSON-like `raw_text`.
5. The orchestrator parses each output with `safe_extract_json`.
6. `_format_agent_requirements` converts parsed requirements into compact
   `[col] [op] [val]` text clauses.
7. Clauses are concatenated into `all_reqs`, passed as `all_requirements` to
   SQLAgent.
8. SQLAgent receives: original query, live dataframe schema, `db_metadata`,
   `all_requirements`, location object, and failed query / error message on retry.
9. SQLAgent generates SQL over `ESTATES`.
10. `_validate_sql` performs **safety-only** validation: must start with `SELECT`;
    must not contain dangerous keywords.
11. DuckDB executes the SQL (with an `EXPLAIN` syntax pre-check).
12. On execution error → retry path (`_handle_retry`, increments `retry_count`).
13. On too-few results (`< 10`) → relaxation path (`_relax_query`).
14. Ranking and Evaluation continue downstream.

---

## 4. Confirmed Current Strengths (not new contributions)

* structured filtering-agent outputs,
* robust JSON parsing (`safe_extract_json`: markdown/think-block stripping,
  `json_repair`, `ast.literal_eval` fallback),
* zero-addition instruction in the SQLAgent prompt,
* strictest-condition / zero-redundancy instruction in the prompt,
* Haversine distance filtering via a registered DuckDB UDF,
* live schema + `db_metadata` passed to SQLAgent,
* DuckDB execution with `EXPLAIN` syntax pre-check,
* retry on execution error,
* relaxation on low-result queries,
* AST-based deterministic relaxation workflow (`sqlglot`),
* relaxation proposals at low / medium / high levels.

---

## 5. Confirmed Current Gaps

### Gap 1 — Silent constraint disappearance (corrected mechanism)

Two distinct failure modes, neither of which is the benign
`"No specific requirements identified."` string (that appears only when an agent
returns **valid** JSON with a genuinely empty requirements list — legitimate):

1. **Schema-validated parse returns `None`.** At call sites that pass a Pydantic
   schema — e.g. `safe_extract_json(building_result.raw_text, schema=BuildingResponse)`
   — a validation failure returns `None`, and the constraint is **silently never
   appended** to `all_reqs`. No error, no placeholder, no log of the loss.
2. **Schema-less parse on malformed JSON.** Inside `_format_agent_requirements`
   (which calls `safe_extract_json` *without* a schema), malformed JSON falls back to
   dumping up to 500 chars of **raw text** into the requirements list — a
   different corruption, not a disappearance.

Both matter for evaluation and logging: a constraint can vanish or degrade with no
signal.

### Gap 2 — Source-agent information is lost

Lost in two places:

1. `_format_agent_requirements` keeps only `(column, operator, value)`.
2. `_generate_sql` concatenates all formatted requirements into one **flat bullet
   list**, making Building / Energy / Regulatory / Proximity constraints
   indistinguishable.

Downstream validation and relaxation cannot know which agent produced which
condition, nor its priority.

### Gap 3 — Schema is contextual, not enforced

The SQLAgent receives the live schema and `db_metadata` in the prompt, but **after**
generation there is no deterministic check that SQL columns exist, values are valid
for their column, the SQL respects the extracted constraints, or that no unauthorized
conditions were added.

Correct statement: *"Schema is provided as prompt context but is not
deterministically enforced against the generated SQL."*

### Gap 4 — SQL validation is safety-only

`_validate_sql` checks only `SELECT`-prefix and a dangerous-keyword blocklist. It
does not detect: missing constraints, extra unauthorized constraints, invalid
columns, invalid categorical values, lost typology, missing location filter, or hard-
constraint removal during relaxation. (Side note: it also rejects `WITH …` CTEs,
since they don't start with `SELECT`.)

### Gap 5 — Relaxation exists but is not constraint-aware

The two-phase hybrid (RelaxationAgent proposals → deterministic AST workflow,
re-execute, keep improvements) is sound and should be **extended, not replaced**.
Current limitation: all conditions treated roughly equally; ordering by position in
the `WHERE` clause; **no hard/soft distinction; no source-agent priority; no use of
RankingAgent weights; no protection for RegulatoryAgent constraints.**

---

## 6. Proposed Contribution

A **training-free, structure-aware robustness layer** for MURENA's SQLAgent:

> Preserve filtering-agent constraints as structured, source-tagged, priority-
> annotated objects (ConstraintIR); validate generated SQL in two layers (static
> schema-linking gate + execution loop) against the live schema and the extracted
> constraints; route queries by complexity; and improve fallback/relaxation using
> source-agent and hard/soft metadata.

Core components:

1. **ConstraintIR** — structured constraint representation.
2. **Parser-failure logging** — make Gap 1 losses *loud*.
3. **Source-agent preservation** — carry origin through to SQL and relaxation.
4. **Layered semantic SQL validation** — static gate + execution loop.
5. **QueryPlan (optional)** — traceability layer, not a generation step.
6. **Complexity-aware routing** — proportional effort.
7. **Constraint-aware relaxation** — hard/soft/ranking-only/review-driven.

### 6.1 Borrowed vs. Novel (read this before claiming contribution)

| Component | Origin |
|---|---|
| ConstraintIR (structured, source-tagged, priority-annotated) | **Novel** — TriSQL has no IR; it decodes straight to SQL |
| Parser-failure logging | **Novel** |
| Source-agent preservation | **Novel** |
| Static schema-linking validation (pre-execution) | **Novel** / classical post-processing line |
| QueryPlan traceability | **Novel** |
| Complexity-aware effort routing | TriSQL |
| Execution-validated acceptance + escalation + rollback | TriSQL (Eq. 9–11) |
| Constraint-aware (hard/soft) relaxation | **Novel** — no TriSQL analog |

**Five of the seven core components are novel.** TriSQL contributes the control-flow
*philosophy* (complexity routing + execution-validated fallback). Foreground this
split in the thesis; it is the answer to "didn't you just reimplement TriSQL?"

### 6.2 Layered Validation (decision: both, layered)

* **Static gate (novel, pre-execution).** Parse the generated SQL AST with
  `sqlglot` (machinery already exists in `_prepare_relaxation_data`). Check every
  column against the live schema and every categorical literal against
  `db_metadata` allowed values; verify constraint coverage against ConstraintIR.
  **On failure it annotates** (which column is wrong, which constraint is missing)
  rather than hard-rejecting — it is a *diagnostic producer* feeding the refiner,
  not a pass/fail wall.
* **Execution loop (TriSQL principle, post-run).** Existing `_execute_sql` →
  error / result-count feedback → fallback. Home of escalation and
  do-not-return-worse.

This separation also resolves the §2.3 attribution: static = ours, execution = TriSQL.

### 6.3 Constraint Priority (decision: per-constraint flag, four categories)

Each ConstraintIR entry carries its **own** `severity`/routing flag, assigned at
**extraction time** (last point where source-agent semantic context exists). This is
more flexible than source-agent identity (e.g. a mandatory legal regulation and a
recommended regulatory preference can come from the same agent yet differ).

| Flag | Meaning | Pipeline behavior |
|---|---|---|
| `hard` | mandatory (e.g. legal minimum surface) | Never relaxed/dropped. Static gate **must** verify presence in SQL. |
| `soft` | relaxable preference | Eligible for relaxation, ordered by ranking weight. |
| `ranking-only` | recommended preference, not a filter | **Never enters `WHERE`**; passed to RankingAgent as a scoring signal. |
| `human-review` / `non-SQL` | unclear / unmappable rule | Excluded from SQL; surfaced to user / logged (this is where Gap 1 losses become loud). |

**One annotation, four behaviors** — the flag drives routing through the entire
layer: static gate (must-verify vs ignore), rendering (`WHERE` vs not), relaxation
(protected vs eligible), failure handling (drop vs surface). This unifying property
is a strong thesis design point.

`ranking-only` structurally prevents *unauthorized constraints* (a soft preference
wrongly becoming a hard filter that empties results); `human-review` structurally
prevents *silent disappearance* (Gap 1).

---

## 7. Open Design Questions (next to work through)

* **Where ConstraintIR is built and where the flag is assigned.** Leading option:
  extend each filtering agent's output schema (currently
  `target_column, operator, value, description`) with a `severity` field, then build
  ConstraintIR in a normalization step right after `safe_extract_json`. This is a
  prompt + Pydantic-schema change per agent, not a `graph_agent` change.
* **Complexity signal definition** (training-free): e.g. number of constraints,
  haversine present, `IN`-list cardinality, retry depth, presence of `hard`
  constraints.
* **Static-gate failure → refiner handoff format** (what diagnostics, in what shape).
* **How RankingAgent weights enter soft-constraint relaxation ordering.**

---

## 8. Mapping Summary (TriSQL stage → MURENA)

| TriSQL stage | MURENA adaptation | Fit |
|---|---|---|
| Question-Guided Schema Selector (trained cross-attention) | column-level schema grounding + static validation (training-free) | Partial; reframed |
| Structure-Aware SQL Generator (skeleton → content) | fixed skeleton + ConstraintIR → rendering | Collapses; minor residual |
| Complexity-Aware SQL Refiner (Eq. 8–11) | complexity routing + layered validation + escalation/rollback | **Strong** |
| *(none)* | constraint-aware hard/soft relaxation | **Novel** |

---

## 9. Repo Facts That Constrain Implementation

* **Two SQL paths, selected by `analysis_mode`:** `"agent"` (default) runs the
  multi-agent path with SQLAgent; `"classic"` calls `_unified_analysis`
  ([graph_agent.py:1045](../backend/app/services/llm/agents/graph_agent.py)) — the
  unified **Baseline Planner**, which sets `sql_query` directly and skips SQLAgent
  ([graph_agent.py:1419](../backend/app/services/llm/agents/graph_agent.py)). They
  never co-run. **This layer targets `agent` mode; `classic` is the untouched
  comparison baseline.**
* **Agent requirements are untyped** `List[Dict[str, Any]]`
  ([schema.py:39–59](../backend/app/services/llm/agents/schema.py)) — free-form
  dicts (`target_column`, `operator`, `value`, `description`), no validation today.
  ConstraintIR is the first typed representation. Adding `severity` is a non-breaking
  dict key.
* **`_format_agent_requirements` is already called per-agent**
  ([graph_agent.py:1471/1485/1489/1493](../backend/app/services/llm/agents/graph_agent.py))
  — the source agent is known at each call site, so source preservation is low-effort.
* **Ranking weights are per-dimension** (`location, regulatory, energy, building,
  proximity` — [RankingWeights](../backend/app/services/llm/agents/schema.py)), 5
  weights inherited by constraints via `source_agent → weights[dimension]`. Available
  before SQL in agent mode.
* **Location is a different shape** — `(lat, lon, radius_km)` → haversine, handled via
  `filtered_locations`, not `(col, op, val)`.
* **An evaluation harness already exists** — [tests/test_suite.py](../backend/tests/test_suite.py):
  query set ([query_parameters.json](../backend/tests/query_parameters.json),
  combinatorial), agent-activation ground truth
  ([agent_ground_truth.json](../backend/tests/agent_ground_truth.json)),
  `calculate_sql_iou`, per-query `final_sql` JSON export. **Extend it; do not rebuild.**

---

## 10. Decisions Locked (v1)

| Decision | Choice |
|---|---|
| Target path | `agent` mode only; `classic`/planner untouched (it is the baseline) |
| v1 behavior | **Observe-only** — SQL generation unchanged; IR + validation + logging only |
| Severity source | **Hybrid** — agent-emitted `severity` if present, else deterministic fallback |
| Constraint gold | **Enrich `agent_ground_truth.json`** to constraint level |
| v1 scope | **Full observe-only stack** (IR, source preservation, schema + semantic validation report-only, eval instrumentation) |
| Behavior changes (rendering, routing, relaxation) | **Deferred to v2**, flag-gated, after baseline numbers exist |

---

## 11. v1 Implementation Spec (Observe-Only)

> Hard invariant for v1: **`state["sql_query"]` must be byte-identical to today's
> output for the same frozen agent inputs.** v1 only *observes* — it builds IR,
> renders a labeled `all_requirements`, validates, and logs. Nothing it computes is
> allowed to change the generated SQL. This guarantees a clean before/after baseline.

### 11.1 New module: `ConstraintIR`

New file `backend/app/services/llm/constraint_ir.py`. Typed Pydantic models:

```python
Severity = Literal["hard", "soft", "ranking_only", "human_review"]
ParseStatus = Literal["success", "failed"]
EntryKind = Literal["predicate", "geo"]

class ConstraintEntry(BaseModel):
    constraint_id: str            # e.g. "energy_001"
    source_agent: str             # building|energy|location|proximity|regulatory
    kind: EntryKind               # predicate | geo
    parse_status: ParseStatus

    # predicate entries
    target_column: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    # geo entries
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_km: Optional[float] = None
    lat_column: Optional[str] = None    # "latitudine"
    lon_column: Optional[str] = None    # "longitudine"

    # metadata
    severity: Severity
    relaxable: bool
    explicit: bool = True
    ranking_weight: float = 0.0         # inherited from source dimension
    original_requirement: Optional[Dict[str, Any]] = None

    # failure record (parse_status == "failed")
    raw_text_preview: Optional[str] = None
    failure_reason: Optional[str] = None

class ConstraintIR(BaseModel):
    query: str
    entries: List[ConstraintEntry]
    failures: List[ConstraintEntry]     # never silently dropped (Gap 1)
```

### 11.2 IR construction

New method `_build_constraint_ir(state) -> ConstraintIR`, called inside
`_generate_sql` **before** the `all_reqs` assembly loop. It consumes the already-parsed
per-agent results in `state` (`building_result`, `energy_result`, `regulatory_result`,
`proximity_result`) plus `filtered_locations`.

* For each agent: re-use the existing parse (`safe_extract_json`); if it returns
  `None` or fails, **emit a `failed` ConstraintEntry into `failures`** (Gap 1 fix),
  not nothing.
* Predicate entries from each requirement dict.
* Geo entries from `filtered_locations`.
* Attach `ranking_weight` from `state["ranking_result"].weights[source_dimension]`.

### 11.3 Severity assignment (hybrid)

`assign_severity(source_agent, requirement_dict) -> (Severity, relaxable)`:

1. If `requirement_dict.get("severity")` is set (agent-emitted) → use it.
2. Else deterministic fallback:

| source_agent | default severity | relaxable |
|---|---|---|
| regulatory | `hard` | False |
| building (typology, id, cadastral) | `hard` | False |
| building (surface thresholds) | `soft` | True |
| energy | `soft` | True |
| location (geo) | `soft` | True (radius widen, not drop) |
| proximity | `ranking_only` | True |

> **v1 staging:** v1 ships the **fallback only** (no agent prompt change) so the
> baseline stays unconfounded. Agent-emitted `severity` is a contained follow-up
> (prompt + dict key) that does not alter SQL — schedule as v1b once baseline numbers
> are captured.

### 11.4 Source preservation (Phase 2)

Render `all_requirements` **from the IR** with source labels:

```text
- [energy] classe_target_ape IN ('A1', 'A2', 'A3', 'A4')
- [location] haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0
- [building] tipologia_bene_immobile = 'Ufficio'
```

> Observe-only caveat: the *labeled* string changes the SQLAgent prompt, which could
> perturb generated SQL. To preserve the v1 invariant, **keep the existing unlabeled
> `all_requirements` as what is sent to the LLM**, and log the labeled version
> alongside. Promote the labeled version into the prompt only in v2 (measure the
> delta deliberately).

### 11.5 Schema + metadata validation (Phase 3, report-only)

`validate_schema_grounding(ir, sql, db_schema, db_metadata) -> dict`:

* every IR `target_column` exists in `db_schema`;
* every column in generated SQL exists (parse with `sqlglot`, reuse
  `_prepare_relaxation_data` machinery);
* categorical literals valid vs `db_metadata["fields"]` allowed values;
* table ∈ {`ESTATES`, `PROPERTIES`, `IMMOBILI`}; functions ∈ {`haversine_km`}.

### 11.6 Semantic SQL validation (Phase 4, report-only)

`validate_sql_semantics(sql, ir, db_schema, db_metadata) -> ValidationReport`:

```json
{
  "is_valid": true,
  "invalid_columns": [],
  "missing_constraints": [],      // IR hard entries absent from SQL
  "extra_conditions": [],         // SQL conditions with no IR entry (zero-addition)
  "hard_constraints_preserved": true,
  "typology_preserved": true,
  "location_filter_preserved": true,
  "zero_addition_violations": [],
  "warnings": []
}
```

Pure observation — never blocks execution in v1. Attach to the trace.

### 11.7 Logging & replay (Phase 0)

* Extend the existing run JSON export (`ENABLE_RUN_JSON_EXPORT`, `get_run_logger`)
  with: `constraint_ir`, `failures`, `validation_report`, `schema_grounding_report`,
  and the intrinsic counters (parse failures, `None`-drops, retry/relax counts,
  num_results).
* **Replay:** reuse the per-query JSON to freeze agent outputs and feed identical IR
  to both arms (eliminates agent-temperature confound).

### 11.8 Evaluation instrumentation

* **Tier A (intrinsic, free):** parse-success, `None`-drop count, execution-success,
  invalid-column rate, retry/fallback/relax counts, non-empty rate, latency — emitted
  per run.
* **Tier C (gold):** enrich `agent_ground_truth.json` — add expected
  `(column, operator, value, severity)` per phrase. Compute constraint precision/recall
  of final SQL vs this gold, fairly for both arms.
* **Circularity guard:** IR-faithfulness reported for the LLM path only; deterministic
  render (v2) is faithful-by-construction — stated, not claimed as a win.
* **Relaxation-safety (v2):** hard-constraint-violation-after-relaxation — GT-free,
  0-by-construction in enhanced, measurably > 0 in baseline.

### 11.9 v1 build order

1. `constraint_ir.py` (models) + unit tests.
2. `_build_constraint_ir` + severity fallback, wired into `_generate_sql` (IR computed,
   **not** used for SQL).
3. Parser-failure capture into `ir.failures`.
4. Schema-grounding + semantic validation (report-only functions).
5. Extend run JSON export with IR + reports + intrinsic counters.
6. Replay harness in `test_suite.py`; run **observe-only baseline** over the query set.
7. Enrich `agent_ground_truth.json` to constraint level; compute precision/recall.
8. Produce the before-picture metrics table.

### 11.10 v1 done = a measurement, not a behavior change

At the end of v1 you can state, with numbers and zero regression risk: today's system's
parse-failure rate, silent-drop count, invalid-column rate, zero-addition-violation
rate, and hard-constraint coverage — the quantified *before* that v2 must improve.
