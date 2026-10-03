"""
Tests for report-only SQL validation (v1).

Run either way:
    python tests/test_constraint_validation.py
    pytest tests/test_constraint_validation.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.constraint_ir import (
    ConstraintIR,
    make_predicate_entry,
    make_geo_entry,
)
from app.services.llm.constraint_validation import (
    validate_schema_grounding,
    validate_sql_semantics,
)

# A small schema resembling the real ESTATES table.
SCHEMA = {
    "types": {
        "tipologia_bene_immobile": "VARCHAR",
        "classe_target_ape": "VARCHAR",
        "surface_area": "DOUBLE",
        "codice_comune": "VARCHAR",
        "latitudine": "DOUBLE",
        "longitudine": "DOUBLE",
        "energy_class": "VARCHAR",
        "mobility": "DOUBLE",
    }
}

# Metadata with allowed categorical values for energy_class.
METADATA = {
    "fields": {
        "energy_class": {"values": ["A1", "A2", "A3", "A4", "B", "C", "D", "E", "F", "G"]},
        "codice_comune": {"values": ["L219", "F205"]},
    }
}


def _ir_office_energy_geo():
    """IR for: offices, energy A1-A4, within 3km of a point."""
    return ConstraintIR(
        query="offices near Porta Nuova, energy class A",
        entries=[
            make_predicate_entry(index=1, source_agent="building",
                                 requirement={"target_column": "tipologia_bene_immobile",
                                              "operator": "=", "value": "Ufficio"}),
            make_predicate_entry(index=1, source_agent="energy",
                                 requirement={"target_column": "classe_target_ape",
                                              "operator": "IN", "value": ["A1", "A2", "A3", "A4"]}),
            make_geo_entry(index=1, lat=45.0622, lon=7.6785, radius_km=3.0),
        ],
    )


# ---------------------------------------------------------------------------
# Schema grounding
# ---------------------------------------------------------------------------

def test_clean_sql_passes_schema_grounding():
    ir = _ir_office_energy_geo()
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile = 'Ufficio' "
           "AND classe_target_ape IN ('A1','A2','A3','A4') "
           "AND haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0")
    rep = validate_schema_grounding(ir, sql, SCHEMA, METADATA)
    assert rep.sql_parse_ok is True
    assert rep.invalid_columns == []


def test_invalid_column_is_detected():
    ir = _ir_office_energy_geo()
    sql = "SELECT * FROM ESTATES WHERE made_up_column = 5"
    rep = validate_schema_grounding(ir, sql, SCHEMA, METADATA)
    assert "made_up_column" in rep.invalid_columns
    assert rep.is_valid is False


def test_invalid_categorical_value_is_detected():
    # energy_class only allows A1-G; 'Z9' is invalid.
    ir = ConstraintIR(
        query="q",
        entries=[make_predicate_entry(index=1, source_agent="energy",
                 requirement={"target_column": "energy_class", "operator": "=", "value": "Z9"})],
    )
    sql = "SELECT * FROM ESTATES WHERE energy_class = 'Z9'"
    rep = validate_schema_grounding(ir, sql, SCHEMA, METADATA)
    assert any(v["value"] == "Z9" for v in rep.invalid_categorical_values)
    assert rep.is_valid is False


def test_unparseable_sql_flagged():
    ir = _ir_office_energy_geo()
    rep = validate_schema_grounding(ir, "NOT EVEN SQL ;;;", SCHEMA, METADATA)
    assert rep.sql_parse_ok is False
    assert rep.is_valid is False


# ---------------------------------------------------------------------------
# Semantic faithfulness
# ---------------------------------------------------------------------------

def test_faithful_sql_is_valid():
    ir = _ir_office_energy_geo()
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile = 'Ufficio' "
           "AND classe_target_ape IN ('A1','A2','A3','A4') "
           "AND haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0 "
           "ORDER BY haversine_km(latitudine, longitudine, 45.0622, 7.6785) ASC")
    rep = validate_sql_semantics(sql, ir, SCHEMA, METADATA)
    assert rep.hard_constraints_preserved is True
    assert rep.typology_preserved is True
    assert rep.location_filter_preserved is True
    assert rep.extra_conditions == []
    assert rep.is_valid is True


def test_dropped_hard_constraint_detected():
    # SQL forgot the typology (a hard constraint).
    ir = _ir_office_energy_geo()
    sql = ("SELECT * FROM ESTATES WHERE classe_target_ape IN ('A1','A2','A3','A4') "
           "AND haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0")
    rep = validate_sql_semantics(sql, ir, SCHEMA, METADATA)
    assert rep.hard_constraints_preserved is False
    assert any("tipologia_bene_immobile" in m for m in rep.missing_constraints)
    assert rep.typology_preserved is False
    assert rep.is_valid is False


def test_dropped_location_detected():
    ir = _ir_office_energy_geo()
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile = 'Ufficio' "
           "AND classe_target_ape IN ('A1','A2','A3','A4')")
    rep = validate_sql_semantics(sql, ir, SCHEMA, METADATA)
    assert rep.location_filter_preserved is False
    assert rep.is_valid is False


def test_zero_addition_violation_detected():
    # SQL adds a surface_area filter that no agent asked for.
    ir = _ir_office_energy_geo()
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile = 'Ufficio' "
           "AND classe_target_ape IN ('A1','A2','A3','A4') "
           "AND surface_area >= 5000 "
           "AND haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0")
    rep = validate_sql_semantics(sql, ir, SCHEMA, METADATA)
    assert "surface_area" in rep.extra_conditions
    assert "surface_area" in rep.zero_addition_violations


def test_real_trace_example_faithful():
    """The exact SQL from the 2026-06-14 trace should validate cleanly against its IR."""
    ir = ConstraintIR(
        query="Offices near Porta Nuova with energy class A",
        entries=[
            make_predicate_entry(index=1, source_agent="energy",
                                 requirement={"target_column": "classe_target_ape",
                                              "operator": "IN", "value": ["A1", "A2", "A3", "A4"]}),
            make_geo_entry(index=1, lat=45.0622, lon=7.6785, radius_km=3.0),
        ],
    )
    sql = ("SELECT * FROM ESTATES WHERE classe_target_ape IN ('A1', 'A2', 'A3', 'A4') "
           "AND haversine_km(latitudine, longitudine, 45.0622, 7.6785) <= 3.0 "
           "ORDER BY haversine_km(latitudine, longitudine, 45.0622, 7.6785) ASC")
    rep = validate_sql_semantics(sql, ir, SCHEMA, METADATA)
    assert rep.is_valid is True
    assert rep.extra_conditions == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
