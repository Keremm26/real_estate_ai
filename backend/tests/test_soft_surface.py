"""
Tests for "inferred surfaces rank, they do not filter" in graph_agent (no LLM calls).

The building agent turns a capacity into a surface ("6 students" -> 120 m2) and
'large'/'small' into a dataset percentile. Those numbers are its own guesses,
not requirements: they stay in the building ranking but are kept out of the SQL,
so the legal minimum from the regulatory agent is the size floor (a 45 m2 house
that is legal for 6 tenants is no longer dropped by a 120 m2 guess). A surface the
user wrote still filters.

Run:
    python tests/test_soft_surface.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.agents.graph_agent import (
    GraphOrchestratorAgent,
    _query_numbers,
    split_inferred_surface,
)
from app.services.llm.agents.schema import AgentContext, BuildingAgentResult, RegulatoryAgentResult


def bld(*reqs, typologies=("Abitazioni",)):
    return json.dumps({"typologies": list(typologies), "found": True, "requirements": list(reqs)})


def surface(op, value):
    return {"target_column": "surface_area", "operator": op, "value": value}


def test_numbers_in_the_request():
    assert 2000 in _query_numbers("con superficie sopra i 2000 mq")
    assert 2000 in _query_numbers("el edificio debe tener más de 2.000 m2")     # thousands dot
    assert {100.0, 200.0} <= set(_query_numbers("tra 100 e 200 mq"))
    assert 2.7 in _query_numbers("altezza 2,70 m")
    assert {1500.0, 1.5} <= set(_query_numbers("about 1,500 sq m"))


def test_capacity_estimate_ranks_but_does_not_filter():
    raw = bld(surface(">=", 120), {"target_column": "construction_period", "operator": ">=", "value": 1990})
    sql_raw, soft = split_inferred_surface(raw, "Shared house for 6 students near King's Cross, built after 1990.")
    kept = json.loads(sql_raw)
    assert soft == [surface(">=", 120)]
    assert kept["requirements"] == [{"target_column": "construction_period", "operator": ">=", "value": 1990}]
    assert kept["typologies"] == ["Abitazioni"]


def test_user_stated_surfaces_still_filter():
    for query, req in (("Two-bedroom flat near Brixton, at least 100 square metres.", surface(">=", 100)),
                       ("A flat near Camden Town, between 50 and 60 square metres.", surface("BETWEEN", [50, 60])),
                       ("Residencia; el edificio debe tener más de 2.000 m2.", surface(">=", 2000)),
                       ("Cerca un ufficio di almeno 200 mq.", {"target_column": "superficie_di_riferimento_mq",
                                                              "operator": ">=", "value": 200})):
        assert split_inferred_surface(bld(req), query) == (bld(req), []), query


def test_large_and_small_percentiles_are_preferences():
    sql_raw, soft = split_inferred_surface(bld(surface(">=", 106.15)), "Un immobile grande vicino alla Biblioteca Civica.")
    assert soft and json.loads(sql_raw)["requirements"] == []
    sql_raw, soft = split_inferred_surface(bld(surface(">=", 60), surface("<=", 57.62)),
                                           "A small student residence with 4 single rooms near Lavapiés.")
    assert len(soft) == 2


def test_benchmark_user_surfaces_are_recognised():
    """Every surface a benchmark query states in digits is read from its text, so it
    keeps filtering (PQ-37 'grande' is a vague size, a preference by design)."""
    here = os.path.dirname(__file__)
    for city in ("turin", "london", "madrid"):
        for q in json.load(open(os.path.join(here, f"benchmark_queries_{city}.json")))["queries"]:
            s = q["slots"].get("surface")
            if not isinstance(s, dict) or q["id"] == "PQ-37":
                continue
            nums = _query_numbers(q["query"])
            for v in (s.get("min"), s.get("max")):
                if v is not None:
                    assert any(abs(v - n) <= 0.5 for n in nums), (q["id"], v, q["query"])


def test_sql_agent_receives_the_legal_floor_not_the_guess():
    captured = {}

    class _SQL:
        def run(self, **kw):
            captured.update(kw)
            return type("R", (), {"sql": "SELECT * FROM ESTATES", "raw_text": "{}", "prompt": None})()

    orch = object.__new__(GraphOrchestratorAgent)
    orch.sql_agent = _SQL()
    orch._update_progress = lambda *a, **k: None
    orch._log_execution = lambda *a, **k: None
    query = "Find a property near King's Cross for a shared house for 6 university students."
    state = {
        "query": query, "retry_count": 0, "sql_query": "", "db_schema": {"types": {}}, "db_metadata": {},
        "location_payload": [], "gemini_responses": {"location_extraction": {"places": []}},
        "building_result": BuildingAgentResult(raw_text=bld(surface(">=", 120))),
        "regulatory_result": RegulatoryAgentResult(raw_text=json.dumps({"found": True, "requirements": [
            {"target_column": "surface_area", "operator": ">=", "value": 39.06}]})),
        "energy_result": None, "proximity_result": None, "context": AgentContext(user_query=query),
        "constraint_mode": "observe",
    }
    out = orch._generate_sql(state)
    reqs = captured["all_requirements"]
    assert "surface_area >= 39.06" in reqs and ">= 120" not in reqs
    assert out["gemini_responses"]["soft_surface_preferences"] == [surface(">=", 120)]
    assert json.loads(out["building_result"].raw_text)["requirements"] == [surface(">=", 120)]   # ranking keeps it


def test_closeness_score_is_symmetric_in_ratio():
    import pandas as pd
    from app.utils.scoring import calculate_closeness_score
    s = calculate_closeness_score(pd.Series([120, 240, 60, 360, 40, 0, None]), 120)
    assert s[0] == 100.0 and s[1] == s[2] and 36 <= s[1] <= 38          # double or half: ~37
    assert s[3] == 0.0 and s[4] == 0.0 and s[5] == 0.0 and s[6] == 0.0   # 3x, 1/3x, empty


def test_surface_scoring_follows_where_the_number_comes_from():
    from app.services.llm.agents.graph_agent import mark_surface_scoring
    stats = {"percentiles": {"25%": 49.4, "50%": 70.0, "75%": 106.15}}
    col = "superficie_di_riferimento_mq"
    reqs = [{"target_column": col, "operator": ">=", "value": 1200},              # 60 beds guess
            {"target_column": col, "operator": ">=", "value": 106.15},            # 'grande' = p75
            {"target_column": col, "operator": "<=", "value": 49.4},              # 'small' = p25
            {"target_column": col, "operator": ">=", "value": 2000},              # typed by the user
            {"target_column": col, "operator": "BETWEEN", "value": [100, 200]}]   # typed range: range score
    marked = mark_surface_scoring(reqs, "Residenza per 60 posti letto, grande, sopra i 2000 mq, tra 100 e 200 mq", stats)
    assert [("closeness" if r.get("closeness") else "pass_fail" if r.get("pass_fail") else "-") for r in marked] \
        == ["closeness", "-", "-", "pass_fail", "-"]
    assert "closeness" not in reqs[0] and "pass_fail" not in reqs[3]       # inputs are not mutated


def test_building_ranker_prefers_the_size_closest_to_the_guess():
    import pandas as pd
    from app.services.llm.agents.building_agent import BuildingAgent
    df = pd.DataFrame({"id": ["a", "b", "c", "d", "e"], "superficie_di_riferimento_mq": [80, 120, 130, 300, 1000],
                       "tipologia_bene_immobile": ["Abitazioni"] * 5})
    req = {"target_column": "superficie_di_riferimento_mq", "operator": ">=", "value": 120}
    agent = object.__new__(BuildingAgent)
    target = agent._run_ranking(df=df, ranked_typologies=[], requirements=[{**req, "closeness": True}], global_stats={})
    order = target.sort_values("building_score", ascending=False)["id"].tolist()
    assert order == ["b", "c", "a", "d", "e"]                              # 120, 130, 80, 300, 1000 m2
    bigger = agent._run_ranking(df=df, ranked_typologies=[], requirements=[req], global_stats={})
    assert bigger.set_index("id").loc["e", "building_score"] == 100.0    # the old rule: biggest wins
    # a size the user typed: everything that meets it scores alike, below it scores 0
    typed = agent._run_ranking(df=df, ranked_typologies=[], requirements=[{**req, "value": 100, "pass_fail": True}],
                               global_stats={}).set_index("id")["building_score"]
    assert typed["b"] == typed["c"] == typed["d"] == typed["e"] == 100.0 and typed["a"] == 0.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
