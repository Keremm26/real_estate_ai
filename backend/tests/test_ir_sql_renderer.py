"""
Tests for the deterministic IR -> SQL renderer (v2).

Covers: column resolution (English -> Italian dataset names), each operator,
geo/haversine rendering, value quoting/escaping, skip records, hard-only policy,
and that the output actually parses + executes on DuckDB with a haversine UDF.

Run:
    python tests/test_ir_sql_renderer.py
    pytest tests/test_ir_sql_renderer.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import duckdb
import pandas as pd
import sqlglot

from app.services.llm.constraint_ir import (
    ConstraintIR,
    make_geo_entry,
    make_predicate_entry,
)
from app.services.llm.ir_sql_renderer import (
    make_column_resolver,
    render_geo,
    render_predicate,
    render_sql,
    select_entries,
)
from app.utils.helpers import haversine_km


# A schema that mirrors the real dataset's Italian column names.
DB_SCHEMA = {
    "types": {
        "tipologia_bene_immobile": "VARCHAR",
        "superficie_di_riferimento_mq": "DOUBLE",
        "classe_target_ape": "VARCHAR",
        "mobilita": "DOUBLE",
        "commerciale": "DOUBLE",
        "latitudine": "DOUBLE",
        "longitudine": "DOUBLE",
    }
}
RESOLVE = make_column_resolver(DB_SCHEMA)


def _ir(entries):
    return ConstraintIR(query="t", entries=entries)


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------

def test_resolver_maps_english_to_dataset():
    assert RESOLVE("surface_area") == "superficie_di_riferimento_mq"
    assert RESOLVE("mobility") == "mobilita"
    assert RESOLVE("commercial") == "commerciale"


def test_resolver_passes_through_real_columns():
    assert RESOLVE("tipologia_bene_immobile") == "tipologia_bene_immobile"
    assert RESOLVE("classe_target_ape") == "classe_target_ape"


def test_resolver_returns_none_for_unknown():
    assert RESOLVE("totally_made_up_column") is None


def test_resolver_trusts_name_when_no_schema():
    r = make_column_resolver({})
    assert r("anything") == "anything"


# ---------------------------------------------------------------------------
# Predicate rendering
# ---------------------------------------------------------------------------

def test_render_in_with_strings_quotes_each():
    e = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "tipologia_bene_immobile", "operator": "IN",
        "value": ["Uffici e assimilabili", "Attività commerciali e assimilabili"]})
    clause, reason = render_predicate(e, RESOLVE)
    assert reason is None
    assert clause == ("tipologia_bene_immobile IN "
                      "('Uffici e assimilabili', 'Attività commerciali e assimilabili')")


def test_render_comparison_numeric_unquoted():
    e = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "surface_area", "operator": ">=", "value": 1000})
    clause, reason = render_predicate(e, RESOLVE)
    assert clause == "superficie_di_riferimento_mq >= 1000"
    assert reason is None


def test_render_between():
    e = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "surface_area", "operator": "BETWEEN", "value": [50, 80]})
    clause, _ = render_predicate(e, RESOLVE)
    assert clause == "superficie_di_riferimento_mq BETWEEN 50 AND 80"


def test_string_value_escapes_single_quote():
    e = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "tipologia_bene_immobile", "operator": "=", "value": "L'Albergo"})
    clause, _ = render_predicate(e, RESOLVE)
    assert clause == "tipologia_bene_immobile = 'L''Albergo'"


def test_unknown_column_is_skipped():
    e = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "made_up", "operator": ">=", "value": 5})
    clause, reason = render_predicate(e, RESOLVE)
    assert clause is None
    assert "not in schema" in reason


def test_none_value_comparison_skipped():
    # Regulatory min-surface with a pending threshold -> cannot render.
    e = make_predicate_entry(index=1, source_agent="regulatory", requirement={
        "target_column": "surface_area", "operator": ">=", "value": None})
    clause, reason = render_predicate(e, RESOLVE)
    assert clause is None
    assert "no value" in reason


# ---------------------------------------------------------------------------
# Geo rendering
# ---------------------------------------------------------------------------

def test_render_geo_uses_haversine():
    e = make_geo_entry(index=1, lat=45.07, lon=7.66, radius_km=3.0)
    clause, reason = render_geo(e)
    assert reason is None
    assert clause == "haversine_km(latitudine, longitudine, 45.07, 7.66) <= 3.0"


def test_render_geo_default_radius_when_missing():
    e = make_geo_entry(index=1, lat=45.0, lon=7.6, radius_km=None)
    clause, _ = render_geo(e, default_radius_km=2.5)
    assert clause.endswith("<= 2.5")


# ---------------------------------------------------------------------------
# Full render + policy
# ---------------------------------------------------------------------------

def _full_ir():
    return _ir([
        make_predicate_entry(index=1, source_agent="building", requirement={
            "target_column": "tipologia_bene_immobile", "operator": "IN",
            "value": ["Uffici e assimilabili"]}),          # hard
        make_predicate_entry(index=2, source_agent="building", requirement={
            "target_column": "surface_area", "operator": ">=", "value": 200}),  # soft
        make_predicate_entry(index=1, source_agent="proximity", requirement={
            "target_column": "mobility", "operator": ">=", "value": 75}),        # soft
        make_geo_entry(index=1, lat=45.07, lon=7.66, radius_km=3.0),
    ])


def test_full_render_includes_all():
    res = render_sql(_full_ir(), resolve_column=RESOLVE)
    assert res.sql.startswith("SELECT * FROM ESTATES WHERE ")
    assert "tipologia_bene_immobile IN ('Uffici e assimilabili')" in res.sql
    assert "superficie_di_riferimento_mq >= 200" in res.sql
    assert "mobilita >= 75" in res.sql
    assert "haversine_km(" in res.sql
    assert len(res.rendered_ids) == 4
    assert res.skipped == []


def test_hard_only_drops_soft_keeps_geo_and_typology():
    res = render_sql(_full_ir(), resolve_column=RESOLVE, hard_only=True)
    assert "tipologia_bene_immobile IN" in res.sql       # hard kept
    assert "superficie" not in res.sql                    # soft dropped
    assert "mobilita" not in res.sql                       # soft dropped
    assert "haversine_km(" in res.sql                      # geo anchor kept


def test_limit_appended():
    res = render_sql(_full_ir(), resolve_column=RESOLVE, limit=500)
    assert res.sql.endswith(" LIMIT 500")


def test_empty_ir_returns_no_sql():
    res = render_sql(_ir([]), resolve_column=RESOLVE)
    assert res.sql is None
    assert res.is_empty


def test_failures_never_rendered():
    ir = _ir([])
    ir.failures.append(make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "surface_area", "operator": ">=", "value": 10}))
    # failures live in ir.failures, not ir.entries -> selection ignores them
    assert select_entries(ir) == []


# ---------------------------------------------------------------------------
# The rendered SQL must parse AND execute on DuckDB
# ---------------------------------------------------------------------------

def test_rendered_sql_parses_and_executes():
    df = pd.DataFrame({
        "tipologia_bene_immobile": ["Uffici e assimilabili", "Altro"],
        "superficie_di_riferimento_mq": [250.0, 100.0],
        "mobilita": [80.0, 10.0],
        "commerciale": [50.0, 50.0],
        "classe_target_ape": ["A1", "G"],
        "latitudine": [45.07, 45.50],
        "longitudine": [7.66, 7.90],
    })
    res = render_sql(_full_ir(), resolve_column=RESOLVE)

    # 1. parses
    assert sqlglot.parse_one(res.sql, read="duckdb") is not None

    # 2. executes and filters to the one matching row
    con = duckdb.connect(":memory:")
    con.create_function("haversine_km", haversine_km, return_type="FLOAT")
    con.register("ESTATES", df)
    out = con.execute(res.sql).fetchdf()
    con.close()
    assert len(out) == 1
    assert out.iloc[0]["tipologia_bene_immobile"] == "Uffici e assimilabili"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
