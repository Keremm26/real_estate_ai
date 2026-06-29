"""
Before/after evaluation: run the query bank through the pipeline in BOTH
constraint modes and compare.

Two axes are measured:
  1. IR-vs-intent recall (how well the pipeline captures what was asked). v2 is
     NOT expected to change this — it touches SQL reliability, not extraction.
  2. SQL reliability (the v2 target): invalid-column errors, SQL faithfulness,
     which SQL path produced the result (llm / repaired / deterministic fallback /
     generic fallback), and relaxation-safety violations.

  observe = v1 baseline (SQL byte-identical to today)  -> the "before"
  enforce = v2 (alias repair + IR fallback + relaxation safety) -> the "after"

Cost: makes real LLM calls. Defaults to a small model and a small query slice.

Run:
    python tests/run_gold_eval.py                 # both modes, first 3 queries
    GOLD_EVAL_LIMIT=6 python tests/run_gold_eval.py
    CONSTRAINT_MODES=enforce python tests/run_gold_eval.py
    GOLD_EVAL_MODEL=gpt-5.4 python tests/run_gold_eval.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.services.analysis_service import analysis_service
from app.services.llm.constraint_ir import ConstraintIR
from app.services.llm.constraint_gold import load_gold, score_query, aggregate_scores
from tests.query_bank import QUERY_BANK, build_query, phrases_of

GOLD = load_gold(os.path.join(os.path.dirname(__file__), "constraint_ground_truth.json"))

# --- Cost / scope controls (all overridable via env) ---
LIMIT = int(os.environ.get("GOLD_EVAL_LIMIT", "3")) or None      # queries per mode
MODES = [m.strip() for m in os.environ.get("CONSTRAINT_MODES", "observe,enforce").split(",") if m.strip()]
MODEL = os.environ.get("GOLD_EVAL_MODEL", "gpt-5.4-mini")
TIERS = None  # e.g. {1, 2}

# Use the smaller model for evaluation runs.
settings.set_llm_model(MODEL)

COMBOS = [
    e["combo"] for e in QUERY_BANK
    if (TIERS is None or e["tier"] in TIERS)
][: LIMIT if LIMIT else None]


def _reliability(res: dict) -> dict:
    """Pull SQL-reliability signals from the constraint-layer snapshot."""
    snap = (res.get("gemini_responses", {}) or {}).get("constraint_layer", {}) or {}
    counters = snap.get("counters", {}) or {}
    return {
        "sql_source": snap.get("sql_source"),
        "fallback_used": snap.get("fallback_used"),
        "repairs_applied": len(snap.get("repairs_applied", []) or []),
        "relax_hits": snap.get("relaxation_safety_hits", 0),
        "relax_blocked": snap.get("relaxation_safety_blocked", 0),
        "invalid_columns": counters.get("invalid_columns_count", 0),
        "missing_constraints": counters.get("missing_constraints_count", 0),
        "hard_preserved": counters.get("hard_constraints_preserved", None),
        "sql_valid": counters.get("is_valid", None),
        "generic_fallback": bool((res.get("gemini_responses", {}) or {}).get("fallback_activated"))
                            and snap.get("sql_source") == "generic_fallback",
    }


async def run_combo(combo: dict, idx: int, mode: str):
    query = build_query(combo)
    res = await analysis_service.run_analysis(
        run_id=f"gold_{mode}_{idx}", query=query, dataset_key="full",
        map_limit=100, llm_limit=1, analysis_mode="agent", use_relaxation=True,
        constraint_mode=mode,
    )
    snap = (res.get("gemini_responses", {}) or {}).get("constraint_layer", {})
    ir_dict = snap.get("constraint_ir")
    ir = ConstraintIR.model_validate(ir_dict) if ir_dict else ConstraintIR(query=query)
    phrases = phrases_of(combo)
    score = score_query(phrases=phrases, ir=ir, gold=GOLD)
    return {"query": query, "phrases": phrases, "score": score,
            "reliability": _reliability(res)}


def aggregate_reliability(rows: list) -> dict:
    n = len(rows) or 1
    src = {}
    for r in rows:
        s = r["reliability"]["sql_source"] or "unknown"
        src[s] = src.get(s, 0) + 1
    return {
        "sql_source_dist": src,
        "total_repairs": sum(r["reliability"]["repairs_applied"] for r in rows),
        "total_invalid_columns": sum(r["reliability"]["invalid_columns"] for r in rows),
        "total_missing_constraints": sum(r["reliability"]["missing_constraints"] for r in rows),
        "relax_hits": sum(r["reliability"]["relax_hits"] for r in rows),
        "relax_blocked": sum(r["reliability"]["relax_blocked"] for r in rows),
        "sql_valid_rate": round(sum(1 for r in rows if r["reliability"]["sql_valid"]) / n, 2),
    }


async def run_mode(mode: str):
    print(f"\n{'#' * 72}\n# MODE: {mode}  (model={settings.LLM_MODEL})\n{'#' * 72}")
    rows = []
    for i, combo in enumerate(COMBOS, start=1):
        print(f"[{mode}][{i}/{len(COMBOS)}] {build_query(combo)[:64]} ...")
        try:
            r = await run_combo(combo, i, mode)
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {str(e)[:160]}")
            continue
        s, rel = r["score"], r["reliability"]
        print(f"    recall={s['recall']}  captured={s['captured']}/{s['expected']}  "
              f"| sql_source={rel['sql_source']}  repairs={rel['repairs_applied']}  "
              f"invalid_cols={rel['invalid_columns']}  relax_hits/blocked={rel['relax_hits']}/{rel['relax_blocked']}")
        rows.append(r)
    return rows


async def main():
    per_mode = {}
    for mode in MODES:
        per_mode[mode] = await run_mode(mode)

    out = os.path.join(os.path.dirname(__file__), "..", "data", "agent_logs",
                       f"gold_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(per_mode, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved to: {out}")

    print("\n" + "=" * 72)
    print("BEFORE/AFTER COMPARISON")
    print("=" * 72)
    for mode in MODES:
        rows = per_mode[mode]
        if not rows:
            print(f"\n[{mode}] no successful runs")
            continue
        rec = aggregate_scores([r["score"] for r in rows])
        rel = aggregate_reliability(rows)
        print(f"\n[{mode}]  ({len(rows)} queries)")
        print(f"  IR-vs-intent recall (micro)   : "
              f"{rec['micro_recall']:.2f}" if rec['micro_recall'] is not None else "  recall: n/a")
        print(f"  SQL valid rate                : {rel['sql_valid_rate']}")
        print(f"  invalid-column errors (total) : {rel['total_invalid_columns']}")
        print(f"  missing hard constraints      : {rel['total_missing_constraints']}")
        print(f"  alias repairs applied         : {rel['total_repairs']}")
        print(f"  relaxation safety hits/blocked: {rel['relax_hits']}/{rel['relax_blocked']}")
        print(f"  SQL source distribution       : {rel['sql_source_dist']}")


if __name__ == "__main__":
    asyncio.run(main())
