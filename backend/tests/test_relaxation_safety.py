"""
Tests for v2 relaxation safety: relaxation must never weaken a HARD constraint.

observe mode -> measure only (records hits, behavior unchanged: hard constraint
                 can still be dropped -> this is the "before" violation count)
enforce mode -> block the drop/relax (hits == blocked, typology preserved)

Exercises _apply_ast_relaxation_workflow against the real DuckDB executor with no
LLM calls (empty proposal list still drives STEP 2 removal).

Run:
    python tests/test_relaxation_safety.py
    pytest tests/test_relaxation_safety.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import sqlglot

from app.services.analysis.executor import execute_sql_query
from app.services.llm.agents.graph_agent import GraphOrchestratorAgent
from app.services.llm.constraint_ir import ConstraintIR, make_predicate_entry


# 3 offices + 50 others, all with surface >= 1000. Filtering to offices gives 3
# (< 10), so STEP 2 will try to drop a condition. Dropping the (soft) surface
# filter keeps 3; dropping the (hard) typology filter jumps to 53 (>= 10).
DF = pd.DataFrame({
    "tipologia_bene_immobile": (["Uffici e assimilabili"] * 3 + ["Altro"] * 50),
    "superficie_di_riferimento_mq": [1500.0] * 53,
})

INITIAL_SQL = (
    "SELECT * FROM ESTATES WHERE tipologia_bene_immobile = 'Uffici e assimilabili' "
    "AND superficie_di_riferimento_mq >= 1000"
)


def _agent():
    # Build a real instance without the heavy __init__; wire just the executor.
    agent = GraphOrchestratorAgent.__new__(GraphOrchestratorAgent)
    agent.execute_sql_fn = execute_sql_query
    return agent


def _ir_with_hard_typology():
    return ConstraintIR(query="t", entries=[
        make_predicate_entry(index=1, source_agent="building", requirement={
            "target_column": "tipologia_bene_immobile", "operator": "=",
            "value": "Uffici e assimilabili"}),   # hard (building identity column)
        make_predicate_entry(index=2, source_agent="building", requirement={
            "target_column": "surface_area", "operator": ">=", "value": 1000}),  # soft
    ])


def _state(mode):
    return {
        "constraint_mode": mode,
        "constraint_ir": _ir_with_hard_typology(),
        "db_schema": {"types": {
            "tipologia_bene_immobile": "VARCHAR",
            "superficie_di_riferimento_mq": "DOUBLE"}},
        "db_metadata": {},
        "base_dataset": DF,
        "dataset_path": None,
        "selected_data": DF[DF["tipologia_bene_immobile"] == "Uffici e assimilabili"],
        "gemini_responses": {"constraint_layer": {
            "relaxation_safety_hits": 0, "relaxation_safety_blocked": 0,
            "relaxation_safety_columns": []}},
    }


def _has_typology(sql):
    return "tipologia_bene_immobile" in sql


# ---------------------------------------------------------------------------
# Helper-level checks
# ---------------------------------------------------------------------------

def test_protected_columns_include_hard_typology():
    agent = _agent()
    protected, reverse = agent._relaxation_protected(_state("enforce"))
    # protected stores CANONICAL names; tipologia_bene_immobile -> property_type
    assert "property_type" in protected
    # the dataset name must still resolve into the protected set
    assert reverse.get("tipologia_bene_immobile", "tipologia_bene_immobile") in protected
    # the soft surface column must NOT be protected (canonical = surface_area)
    assert "surface_area" not in protected


def test_condition_protected_column_detects_alias():
    agent = _agent()
    protected, reverse = agent._relaxation_protected(_state("enforce"))
    cond = sqlglot.parse_one(
        "tipologia_bene_immobile = 'Uffici e assimilabili'", read="duckdb")
    assert agent._condition_protected_column(cond, protected, reverse) == "tipologia_bene_immobile"
    soft = sqlglot.parse_one("superficie_di_riferimento_mq >= 1000", read="duckdb")
    assert agent._condition_protected_column(soft, protected, reverse) is None


# ---------------------------------------------------------------------------
# Workflow behavior: observe measures, enforce blocks
# ---------------------------------------------------------------------------

def test_observe_drops_hard_typology_and_records_hit():
    agent = _agent()
    state = _state("observe")
    final_sql = agent._apply_ast_relaxation_workflow(state, INITIAL_SQL, proposals=[])
    # current behavior: typology gets removed to reach the threshold
    assert not _has_typology(final_sql)
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["relaxation_safety_hits"] >= 1      # measured the violation
    assert snap["relaxation_safety_blocked"] == 0   # but did not block it
    assert "tipologia_bene_immobile" in snap["relaxation_safety_columns"]


def test_enforce_preserves_hard_typology_and_blocks():
    agent = _agent()
    state = _state("enforce")
    final_sql = agent._apply_ast_relaxation_workflow(state, INITIAL_SQL, proposals=[])
    # the hard typology must survive relaxation
    assert _has_typology(final_sql)
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["relaxation_safety_hits"] >= 1
    assert snap["relaxation_safety_blocked"] == snap["relaxation_safety_hits"]
    assert snap["relaxation_safety_blocked"] >= 1


def test_no_ir_means_no_protection_no_crash():
    agent = _agent()
    state = _state("enforce")
    state["constraint_ir"] = None
    # Should behave like the legacy workflow (no protection) and not raise.
    final_sql = agent._apply_ast_relaxation_workflow(state, INITIAL_SQL, proposals=[])
    assert isinstance(final_sql, str)
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["relaxation_safety_hits"] == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
