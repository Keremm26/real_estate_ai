"""
Tests for the gold scorer (IR-vs-intent recall).

Run:
    python tests/test_constraint_gold.py
    pytest tests/test_constraint_gold.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.constraint_ir import ConstraintIR, make_predicate_entry, make_geo_entry
from app.services.llm.constraint_gold import load_gold, score_query, aggregate_scores

GOLD_PATH = os.path.join(os.path.dirname(__file__), "constraint_ground_truth.json")
GOLD = load_gold(GOLD_PATH)


def _ir(entries):
    return ConstraintIR(query="t", entries=entries)


def test_full_recall_when_all_captured():
    # Query = abitazione + Porta Susa + sopra i 1000 mq + C o più efficiente
    phrases = ["abitazione", "Porta Susa", "sopra i 1000 mq", "C o più efficiente"]
    ir = _ir([
        make_predicate_entry(index=1, source_agent="building",
            requirement={"target_column": "tipologia_bene_immobile", "operator": "IN",
                         "value": ["Abitazioni adibite a residenza con carattere continuativo"]}),
        make_geo_entry(index=1, lat=45.07, lon=7.66, radius_km=3.0),
        make_predicate_entry(index=2, source_agent="building",
            requirement={"target_column": "superficie_di_riferimento_mq", "operator": ">=", "value": 1000}),
        make_predicate_entry(index=1, source_agent="energy",
            requirement={"target_column": "classe_target_ape", "operator": "IN", "value": ["C", "B"]}),
    ])
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert r["expected"] == 4
    assert r["captured"] == 4
    assert r["recall"] == 1.0
    assert r["missed"] == []


def test_surface_alias_counts_as_hit():
    # IR uses the dataset (Italian) surface column; gold uses surface_area. Alias must match.
    phrases = ["sopra i 1000 mq"]
    ir = _ir([make_predicate_entry(index=1, source_agent="building",
        requirement={"target_column": "superficie_di_riferimento_mq", "operator": ">=", "value": 1000})])
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert r["recall"] == 1.0


def test_missing_typology_lowers_recall():
    # Query asks for residential + location, but IR dropped the typology.
    phrases = ["abitazione", "Porta Susa"]
    ir = _ir([make_geo_entry(index=1, lat=45.07, lon=7.66, radius_km=3.0)])
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert r["expected"] == 2
    assert r["captured"] == 1
    assert r["recall"] == 0.5
    assert any("abitazione" in m for m in r["missed"])


def test_generic_phrase_expects_nothing():
    # "immobile" is generic -> expects nothing -> contributes 0 to expected.
    phrases = ["immobile"]
    ir = _ir([])
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert r["expected"] == 0
    assert r["recall"] is None  # nothing to score


def test_future_use_no_typology_violation_flagged():
    # "centro per anziani" is a future use -> IR should NOT have a typology.
    phrases = ["centro per anziani"]
    ir = _ir([make_predicate_entry(index=1, source_agent="building",
        requirement={"target_column": "tipologia_bene_immobile", "operator": "IN",
                     "value": ["Ospedali, cliniche, case di cura e assimilabili"]})])
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert "centro per anziani" in r["no_typology_violations"]


def test_future_use_no_violation_when_no_typology():
    phrases = ["centro per anziani"]
    ir = _ir([])  # no typology -> no violation
    r = score_query(phrases=phrases, ir=ir, gold=GOLD)
    assert r["no_typology_violations"] == []


def test_aggregate():
    rs = [
        {"expected": 4, "captured": 4, "recall": 1.0, "no_typology_violations": []},
        {"expected": 2, "captured": 1, "recall": 0.5, "no_typology_violations": ["x"]},
        {"expected": 0, "captured": 0, "recall": None, "no_typology_violations": []},
    ]
    agg = aggregate_scores(rs)
    assert agg["total_expected"] == 6
    assert agg["total_captured"] == 5
    assert abs(agg["micro_recall"] - 5/6) < 1e-9
    assert abs(agg["macro_recall"] - 0.75) < 1e-9   # mean of 1.0 and 0.5
    assert agg["no_typology_violations"] == 1


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
