"""
Tests for the user-vs-law surface conflict rule in graph_agent (no LLM calls).

When the regulatory agent's legal minimum surface is above an upper bound the
building agent extracted from the request (<=, BETWEEN, ==), no building can
satisfy both: the bound is dropped so SQL and ranking use the legal minimum, and
the conflict is recorded for the status message and the result files.

Run:
    python tests/test_surface_conflicts.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.agents.graph_agent import _surface_conflict_note, resolve_surface_conflicts
from app.services.llm.agents.schema import BuildingAgentResult


def reg(value, found=True, op=">=", col="surface_area"):
    return json.dumps({"found": found, "requirements": [
        {"target_column": col, "operator": op, "value": value, "regulation": "London Plan Table 3.1"}]})


def bld(*reqs, typologies=("Abitazioni",)):
    return json.dumps({"typologies": list(typologies), "found": True, "requirements": list(reqs)})


def test_maximum_below_legal_minimum_is_dropped():
    raw, conflicts = resolve_surface_conflicts(
        reg(70), bld({"target_column": "surface_area", "operator": ">=", "value": 50},
                     {"target_column": "surface_area", "operator": "<=", "value": 60}))
    kept = json.loads(raw)["requirements"]
    assert kept == [{"target_column": "surface_area", "operator": ">=", "value": 50}]
    assert len(conflicts) == 1 and conflicts[0]["legal_minimum"] == 70
    assert conflicts[0]["requested"] == "surface_area <= 60"
    assert json.loads(raw)["typologies"] == ["Abitazioni"]   # the rest of the output is kept


def test_between_range_below_legal_minimum_is_dropped():
    raw, conflicts = resolve_surface_conflicts(
        reg(616), bld({"target_column": "surface_area", "operator": "BETWEEN", "value": [50, 80]}))
    assert json.loads(raw)["requirements"] == [] and len(conflicts) == 1


def test_exact_value_below_legal_minimum_is_dropped():
    raw, conflicts = resolve_surface_conflicts(
        reg(43), bld({"target_column": "superficie_di_riferimento_mq", "operator": "=", "value": 40}))
    assert json.loads(raw)["requirements"] == [] and len(conflicts) == 1   # dataset column name, '=' alias


def test_no_conflict_when_the_bound_allows_the_legal_minimum():
    for bound in ({"target_column": "surface_area", "operator": "<=", "value": 80},
                  {"target_column": "surface_area", "operator": ">=", "value": 30},
                  {"target_column": "energy_class", "operator": "<=", "value": 2}):
        assert resolve_surface_conflicts(reg(70), bld(bound)) == (None, [])


def test_no_conflict_without_a_legal_minimum():
    b = bld({"target_column": "surface_area", "operator": "<=", "value": 60})
    assert resolve_surface_conflicts(reg(70, found=False), b) == (None, [])
    assert resolve_surface_conflicts(reg(70, op="<="), b) == (None, [])     # not a minimum
    assert resolve_surface_conflicts('{"found": true, "requirements": []}', b) == (None, [])
    assert resolve_surface_conflicts("not json", b) == (None, [])


def test_highest_legal_minimum_binds():
    two = json.dumps({"found": True, "requirements": [
        {"target_column": "surface_area", "operator": ">=", "value": 50, "regulation": "a"},
        {"target_column": "surface_area", "operator": ">=", "value": 90, "regulation": "b"}]})
    raw, conflicts = resolve_surface_conflicts(two, bld({"target_column": "surface_area", "operator": "<=", "value": 60}))
    assert conflicts[0]["legal_minimum"] == 90 and conflicts[0]["regulation"] == "b"


def test_note_and_agent_result_update():
    raw, conflicts = resolve_surface_conflicts(reg(70), bld({"target_column": "surface_area", "operator": "<=", "value": 60}))
    result = BuildingAgentResult(raw_text=bld({"target_column": "surface_area", "operator": "<=", "value": 60}))
    result.raw_text = raw                        # what _analyze_request does
    assert json.loads(result.raw_text)["requirements"] == []
    note = _surface_conflict_note({"gemini_responses": {"surface_conflicts": conflicts}})
    assert "70 m²" in note and "London Plan Table 3.1" in note
    assert _surface_conflict_note({"gemini_responses": {}}) == ""


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
