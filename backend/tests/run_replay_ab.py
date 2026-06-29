"""
Replay-based A/B evaluation (Option A): a CONTROLLED before/after for v2.

Why
---
Running the pipeline twice (once per mode) lets the LLM produce different SQL each
time, so observe-vs-enforce deltas get polluted by model non-determinism (recall
wobbles even though v2 never touches extraction). This harness removes that
confound:

  Phase 1 (LLM, one run per query): capture the FIRST-PASS raw LLM SQL + the
           ConstraintIR. These are the model's output — the controlled input.
  Phase 2 (no LLM, deterministic): feed that SAME sql + IR through observe-handling
           and enforce-handling and compare.

Because both arms start from identical SQL + IR:
  - recall is constant by construction (v2 does not change extraction),
  - every reliability delta is 100% attributable to v2.

Phase 1 is cached to disk, so Phase 2 can be re-run for free (set REPLAY_CACHE).

Run:
    python tests/run_replay_ab.py                      # all 24 queries
    REPLAY_LIMIT=8 python tests/run_replay_ab.py
    REPLAY_TIERS=3,4 python tests/run_replay_ab.py
    REPLAY_CACHE=data/agent_logs/replay_cache_XXten.json python tests/run_replay_ab.py  # replay only
"""

import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import duckdb

from app.core.config import settings
from app.services.analysis.executor import execute_sql_query
from app.services.analysis_service import analysis_service
from app.services.llm.agents.graph_agent import GraphOrchestratorAgent
from app.services.llm.constraint_gold import load_gold, score_query, aggregate_scores
from app.services.llm.constraint_ir import ConstraintIR
from app.services.llm.constraint_validation import validate_sql_semantics
from app.services.llm.ir_sql_renderer import make_column_resolver, render_sql
from app.services.llm.ir_sql_repair import repair_column_aliases
from tests.query_bank import QUERY_BANK, build_query, phrases_of

HERE = os.path.dirname(__file__)
GOLD = load_gold(os.path.join(HERE, "constraint_ground_truth.json"))

LIMIT = int(os.environ.get("REPLAY_LIMIT", "0")) or None
TIERS = ({int(t) for t in os.environ["REPLAY_TIERS"].split(",")}
         if os.environ.get("REPLAY_TIERS") else None)
MODEL = os.environ.get("GOLD_EVAL_MODEL", "gpt-5.4-mini")
CACHE_IN = os.environ.get("REPLAY_CACHE")          # reuse a capture, skip Phase 1
RELAX_THRESHOLD = 10                                 # matches pipeline's trigger

ENTRIES = [e for e in QUERY_BANK if (TIERS is None or e["tier"] in TIERS)]
if LIMIT:
    ENTRIES = ENTRIES[:LIMIT]


def _dataset_path() -> str:
    p = settings.dataset_options.get("full")
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(HERE, "..", p))


def _db_schema(dataset_path: str) -> dict:
    """Read column names + types from the parquet via DuckDB DESCRIBE."""
    con = duckdb.connect(":memory:")
    rows = con.execute(f"DESCRIBE SELECT * FROM '{dataset_path}'").fetchall()
    con.close()
    return {"types": {r[0]: r[1] for r in rows}}


# ---------------------------------------------------------------------------
# Phase 1 — capture raw LLM SQL + IR (one real run per query)
# ---------------------------------------------------------------------------

async def capture() -> list:
    settings.set_llm_model(MODEL)
    captured = []
    for i, entry in enumerate(ENTRIES, start=1):
        combo = entry["combo"]
        query = build_query(combo)
        print(f"[capture {i}/{len(ENTRIES)}] T{entry['tier']} {query[:60]} ...")
        try:
            res = await analysis_service.run_analysis(
                run_id=f"cap_{i}", query=query, dataset_key="full",
                map_limit=100, llm_limit=1, analysis_mode="agent",
                use_relaxation=True, constraint_mode="observe",
            )
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {str(e)[:140]}")
            continue
        gr = res.get("gemini_responses", {}) or {}
        raw_sql = (gr.get("sql_generation", {}) or {}).get("sql_query")
        ir_dict = (gr.get("constraint_layer", {}) or {}).get("constraint_ir")
        if not raw_sql or not ir_dict:
            print("    SKIP: no raw SQL / IR captured")
            continue
        captured.append({
            "tier": entry["tier"], "query": query,
            "phrases": phrases_of(combo), "raw_sql": raw_sql, "ir": ir_dict,
        })
    return captured


# ---------------------------------------------------------------------------
# Phase 2 — deterministic replay through both modes
# ---------------------------------------------------------------------------

def _relax_safety(raw_sql, ir, db_schema, dataset_path, mode):
    """Deterministically measure relaxation safety on the captured SQL.

    Simulates the pipeline's STEP-2 progressive removal (empty proposals) ONLY
    when the query under-fills (< threshold), which is exactly when the real
    pipeline relaxes. Returns (hits, blocked).
    """
    df, err = execute_sql_query(raw_sql, None, dataset_path=dataset_path)
    rows = 0 if (err or df is None) else len(df)
    if rows >= RELAX_THRESHOLD:
        return 0, 0

    agent = GraphOrchestratorAgent.__new__(GraphOrchestratorAgent)
    agent.execute_sql_fn = execute_sql_query
    state = {
        "constraint_mode": mode, "constraint_ir": ir,
        "db_schema": db_schema, "db_metadata": {},
        "base_dataset": None, "dataset_path": dataset_path,
        "selected_data": df if df is not None else [],
        "gemini_responses": {"constraint_layer": {
            "relaxation_safety_hits": 0, "relaxation_safety_blocked": 0,
            "relaxation_safety_columns": []}},
    }
    try:
        agent._apply_ast_relaxation_workflow(state, raw_sql, proposals=[])
    except Exception:
        pass
    snap = state["gemini_responses"]["constraint_layer"]
    return snap["relaxation_safety_hits"], snap["relaxation_safety_blocked"]


def _evaluate_sql(sql, ir, db_schema, dataset_path):
    """Validate + execute one SQL string; return reliability signals."""
    report = validate_sql_semantics(sql, ir, db_schema, {})
    df, err = execute_sql_query(sql, None, dataset_path=dataset_path)
    return {
        "sql_valid": report.is_valid,
        "executes": err is None,
        "rows": 0 if (err or df is None) else len(df),
        "invalid_columns": len(report.invalid_columns),
        "missing_hard": len(report.missing_constraints),
        "hard_preserved": report.hard_constraints_preserved,
    }


def replay(captured: list) -> list:
    dataset_path = _dataset_path()
    db_schema = _db_schema(dataset_path)
    resolver = make_column_resolver(db_schema)
    results = []

    for item in captured:
        ir = ConstraintIR.model_validate(item["ir"])
        raw_sql = item["raw_sql"]

        # Recall is computed once from the shared IR -> identical for both modes.
        recall = score_query(phrases=item["phrases"], ir=ir, gold=GOLD)

        # OBSERVE: run the raw LLM SQL exactly as today.
        obs = _evaluate_sql(raw_sql, ir, db_schema, dataset_path)
        obs_h, obs_b = _relax_safety(raw_sql, ir, db_schema, dataset_path, "observe")
        obs.update(relax_hits=obs_h, relax_blocked=obs_b)

        # ENFORCE: alias-repair first; if it still fails / returns nothing, IR fallback.
        rep = repair_column_aliases(raw_sql, db_schema)
        enf_sql, sql_source = rep.sql, ("repaired_llm" if rep.changed else "llm")
        enf = _evaluate_sql(enf_sql, ir, db_schema, dataset_path)
        if not enf["executes"] or enf["rows"] == 0:
            for hard_only in (False, True):
                rendered = render_sql(ir, resolve_column=resolver, hard_only=hard_only)
                if not rendered.sql:
                    continue
                fb = _evaluate_sql(rendered.sql, ir, db_schema, dataset_path)
                if fb["executes"] and fb["rows"] > 0:
                    enf, enf_sql = fb, rendered.sql
                    sql_source = "deterministic_fallback"
                    break
        enf_h, enf_b = _relax_safety(raw_sql, ir, db_schema, dataset_path, "enforce")
        enf.update(relax_hits=enf_h, relax_blocked=enf_b,
                   repairs=len(rep.repairs), sql_source=sql_source)

        results.append({
            "tier": item["tier"], "query": item["query"],
            "recall": recall, "observe": obs, "enforce": enf,
            "raw_sql": raw_sql, "enforce_sql": enf_sql,
        })
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _agg(results, mode):
    rows = [r[mode] for r in results]
    n = len(rows) or 1
    src = {}
    for r in rows:
        s = r.get("sql_source", "llm")
        src[s] = src.get(s, 0) + 1
    return {
        "valid_rate": round(sum(1 for r in rows if r["sql_valid"]) / n, 2),
        "exec_rate": round(sum(1 for r in rows if r["executes"]) / n, 2),
        "zero_row_queries": sum(1 for r in rows if r["rows"] == 0),
        "invalid_columns": sum(r["invalid_columns"] for r in rows),
        "missing_hard": sum(r["missing_hard"] for r in rows),
        "repairs": sum(r.get("repairs", 0) for r in rows),
        "relax_hits": sum(r["relax_hits"] for r in rows),
        "relax_blocked": sum(r["relax_blocked"] for r in rows),
        "sql_source": src,
    }


def report(results):
    print("\n" + "=" * 74)
    print(f"REPLAY A/B  ({len(results)} queries, model={MODEL})")
    print("Same captured LLM SQL + IR through both arms -> deltas are pure v2.")
    print("=" * 74)

    rec = aggregate_scores([r["recall"] for r in results])
    mr = rec["micro_recall"]
    print(f"\nIR-vs-intent recall (constant both arms): "
          f"{mr:.2f}" if mr is not None else "n/a")

    o, e = _agg(results, "observe"), _agg(results, "enforce")
    rowfmt = "  {:<32}{:>14}{:>14}"
    print(rowfmt.format("metric", "observe", "enforce"))
    print("  " + "-" * 60)
    print(rowfmt.format("SQL valid rate", o["valid_rate"], e["valid_rate"]))
    print(rowfmt.format("execution success rate", o["exec_rate"], e["exec_rate"]))
    print(rowfmt.format("queries with 0 rows", o["zero_row_queries"], e["zero_row_queries"]))
    print(rowfmt.format("invalid-column errors", o["invalid_columns"], e["invalid_columns"]))
    print(rowfmt.format("missing hard constraints", o["missing_hard"], e["missing_hard"]))
    print(rowfmt.format("alias repairs applied", o["repairs"], e["repairs"]))
    print(rowfmt.format("relaxation hits", o["relax_hits"], e["relax_hits"]))
    print(rowfmt.format("relaxation HARD violations", o["relax_hits"] - o["relax_blocked"],
                        e["relax_hits"] - e["relax_blocked"]))
    print(f"\n  observe SQL source: {o['sql_source']}")
    print(f"  enforce SQL source: {e['sql_source']}")

    # Per-query lines where v2 changed something.
    print("\n  Per-query v2 activations:")
    any_change = False
    for r in results:
        e_, o_ = r["enforce"], r["observe"]
        changed = (e_.get("sql_source") != "llm"
                   or e_["invalid_columns"] != o_["invalid_columns"]
                   or e_["rows"] != o_["rows"]
                   or (e_["relax_hits"] - e_["relax_blocked"]) != (o_["relax_hits"] - o_["relax_blocked"]))
        if changed:
            any_change = True
            print(f"    [T{r['tier']}] {r['query'][:50]:<50} "
                  f"src={e_.get('sql_source')} obs_rows={o_['rows']} enf_rows={e_['rows']} "
                  f"obs_viol={o_['relax_hits']-o_['relax_blocked']} enf_viol={e_['relax_hits']-e_['relax_blocked']}")
    if not any_change:
        print("    (none in this slice)")


async def main():
    if CACHE_IN:
        path = CACHE_IN if os.path.isabs(CACHE_IN) else os.path.join(HERE, "..", CACHE_IN)
        with open(path, encoding="utf-8") as f:
            captured = json.load(f)
        print(f"Loaded {len(captured)} cached captures from {path} (Phase 1 skipped).")
    else:
        captured = await capture()
        cache_out = os.path.join(HERE, "..", "data", "agent_logs",
                                 f"replay_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(cache_out, "w", encoding="utf-8") as f:
            json.dump(captured, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nCaptured {len(captured)} queries -> {cache_out}")

    results = replay(captured)
    out = os.path.join(HERE, "..", "data", "agent_logs",
                       f"replay_ab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    report(results)
    print(f"\nSaved detailed results to: {out}")


if __name__ == "__main__":
    asyncio.run(main())
