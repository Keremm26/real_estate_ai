"""
Tests for agent-vocabulary -> dataset column resolution (no LLM calls).

The agents emit canonical English column names (surface_area, mobility, ...)
while the estates dataset has Italian columns (superficie_di_riferimento_mq,
mobilita, ...). Before this fix the rankers skipped every such requirement
(score 0 for all buildings) and the agents' prompt statistics were empty.

Covers: the shared COLUMN_ALIASES map + resolve_column, column statistics keyed
by the agent name, requirement resolution / "found" predicate in graph_agent,
the four rankers scoring on an Italian-only frame, the two regulatory ranker
crashes, and the widened TriSQL SQL repair.

Run:
    python tests/test_column_aliases.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

from app.core.config import settings
from app.core.constants import (
    COLUMN_ALIASES,
    DB_METADATA,
    REGULATORY_AGENT_COLUMNS,
    resolve_column,
    update_runtime_metadata,
)
from app.services.analysis.executor import execute_sql_query
from app.services.llm.agents.building_agent import BuildingAgent
from app.services.llm.agents.energy_agent import EnergyAgent
from app.services.llm.agents.graph_agent import (
    GraphOrchestratorAgent,
    _count_scorable,
    _resolve_requirements,
)
from app.services.llm.agents.proximity_agent import ProximityAgent
from app.services.llm.agents.regulatory_agent import RegulatoryAgent
from app.services.llm.constraint_ir import ConstraintIR, assign_severity, make_predicate_entry
from app.services.llm.constraint_validation import validate_sql_semantics
from app.services.llm.ir_sql_renderer import make_column_resolver, render_predicate
from app.services.llm.ir_sql_repair import repair_column_aliases
from app.utils.scoring import calculate_equality_score


# A small frame with the dataset's ITALIAN column names only.
DF = pd.DataFrame({
    "id": ["1", "2", "3", "4"],
    "superficie_di_riferimento_mq": [40.0, 120.0, 600.0, 1500.0],
    "tipologia_bene_immobile": ["Abitazioni", "Uffici", "Abitazioni", "Uffici"],
    "classe_energetica_ape": ["G", "C", "A2", "E"],
    "mobilita": [10.0, 50.0, 80.0, 95.0],
    "epoca_costruzione": ["1960", "1975", "1990", "2010"],
})
COLS = set(DF.columns)


def _agent(cls):
    """Build an agent without __init__, so no LLM client is created."""
    return cls.__new__(cls)


def _nonzero(out, score_col):
    return int((out[score_col] > 0).sum())


# ---------------------------------------------------------------------------
# resolve_column / COLUMN_ALIASES

def test_resolve_canonical_to_dataset_name():
    assert resolve_column("surface_area", COLS) == "superficie_di_riferimento_mq"
    assert resolve_column("mobility", COLS) == "mobilita"
    assert resolve_column("construction_period", COLS) == "epoca_costruzione"


def test_resolve_literal_match_wins_and_english_schema_still_works():
    assert resolve_column("superficie_di_riferimento_mq", COLS) == "superficie_di_riferimento_mq"
    # An English-column dataset (create_estate_dataset.py output) needs no alias.
    assert resolve_column("surface_area", {"surface_area", "mobility"}) == "surface_area"


def test_resolve_unknown_or_columnless_names_return_none():
    assert resolve_column("legal_nature", COLS) is None
    assert resolve_column("cultural_constraint", COLS) is None
    assert resolve_column("made_up_col", COLS) is None
    assert resolve_column(None, COLS) is None


def test_target_class_is_never_aliased_to_energy_class():
    assert resolve_column("energy_class", {"classe_target_ape"}) is None


def test_no_name_belongs_to_two_alias_groups():
    seen = {}
    for canonical, variants in COLUMN_ALIASES.items():
        for name in {canonical} | variants:
            assert name not in seen, f"{name} in both {seen.get(name)} and {canonical}"
            seen[name] = canonical


# ---------------------------------------------------------------------------
# column statistics (what the agents see in their prompts)

def test_statistics_are_keyed_by_the_requested_agent_name():
    stats = GraphOrchestratorAgent._get_column_statistics(
        None, columns=["surface_area", "mobility", "legal_nature"], dataset_df=DF)
    assert stats["total_records"] == 4
    assert stats["surface_area"]["max"] == 1500.0
    assert stats["mobility"]["min"] == 10.0
    assert "legal_nature" not in stats  # no such column: skipped, as before


def test_statistics_report_real_record_count_when_nothing_resolves():
    stats = GraphOrchestratorAgent._get_column_statistics(
        None, columns=["legal_nature", "cultural_constraint"], dataset_df=DF)
    assert stats["total_records"] == 4  # was 0: the agent was told the dataset is empty


def test_statistics_do_not_mutate_the_input_frame():
    before = list(DF.columns)
    GraphOrchestratorAgent._get_column_statistics(None, columns=["surface_area"], dataset_df=DF)
    assert list(DF.columns) == before


# ---------------------------------------------------------------------------
# requirement resolution + "found" predicate (graph_agent)

def test_resolve_requirements_returns_copies_and_maps_legacy_keys():
    reqs = [{"target_column": "surface_area", "operator": ">=", "value": 100},
            {"colonna_target": "mobility", "operatore": ">=", "valore": 70},
            {"target_column": "legal_nature", "operator": "==", "value": "x"}]
    out = _resolve_requirements(reqs, COLS)
    assert out[0]["target_column"] == "superficie_di_riferimento_mq"
    assert (out[1]["target_column"], out[1]["operator"], out[1]["value"]) == ("mobilita", ">=", 70)
    assert out[2]["target_column"] == "legal_nature"  # unresolvable: kept, rankers skip it
    assert reqs[0]["target_column"] == "surface_area"  # agent output untouched


def test_count_scorable_ignores_columnless_and_excluded():
    reqs = [{"target_column": "legal_nature", "value": "x"},
            {"target_column": "property_type", "value": ["Uffici"]}]
    assert _count_scorable(reqs, DF) == 1
    assert _count_scorable(reqs, DF, exclude={"tipologia_bene_immobile"}) == 0


def test_found_predicate_matches_what_rankers_score():
    regulatory_columns = [resolve_column(c, COLS) or c for c in REGULATORY_AGENT_COLUMNS]
    # no value (threshold pending documents): not scorable -> agent not "found"
    assert _count_scorable([{"target_column": "surface_area", "operator": ">=", "value": None}], DF) == 0
    # column outside the regulatory ranker's allowed list
    assert _count_scorable([{"target_column": "energy_class", "operator": "IN", "value": ["B"]}],
                           DF, allowed_columns=regulatory_columns) == 0
    # text value on a numeric column (logged agent output: construction_period >= 'D')
    assert _count_scorable([{"target_column": "construction_period", "operator": ">=", "value": "D"}], DF) == 0
    # proximity scores by min-max when no threshold is given
    assert _count_scorable([{"target_column": "mobility", "value": None}], DF, require_value=False) == 1


def test_non_string_target_column_does_not_crash():
    reqs = [{"target_column": ["mobility", "green"], "operator": ">=", "value": 70}]
    assert resolve_column(["mobility"], COLS) is None
    assert _resolve_requirements(reqs, COLS)[0]["target_column"] is None
    assert _count_scorable(reqs, DF) == 0


# ---------------------------------------------------------------------------
# rankers on the Italian-only frame, with English requirement names

def test_building_ranker_scores_surface_area():
    reqs = _resolve_requirements([{"target_column": "surface_area", "operator": ">=", "value": 100}], COLS)
    out = _agent(BuildingAgent).run(mode="ranking", df=DF.copy(), ranked_typologies=[],
                                    requirements=reqs, global_stats={})
    assert _nonzero(out, "building_score") == 3


def test_energy_ranker_scores_energy_class():
    reqs = _resolve_requirements([{"target_column": "energy_class", "operator": "IN",
                                   "value": ["A2", "B", "C"]}], COLS)
    out = _agent(EnergyAgent).run(mode="ranking", df=DF.copy(), requirements=reqs, global_stats={})
    assert _nonzero(out, "energy_score") == 2


def test_proximity_ranker_scores_mobility():
    reqs = _resolve_requirements([{"target_column": "mobility", "operator": ">=", "value": 70}], COLS)
    out = _agent(ProximityAgent).run(mode="ranking", df=DF.copy(), requirements=reqs, global_stats={})
    assert _nonzero(out, "proximity_score") == 2


def _regulatory(reqs, global_stats=None):
    regulatory_columns = [resolve_column(c, COLS) or c for c in REGULATORY_AGENT_COLUMNS]
    return _agent(RegulatoryAgent).run(
        mode="ranking", df=DF.copy(), requirements=_resolve_requirements(reqs, COLS),
        available_columns=regulatory_columns, global_stats=global_stats or {})


def test_regulatory_ranker_scores_surface_threshold():
    out = _regulatory([{"target_column": "surface_area", "operator": ">=", "value": 500}])
    assert _nonzero(out, "regulatory_score") == 2


def test_regulatory_numeric_equality_no_longer_crashes():
    # Used undefined col_stats / diff (NameError -> whole regulatory score 0).
    stats = {"superficie_di_riferimento_mq": {"min": 40.0, "max": 1500.0}}
    out = _regulatory([{"target_column": "surface_area", "operator": "==", "value": 120}], stats)
    assert out.loc[out["id"] == "2", "regulatory_score"].iloc[0] == 100.0
    assert out["regulatory_score"].min() < 100.0


def test_regulatory_categorical_no_longer_crashes():
    # np.where(mask, 1, "N/A") raised DTypePromotionError under numpy >= 2.
    out = _regulatory([{"target_column": "property_type", "operator": "IN", "value": ["Uffici"]}])
    assert _nonzero(out, "regulatory_score") == 2


def test_building_ranker_skips_unscorable_instead_of_halving_the_score():
    typology_only = _agent(BuildingAgent).run(
        mode="ranking", df=DF.copy(), ranked_typologies=["Uffici"], requirements=[], global_stats={})
    with_bad_reqs = _agent(BuildingAgent).run(
        mode="ranking", df=DF.copy(), ranked_typologies=["Uffici"], global_stats={},
        requirements=_resolve_requirements([
            {"target_column": "surface_area", "operator": "<=", "value": None},
            {"target_column": "construction_period", "operator": ">=", "value": "D"},
        ], COLS))
    assert list(with_bad_reqs["building_score"]) == list(typology_only["building_score"])


def test_building_ranker_scores_between_as_a_range():
    reqs = _resolve_requirements([{"target_column": "surface_area", "operator": "BETWEEN",
                                   "value": [100, 700]}], COLS)
    out = _agent(BuildingAgent).run(mode="ranking", df=DF.copy(), ranked_typologies=[],
                                    requirements=reqs, global_stats={})
    scores = dict(zip(out["id"], out["building_score"]))
    assert scores["2"] == 100.0 and scores["3"] == 100.0   # 120 and 600 m2: inside
    assert scores["1"] < 100.0 and scores["4"] == 0.0      # 40 m2 near, 1500 m2 far


def test_regulatory_between_and_single_equals():
    out = _regulatory([{"target_column": "surface_area", "operator": "BETWEEN", "value": [100, 700]}])
    assert _nonzero(out, "regulatory_score") >= 2
    stats = {"superficie_di_riferimento_mq": {"min": 40.0, "max": 1500.0}}
    out = _regulatory([{"target_column": "surface_area", "operator": "=", "value": 120}], stats)
    assert out.loc[out["id"] == "2", "regulatory_score"].iloc[0] == 100.0  # was a string compare -> 0


def test_equality_score_is_robust_to_outliers():
    stats = {"min": 0.5, "max": 15881348.49, "percentiles": {"25%": 49.4, "75%": 106.2}}
    scores = calculate_equality_score(pd.Series([100.0, 105.0, 400.0, 8912.0]), 100, stats)
    assert scores.iloc[0] == 100.0 and scores.iloc[1] > 80.0
    assert scores.iloc[2] == 0.0 and scores.iloc[3] == 0.0  # min-max range scored these ~100


def test_statistics_record_count_on_the_file_path_branch():
    path = settings.DATASET_FULL if os.path.isabs(settings.DATASET_FULL) else \
        os.path.join(os.path.dirname(__file__), "..", settings.DATASET_FULL)
    if not os.path.exists(path):
        return  # dataset not available in this environment
    stats = GraphOrchestratorAgent._get_column_statistics(
        None, columns=["legal_nature"], dataset_path=path)
    assert stats["total_records"] > 0


# ---------------------------------------------------------------------------
# TriSQL: severity, rendering and validation with the shared map

def test_canonical_building_identity_columns_are_hard():
    for col in ("property_type", "cadastral_sheet", "cadastral_units_count", "tipologia_bene_immobile", "foglio"):
        assert assign_severity("building", col) == ("hard", False), col
    assert assign_severity("building", "surface_area") == ("soft", True)


def test_renderer_casts_text_column_compared_with_numbers():
    schema = {"types": {"epoca_costruzione": "str", "superficie_di_riferimento_mq": "float64"}}
    resolve = make_column_resolver(schema)
    entry = make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "construction_period", "operator": ">=", "value": 1990})
    clause, skip = render_predicate(entry, resolve)
    assert skip is None and clause == "TRY_CAST(epoca_costruzione AS DOUBLE) >= 1990.0"
    df, err = execute_sql_query(f"SELECT * FROM ESTATES WHERE {clause}", DF, dataset_path=None)
    assert err is None and sorted(df["id"]) == ["3", "4"]  # built 1990 and 2010


def test_validator_grounds_sql_columns_literally_and_reports_alias_mismatch():
    schema = {"types": {c: "float64" for c in COLS}}
    ir = ConstraintIR(query="t", entries=[make_predicate_entry(index=1, source_agent="building", requirement={
        "target_column": "surface_area", "operator": ">=", "value": 100})])
    report = validate_sql_semantics("SELECT * FROM ESTATES WHERE surface_area >= 100", ir, schema, {})
    assert report.invalid_columns == ["surface_area"]          # DuckDB would reject it
    assert report.alias_mismatch_columns == ["surface_area"]   # ...and repair can fix it
    assert not report.is_valid
    fixed = validate_sql_semantics("SELECT * FROM ESTATES WHERE superficie_di_riferimento_mq >= 100", ir, schema, {})
    assert fixed.invalid_columns == [] and fixed.hard_constraints_preserved is True


def test_validator_ignores_names_the_query_defines():
    schema = {"types": {c: "float64" for c in COLS}}
    ir = ConstraintIR(query="t", entries=[])
    report = validate_sql_semantics(
        "SELECT id, mobilita * 2 AS score FROM ESTATES ORDER BY score DESC", ir, schema, {})
    assert report.invalid_columns == []


def test_runtime_metadata_resolves_agent_names():
    saved = {k: DB_METADATA.get(k) for k in ("filterable_columns", "fields", "_last_updated")}
    try:
        update_runtime_metadata(DF)
        assert "surface_area" in DB_METADATA["filterable_columns"]
        assert DB_METADATA["fields"]["surface_area"]["max"] == 1500.0
        assert "Uffici" in DB_METADATA["fields"]["property_type"]["values"]
    finally:
        DB_METADATA.update(saved)


# ---------------------------------------------------------------------------
# TriSQL SQL repair now covers the widened map

def test_sql_repair_covers_newly_aliased_columns():
    schema = {"types": {c: "VARCHAR" for c in ["latitudine", "longitudine", "epoca_costruzione"]}}
    res = repair_column_aliases(
        "SELECT * FROM ESTATES WHERE latitude > 45 AND longitude < 8 AND construction_period = '1960'",
        schema)
    assert res.changed
    for name in ("latitudine", "longitudine", "epoca_costruzione"):
        assert name in res.sql
    assert not res.unresolved_columns


def test_sql_repair_casts_text_column_compared_with_numbers():
    schema = {"types": {"epoca_costruzione": "str", "superficie_di_riferimento_mq": "float64"}}
    res = repair_column_aliases(
        "SELECT * FROM ESTATES WHERE construction_period >= 1990 AND surface_area BETWEEN 100 AND 700",
        schema)
    assert "TRY_CAST(epoca_costruzione AS DOUBLE) >= 1990" in res.sql
    assert {"from": "epoca_costruzione", "to": "TRY_CAST(epoca_costruzione AS DOUBLE)"} in res.repairs
    df, err = execute_sql_query(res.sql, DF, dataset_path=None)
    assert err is None and list(df["id"]) == ["3"]  # 1990, 600 m2 (2010 one is 1500 m2)


def test_sql_repair_leaves_names_the_query_defines():
    schema = {"types": {c: "float64" for c in COLS} | {"id": "str"}}
    res = repair_column_aliases(
        "SELECT id, mobilita AS energy_score_total FROM ESTATES ORDER BY energy_score_total DESC", schema)
    assert not res.changed  # was rewritten to ORDER BY ape_score_total
    res = repair_column_aliases(
        "WITH p AS (SELECT 45.07 AS latitude) SELECT e.id FROM ESTATES e, p WHERE p.latitude > 45", schema)
    assert "p.latitude" in res.sql  # CTE column, not the dataset's latitudine


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
