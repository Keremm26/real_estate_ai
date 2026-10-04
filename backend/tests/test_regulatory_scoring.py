"""
Offline tests for app/services/llm/rag/eval/regulatory_scoring.py (no LLM calls):
the shared scorer used by the downstream evaluation and the pipeline comparison.

Run:
    python tests/test_regulatory_scoring.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.rag.eval import regulatory_scoring as rs  # noqa: E402

COLS = ["surface_area", "property_type"]
HITS = [{"doc_name": "D.M. MUR 1256/2021 — Allegato A", "article_ref": "6.1.1"}]
KEY2NAME = {"dm_mur_1256_2021": "D.M. MUR 1256/2021 — Allegato A"}
EXPECT = {"activate": True, "target_column": "surface_area", "operator": ">=", "value_min": 570, "value_max": 1050}


def _answer(value, regulation="D.M. MUR 1256/2021 Allegato A 6.1.1", found=True, description=""):
    req = {"target_column": "surface_area", "operator": ">=", "value": value, "unit": "m2",
           "regulation": regulation, "description": description}
    return json.dumps({"found": found, "requirements": [req] if value is not None else []})


def test_to_float_handles_italian_numbers():
    assert rs.to_float("1.050,5") == 1050.5
    assert rs.to_float("1.050.000") == 1050000.0
    assert rs.to_float("1050,5") == 1050.5
    assert rs.to_float("1.05") == 1.05          # single dot = decimal point
    assert rs.to_float("12.5 m2") == 12.5
    assert rs.to_float(None) is None and rs.to_float(True) is None


def test_activated_in_range_and_grounded():
    r = rs.score_output(EXPECT, _answer(750), HITS, COLS, key2name=KEY2NAME)
    assert r["activated"] and r["activation_ok"] and r["value_in_range"]
    assert r["target_ok"] and r["operator_ok"] and r["main_doc_grounded"]


def test_missing_requirement_on_expected_true_is_a_miss():
    r = rs.score_output(EXPECT, _answer(None, found=False), HITS, COLS, key2name=KEY2NAME)
    assert not r["activated"] and not r["activation_ok"] and r["value_in_range"] is False


def test_decline_items():
    exp = {"activate": False}
    assert rs.score_output(exp, _answer(None, found=False), [], COLS, key2name=KEY2NAME)["activation_ok"]
    assert not rs.score_output(exp, _answer(500), HITS, COLS, key2name=KEY2NAME)["activation_ok"]


def test_either_needs_grounding_and_range_when_activating():
    exp = {**EXPECT, "activate": "either"}
    assert rs.score_output(exp, _answer(None, found=False), [], COLS, key2name=KEY2NAME)["activation_ok"]
    assert rs.score_output(exp, _answer(750), HITS, COLS, key2name=KEY2NAME)["activation_ok"]
    # activating with a citation to no retrieved document is not accepted
    assert not rs.score_output(exp, _answer(750, regulation="User query"), HITS, COLS, key2name=KEY2NAME)["activation_ok"]
    # activating with an out-of-range value is not accepted
    assert not rs.score_output(exp, _answer(5000), HITS, COLS, key2name=KEY2NAME)["activation_ok"]


def test_echo_of_user_value():
    r = rs.score_output(EXPECT, _answer(2000, regulation="User query"), HITS, COLS,
                        user_values=[2000], key2name=KEY2NAME)
    assert r["echo"] is True and r["value_in_range"] is False and r["main_doc_grounded"] is False
    assert rs.score_output(EXPECT, _answer(750), HITS, COLS, user_values=[2000], key2name=KEY2NAME)["echo"] is False
    assert rs.score_output(EXPECT, _answer(750), HITS, COLS, key2name=KEY2NAME)["echo"] is None


def test_short_act_numbers_are_grounded():
    hits = [{"doc_name": "Regolamento comunale n. 30 — Regolamento d'Igiene della Città di Torino"}]
    assert rs.doc_grounded("Regolamento comunale n. 30, Art. 130 comma 1", hits)
    assert not rs.doc_grounded("Art. 130", hits)                 # an article number alone is not the act
    assert not rs.doc_grounded("D.P.R. 6 giugno 2001, n. 380", hits)


def test_value_grounding_ignores_the_user_query():
    desc = "12.5 m2 per bed plus 5.0 m2 of services"
    assert rs.value_grounded(desc, "superficie 12,5 mq per posto letto; servizi 5,0 mq")[0] is True
    assert rs.value_grounded(desc, "nothing relevant here")[0] is False
    assert rs.value_grounded("no figures", "12.5")[0] is None


def test_unusable_column_is_not_activation():
    ans = json.dumps({"found": True, "requirements": [
        {"target_column": "not_a_column", "operator": ">=", "value": 750, "regulation": "DM 1256/2021"}]})
    r = rs.score_output(EXPECT, ans, HITS, COLS, key2name=KEY2NAME)
    assert not r["activated"] and r["n_requirements"] == 1


def test_winner_cited_on_precedence_items():
    prec = {"winner": "dm_mur_1256_2021", "loser": "dm_sanita_1975"}
    assert rs.score_output(EXPECT, _answer(750), HITS, COLS, precedence=prec, key2name=KEY2NAME)["winner_cited"] is True
    other = _answer(750, regulation="D.M. Sanità 5 luglio 1975 Art. 2")
    assert rs.score_output(EXPECT, other, HITS, COLS, precedence=prec, key2name=KEY2NAME)["winner_cited"] is False


def test_routing_ok_only_when_routed():
    r = rs.score_output(EXPECT, _answer(750), HITS, COLS, expected_use_case="student_housing",
                        routing={"use_case": "student_housing"}, key2name=KEY2NAME)
    assert r["routing_ok"] is True
    assert rs.score_output(EXPECT, _answer(750), HITS, COLS, expected_use_case="student_housing",
                           key2name=KEY2NAME)["routing_ok"] is None


def test_agent_drops_requirements_without_constraint():
    from app.services.llm.agents.regulatory_agent import drop_empty_requirements

    good = {"target_column": "surface_area", "operator": ">=", "value": 750}
    zero = {"target_column": "surface_area", "operator": ">=", "value": 0, "calculation": "not determinable"}
    raw, n = drop_empty_requirements(json.dumps({"found": True, "requirements": [good, zero]}))
    assert n == 1 and json.loads(raw) == {"found": True, "requirements": [good]}
    raw, n = drop_empty_requirements(json.dumps({"found": True, "requirements": [zero]}))
    assert n == 1 and json.loads(raw) == {"found": False, "requirements": []}
    untouched = json.dumps({"found": True, "requirements": [good, {"target_column": "property_type", "operator": "IN", "value": ["Uffici"]}]})
    assert drop_empty_requirements(untouched) == (untouched, 0)   # categorical values are kept


def test_aggregate_rates():
    recs = [rs.score_output(EXPECT, _answer(750), HITS, COLS, key2name=KEY2NAME),
            rs.score_output(EXPECT, _answer(None, found=False), HITS, COLS, key2name=KEY2NAME),
            rs.score_output({"activate": False}, _answer(None, found=False), [], COLS, key2name=KEY2NAME)]
    agg = rs.aggregate(recs)
    assert agg["n_runs"] == 3
    assert agg["activation_rate"] == 0.5 and agg["correct_decline_rate"] == 1.0
    assert abs(agg["activation_ok_rate"] - 2 / 3) < 1e-9
    assert agg["either_ok_rate"] is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
