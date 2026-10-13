"""
Tests for safe column-alias repair (v2).

Run:
    python tests/test_ir_sql_repair.py
    pytest tests/test_ir_sql_repair.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import duckdb
import pandas as pd

from app.services.llm.ir_sql_repair import repair_column_aliases
from app.utils.helpers import haversine_km


DB_SCHEMA = {
    "types": {
        "tipologia_bene_immobile": "VARCHAR",
        "superficie_di_riferimento_mq": "DOUBLE",
        "mobilita": "DOUBLE",
        "note": "VARCHAR",
        "latitudine": "DOUBLE",
        "longitudine": "DOUBLE",
    }
}


def test_repairs_english_surface_column():
    sql = "SELECT * FROM ESTATES WHERE surface_area >= 1000"
    res = repair_column_aliases(sql, DB_SCHEMA)
    assert res.changed
    assert "superficie_di_riferimento_mq >= 1000" in res.sql
    assert "surface_area" not in res.sql
    assert res.repairs == [{"from": "surface_area", "to": "superficie_di_riferimento_mq"}]


def test_repairs_multiple_columns():
    sql = "SELECT * FROM ESTATES WHERE surface_area >= 200 AND mobility >= 75"
    res = repair_column_aliases(sql, DB_SCHEMA)
    assert res.changed
    assert "superficie_di_riferimento_mq >= 200" in res.sql
    assert "mobilita >= 75" in res.sql
    assert len(res.repairs) == 2


def test_no_change_when_already_correct():
    sql = "SELECT * FROM ESTATES WHERE superficie_di_riferimento_mq >= 1000"
    res = repair_column_aliases(sql, DB_SCHEMA)
    assert not res.changed
    assert res.repairs == []
    assert res.sql == sql


def test_unresolved_column_is_recorded_not_changed():
    sql = "SELECT * FROM ESTATES WHERE made_up_col = 5"
    res = repair_column_aliases(sql, DB_SCHEMA)
    assert not res.changed
    assert "made_up_col" in res.unresolved_columns


def test_string_literal_not_touched():
    # 'surface_area' appears as a VALUE, not a column -> must be left alone.
    sql = "SELECT * FROM ESTATES WHERE note = 'surface_area is big'"
    res = repair_column_aliases(sql, DB_SCHEMA)
    assert not res.changed
    assert "'surface_area is big'" in res.sql


def test_repaired_sql_executes_when_original_would_fail():
    df = pd.DataFrame({
        "tipologia_bene_immobile": ["Uffici e assimilabili", "Altro"],
        "superficie_di_riferimento_mq": [1500.0, 100.0],
        "mobilita": [80.0, 10.0],
        "note": ["x", "y"],
        "latitudine": [45.07, 45.5],
        "longitudine": [7.66, 7.9],
    })
    bad_sql = "SELECT * FROM ESTATES WHERE surface_area >= 1000"

    con = duckdb.connect(":memory:")
    con.create_function("haversine_km", haversine_km, return_type="FLOAT")
    con.register("ESTATES", df)

    # Original fails (column does not exist).
    failed = False
    try:
        con.execute(bad_sql).fetchdf()
    except Exception:
        failed = True
    assert failed, "expected the un-repaired SQL to fail"

    # Repaired runs and filters correctly.
    res = repair_column_aliases(bad_sql, DB_SCHEMA)
    out = con.execute(res.sql).fetchdf()
    con.close()
    assert len(out) == 1
    assert out.iloc[0]["superficie_di_riferimento_mq"] == 1500.0


def test_empty_or_unparseable_sql_is_safe():
    assert repair_column_aliases("", DB_SCHEMA).changed is False
    assert repair_column_aliases("not a query at all", DB_SCHEMA).changed is False


def test_no_schema_means_no_repair():
    sql = "SELECT * FROM ESTATES WHERE surface_area >= 1000"
    res = repair_column_aliases(sql, {})
    assert not res.changed


LABELS = {"tipologia_bene_immobile": {
    "Collegi, luoghi di ricovero, case di pena, caserme, conventi",
    "Abitazioni adibite a residenza con carattere continuativo",
    "Uffici e assimilabili",
}}
COLLEGI = "Collegi, luoghi di ricovero, case di pena, caserme, conventi"


def test_split_label_is_merged_back():
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile IN ('Collegi', 'luoghi di ricovero', "
           "'case di pena', 'caserme', 'conventi')")
    res = repair_column_aliases(sql, DB_SCHEMA, categorical_values=LABELS)
    assert res.changed and f"IN ('{COLLEGI}')" in res.sql
    assert res.repairs[-1]["kind"] == "split_literal"


def test_split_label_next_to_a_valid_label():
    sql = ("SELECT * FROM ESTATES WHERE tipologia_bene_immobile IN "
           "('Abitazioni adibite a residenza con carattere continuativo', 'Collegi', 'luoghi di ricovero', "
           "'case di pena', 'caserme', 'conventi')")
    res = repair_column_aliases(sql, DB_SCHEMA, categorical_values=LABELS)
    assert f"'Abitazioni adibite a residenza con carattere continuativo', '{COLLEGI}'" in res.sql


def test_fragments_that_form_no_label_are_left_alone():
    sql = "SELECT * FROM ESTATES WHERE tipologia_bene_immobile IN ('Collegi', 'caserme')"
    res = repair_column_aliases(sql, DB_SCHEMA, categorical_values=LABELS)
    assert not res.changed and res.sql == sql


def test_correct_labels_and_no_values_are_untouched():
    sql = f"SELECT * FROM ESTATES WHERE tipologia_bene_immobile IN ('{COLLEGI}', 'Uffici e assimilabili')"
    assert not repair_column_aliases(sql, DB_SCHEMA, categorical_values=LABELS).changed
    split = "SELECT * FROM ESTATES WHERE tipologia_bene_immobile IN ('Collegi', 'luoghi di ricovero')"
    assert not repair_column_aliases(split, DB_SCHEMA).changed       # no categorical values given


def test_merged_label_matches_rows():
    df = pd.DataFrame({"tipologia_bene_immobile": [COLLEGI, "Uffici e assimilabili"], "id": [1, 2]})
    sql = ("SELECT id FROM ESTATES WHERE tipologia_bene_immobile IN ('Collegi', 'luoghi di ricovero', "
           "'case di pena', 'caserme', 'conventi')")
    con = duckdb.connect()
    con.register("ESTATES", df)
    assert len(con.execute(sql).df()) == 0                          # the split literals match nothing
    fixed = repair_column_aliases(sql, DB_SCHEMA, categorical_values=LABELS).sql
    assert con.execute(fixed).df()["id"].tolist() == [1]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
