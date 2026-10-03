"""
Tests for the ConstraintIR data layer (v1, observe-only).

Run either way:
    python tests/test_constraint_ir.py        # plain script
    pytest tests/test_constraint_ir.py         # if pytest is installed

These tests use the real requirement dict shapes seen in agent traces:
    {"target_column": ..., "operator": ..., "value": ..., "description": ...}
"""

import os
import sys

# Make "app" importable when run as a plain script from the backend folder.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.constraint_ir import (
    ConstraintIR,
    ConstraintEntry,
    assign_severity,
    make_predicate_entry,
    make_geo_entry,
    make_failure_entry,
    build_constraint_ir,
)


# ---------------------------------------------------------------------------
# Severity fallback rules
# ---------------------------------------------------------------------------

def test_regulatory_is_hard_and_not_relaxable():
    sev, relaxable = assign_severity("regulatory", "surface_area")
    assert sev == "hard"
    assert relaxable is False


def test_building_identity_column_is_hard():
    sev, relaxable = assign_severity("building", "tipologia_bene_immobile")
    assert sev == "hard"
    assert relaxable is False


def test_building_surface_is_soft():
    sev, relaxable = assign_severity("building", "surface_area")
    assert sev == "soft"
    assert relaxable is True


def test_energy_is_soft():
    sev, relaxable = assign_severity("energy", "classe_target_ape")
    assert sev == "soft"
    assert relaxable is True


def test_unknown_agent_goes_to_human_review():
    sev, _ = assign_severity("mystery", "some_col")
    assert sev == "human_review"


def test_agent_emitted_severity_overrides_fallback():
    # Even though energy normally -> soft, an explicit severity wins.
    req = {"target_column": "classe_target_ape", "severity": "hard"}
    sev, relaxable = assign_severity("energy", "classe_target_ape", req)
    assert sev == "hard"
    assert relaxable is False  # default for hard


# ---------------------------------------------------------------------------
# Predicate entries (real shapes)
# ---------------------------------------------------------------------------

def test_predicate_entry_scalar_value():
    req = {"target_column": "surface_area", "operator": ">=", "value": 70,
           "description": "min area 70 sqm"}
    e = make_predicate_entry(index=1, source_agent="building", requirement=req)
    assert e.kind == "predicate"
    assert e.constraint_id == "building_001"
    assert e.target_column == "surface_area"
    assert e.operator == ">="
    assert e.value == 70
    assert e.severity == "soft"
    assert e.original_requirement == req  # raw dict preserved


def test_predicate_entry_list_value_in_clause():
    # energy IN ('A1','A2','A3','A4','B')
    req = {"target_column": "classe_target_ape", "operator": "IN",
           "value": ["A1", "A2", "A3", "A4", "B"]}
    e = make_predicate_entry(index=2, source_agent="energy", requirement=req)
    assert e.operator == "IN"
    assert e.value == ["A1", "A2", "A3", "A4", "B"]
    assert e.constraint_id == "energy_002"


def test_predicate_inherits_ranking_weight():
    req = {"target_column": "surface_area", "operator": ">=", "value": 70}
    e = make_predicate_entry(index=1, source_agent="building",
                             requirement=req, ranking_weight=0.31)
    assert e.ranking_weight == 0.31


# ---------------------------------------------------------------------------
# Geo entries
# ---------------------------------------------------------------------------

def test_geo_entry():
    e = make_geo_entry(index=1, lat=45.0622, lon=7.6785, radius_km=3.0)
    assert e.kind == "geo"
    assert e.source_agent == "location"
    assert e.lat == 45.0622
    assert e.radius_km == 3.0
    assert e.lat_column == "latitudine"
    assert e.severity == "soft"


# ---------------------------------------------------------------------------
# Failure records (Gap 1 fix: never drop silently)
# ---------------------------------------------------------------------------

def test_failure_entry_keeps_evidence():
    e = make_failure_entry(index=1, source_agent="building",
                           raw_text="{ broken json ...", reason="safe_extract_json returned None")
    assert e.parse_status == "failed"
    assert e.severity == "human_review"
    assert "broken json" in e.raw_text_preview
    assert e.failure_reason == "safe_extract_json returned None"


# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------

def test_ir_helpers_split_by_kind_and_severity():
    ir = ConstraintIR(
        query="offices near Porta Nuova, energy A, big",
        entries=[
            make_predicate_entry(index=1, source_agent="building",
                                 requirement={"target_column": "tipologia_bene_immobile",
                                              "operator": "=", "value": "Ufficio"}),
            make_predicate_entry(index=1, source_agent="energy",
                                 requirement={"target_column": "classe_target_ape",
                                              "operator": "IN", "value": ["A1", "A2"]}),
            make_geo_entry(index=1, lat=45.06, lon=7.67, radius_km=3.0),
        ],
        failures=[
            make_failure_entry(index=1, source_agent="regulatory",
                               raw_text="...", reason="None"),
        ],
    )
    assert len(ir.predicates()) == 2
    assert len(ir.geo()) == 1
    assert len(ir.hard()) == 1                 # only the building typology
    assert ir.hard()[0].target_column == "tipologia_bene_immobile"
    assert ir.has_failures() is True


def test_ir_roundtrips_through_json():
    ir = ConstraintIR(
        query="test",
        entries=[make_geo_entry(index=1, lat=1.0, lon=2.0, radius_km=5.0)],
    )
    dumped = ir.model_dump_json()
    restored = ConstraintIR.model_validate_json(dumped)
    assert restored.query == "test"
    assert restored.geo()[0].radius_km == 5.0


# ---------------------------------------------------------------------------
# Pure builder (uses real agent shapes)
# ---------------------------------------------------------------------------

def test_builder_assembles_predicates_geo_and_weights():
    payloads = [
        {
            "source_agent": "building",
            "parsed": {
                "typologies": ["Ufficio", "Ufficio direzionale"],
                "found": True,
                "requirements": [
                    {"target_column": "surface_area", "operator": ">=", "value": 70},
                ],
            },
            "raw_text": "{...}",
        },
        {
            "source_agent": "energy",
            "parsed": {
                "requirements": [
                    {"target_column": "classe_target_ape", "operator": "IN",
                     "value": ["A1", "A2", "A3", "A4", "B"]},
                ],
                "found": True,
            },
            "raw_text": "{...}",
        },
        {
            "source_agent": "regulatory",
            "parsed": {"found": False, "requirements": []},
            "raw_text": "{...}",
        },
    ]
    locations = [{"lat": 45.0622, "lon": 7.6785, "radius_km": 3.0}]
    weights = {"building": 0.3, "energy": 0.25, "location": 0.2}

    ir = build_constraint_ir(query="offices near Porta Nuova, energy A, >=70sqm",
                             agent_payloads=payloads, locations=locations, weights=weights)

    # building: 1 typology predicate + 1 surface predicate; energy: 1; geo: 1
    assert len(ir.predicates()) == 3
    assert len(ir.geo()) == 1

    # typology predicate is hard, inherits building weight
    typ = [e for e in ir.predicates() if e.target_column == "tipologia_bene_immobile"][0]
    assert typ.severity == "hard"
    assert typ.value == ["Ufficio", "Ufficio direzionale"]
    assert typ.ranking_weight == 0.3

    # geo inherits the location weight
    assert ir.geo()[0].ranking_weight == 0.2

    # regulatory had an empty requirements list -> contributes nothing, no failure
    assert ir.has_failures() is False


def test_builder_records_parse_failure_instead_of_dropping():
    payloads = [
        {"source_agent": "building", "parsed": None, "raw_text": "{ broken ..."},
    ]
    ir = build_constraint_ir(query="q", agent_payloads=payloads)
    assert len(ir.entries) == 0
    assert ir.has_failures() is True
    assert ir.failures[0].source_agent == "building"
    assert ir.failures[0].parse_status == "failed"
    assert "broken" in ir.failures[0].raw_text_preview


def test_builder_skips_geo_without_coordinates():
    payloads = []
    locations = [{"lat": None, "lon": None, "radius_km": 3.0}]
    ir = build_constraint_ir(query="q", agent_payloads=payloads, locations=locations)
    assert len(ir.geo()) == 0


def test_builder_on_real_trace_if_available():
    """If an agent trace file is present, build an IR from it and sanity-check."""
    import glob
    import json
    trace_dir = os.path.join(os.path.dirname(__file__), "..", "data", "agent_logs")
    files = sorted(glob.glob(os.path.join(trace_dir, "trace_*.json")))
    if not files:
        return  # no traces in this checkout; skip quietly

    data = json.load(open(files[-1]))
    # pick the FIRST extraction output per agent (agents also appear as scorers)
    name_map = {"building-agent": "building", "energy-agent": "energy",
                "regulatory-agent": "regulatory", "proximity-agent": "proximity"}
    seen = {}
    for ex in data.get("agent_executions", []):
        a = ex.get("agent_name")
        if a in name_map and a not in seen:
            out = ex.get("output", {})
            # extraction outputs are dicts with 'requirements'; scorer outputs are lists
            if isinstance(out, dict):
                seen[a] = out
    payloads = [{"source_agent": name_map[a], "parsed": out, "raw_text": json.dumps(out)}
                for a, out in seen.items()]
    ir = build_constraint_ir(query="trace replay", agent_payloads=payloads)
    # Should not raise, and every entry must have a source agent and severity.
    for e in ir.entries:
        assert e.source_agent
        assert e.severity in ("hard", "soft", "ranking_only", "human_review")


# ---------------------------------------------------------------------------
# Plain-script runner (works without pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
