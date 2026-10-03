"""
Integration tests for the v2 wiring in graph_agent (no LLM calls).

Exercises the two behavior-changing methods directly against a lightweight stand-in
for the orchestrator, using the real DuckDB executor:

  - _apply_ir_repair       : alias repair before execution (enforce mode only)
  - _try_ir_fallback       : deterministic IR-rendered fallback when SQL fails

Run:
    python tests/test_v2_integration.py
    pytest tests/test_v2_integration.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from app.services.analysis.executor import execute_sql_query
from app.services.llm.agents.graph_agent import GraphOrchestratorAgent
from app.services.llm.constraint_ir import (
    ConstraintIR,
    make_geo_entry,
    make_predicate_entry,
)


DB_SCHEMA = {
    "types": {
        "tipologia_bene_immobile": "VARCHAR",
        "superficie_di_riferimento_mq": "DOUBLE",
        "mobilita": "DOUBLE",
        "latitudine": "DOUBLE",
        "longitudine": "DOUBLE",
    }
}

DF = pd.DataFrame({
    "tipologia_bene_immobile": ["Uffici e assimilabili", "Uffici e assimilabili", "Altro"],
    "superficie_di_riferimento_mq": [1500.0, 300.0, 100.0],
    "mobilita": [80.0, 20.0, 90.0],
    "latitudine": [45.07, 45.08, 45.50],
    "longitudine": [7.66, 7.67, 7.90],
})


def _fake_agent():
    """A minimal stand-in carrying just what the two methods touch."""
    return types.SimpleNamespace(execute_sql_fn=execute_sql_query)


def _base_state(mode, ir, sql=""):
    return {
        "constraint_mode": mode,
        "constraint_ir": ir,
        "sql_query": sql,
        "db_schema": DB_SCHEMA,
        "db_metadata": {},
        "base_dataset": DF,
        "dataset_path": None,
        "gemini_responses": {
            "constraint_layer": {
                "sql_source": "llm", "repairs_applied": [],
                "fallback_used": None, "validation_after_repair": None,
            }
        },
    }


def _office_ir():
    return ConstraintIR(query="t", entries=[
        make_predicate_entry(index=1, source_agent="building", requirement={
            "target_column": "tipologia_bene_immobile", "operator": "IN",
            "value": ["Uffici e assimilabili"]}),                                  # hard
        make_predicate_entry(index=2, source_agent="building", requirement={
            "target_column": "surface_area", "operator": ">=", "value": 1000}),     # soft
    ])


# ---------------------------------------------------------------------------
# Repair gate
# ---------------------------------------------------------------------------

def test_repair_fixes_alias_in_enforce_mode():
    ir = _office_ir()
    state = _base_state("enforce", ir,
                        sql="SELECT * FROM ESTATES WHERE surface_area >= 1000")
    GraphOrchestratorAgent._apply_ir_repair(_fake_agent(), state)
    assert "superficie_di_riferimento_mq >= 1000" in state["sql_query"]
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["sql_source"] == "repaired_llm"
    assert snap["repairs_applied"] == [
        {"from": "surface_area", "to": "superficie_di_riferimento_mq"}]


def test_repair_is_noop_in_observe_mode():
    ir = _office_ir()
    sql = "SELECT * FROM ESTATES WHERE surface_area >= 1000"
    state = _base_state("observe", ir, sql=sql)
    GraphOrchestratorAgent._apply_ir_repair(_fake_agent(), state)
    assert state["sql_query"] == sql  # untouched -> baseline stays identical
    assert state["gemini_responses"]["constraint_layer"]["sql_source"] == "llm"


def test_repaired_sql_actually_executes():
    ir = _office_ir()
    state = _base_state("enforce", ir,
                        sql="SELECT * FROM ESTATES WHERE surface_area >= 1000")
    GraphOrchestratorAgent._apply_ir_repair(_fake_agent(), state)
    df, err = execute_sql_query(state["sql_query"], DF)
    assert err is None
    assert len(df) == 1  # only the 1500 sqm office


# ---------------------------------------------------------------------------
# Deterministic IR fallback
# ---------------------------------------------------------------------------

def test_ir_fallback_returns_constraint_preserving_rows():
    ir = _office_ir()
    state = _base_state("enforce", ir)
    ok = GraphOrchestratorAgent._try_ir_fallback(_fake_agent(), state)
    assert ok is True
    # full render = office AND surface>=1000 -> exactly the 1500 sqm row
    assert len(state["selected_data"]) == 1
    assert (state["selected_data"]["tipologia_bene_immobile"] == "Uffici e assimilabili").all()
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["sql_source"] == "deterministic_fallback"
    assert snap["fallback_used"] == "ir_render_full"


def test_ir_fallback_relaxes_to_hard_only_when_full_is_empty():
    # surface >= 100000 makes the full render empty -> hard-only keeps typology.
    ir = ConstraintIR(query="t", entries=[
        make_predicate_entry(index=1, source_agent="building", requirement={
            "target_column": "tipologia_bene_immobile", "operator": "IN",
            "value": ["Uffici e assimilabili"]}),
        make_predicate_entry(index=2, source_agent="building", requirement={
            "target_column": "surface_area", "operator": ">=", "value": 100000}),
    ])
    state = _base_state("enforce", ir)
    ok = GraphOrchestratorAgent._try_ir_fallback(_fake_agent(), state)
    assert ok is True
    snap = state["gemini_responses"]["constraint_layer"]
    assert snap["fallback_used"] == "ir_render_hard_only"
    # hard-only keeps the typology -> 2 office rows, ignores the impossible surface
    assert len(state["selected_data"]) == 2


def test_ir_fallback_noop_in_observe_mode():
    ir = _office_ir()
    state = _base_state("observe", ir)
    ok = GraphOrchestratorAgent._try_ir_fallback(_fake_agent(), state)
    assert ok is False


def test_ir_fallback_false_when_no_entries():
    state = _base_state("enforce", ConstraintIR(query="t", entries=[]))
    ok = GraphOrchestratorAgent._try_ir_fallback(_fake_agent(), state)
    assert ok is False


def test_ir_fallback_geo_only_executes():
    ir = ConstraintIR(query="t", entries=[
        make_geo_entry(index=1, lat=45.07, lon=7.66, radius_km=2.0)])
    state = _base_state("enforce", ir)
    ok = GraphOrchestratorAgent._try_ir_fallback(_fake_agent(), state)
    assert ok is True
    # the two near-center rows are within 2km; the 45.50 one is not
    assert len(state["selected_data"]) == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
