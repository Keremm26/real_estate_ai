"""
Offline tests for the benchmark analysis in tests/test_suite.py (no LLM calls).

Covers the step-3 fixes: ablation configs disable agents the graph actually
knows, ranking weights are read from what the graph recorded (effective weights
after exclusion), the rank-contribution analysis uses the current agent names,
ranking/knowledge impact read the sensitivity folder, and missing data is
reported as n/a instead of a default that looks like a result.

Run:
    python tests/test_suite_analysis.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# test_suite creates its results folder at import time: keep it out of the repo.
_TMP = tempfile.mkdtemp(prefix="suite_analysis_")
os.environ["EXPERIMENT_RESULTS_DIR"] = _TMP

import tests.test_suite as ts  # noqa: E402

GRAPH_TASK_KEYS = {"ranking", "building", "location", "energy", "proximity", "regulatory"}
WEIGHT_KEYS = {"location", "regulatory", "energy", "building", "proximity"}


def _write(folder: Path, name: str, ranking_ids, **extra):
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"ranking": [{"id": i, "score": 50.0} for i in ranking_ids], **extra}
    (folder / name).write_text(json.dumps(payload))


def test_every_ablation_disables_an_agent_the_graph_knows():
    for cid, disabled, _, _ in ts.SENSITIVITY_CONFIGS:
        for agent in disabled or []:
            assert agent in GRAPH_TASK_KEYS, f"{cid} disables unknown agent {agent!r}"


def test_report_labels_cover_the_agent_ablations():
    agent_ablations = {cid for cid, disabled, _, _ in ts.SENSITIVITY_CONFIGS
                       if disabled and disabled != ["ranking"]}
    assert agent_ablations == {"no_proximity", "no_regulatory", "no_location", "no_energy", "no_building"}


def test_ranking_logic_comes_from_the_graph_record():
    res = {"gemini_responses": {"ranking_weights": {
        "weights": {"location": 0.33, "regulatory": 0.32, "energy": 0.08, "building": 0.16, "proximity": 0.11},
        "effective_weights": {"location": 0.55, "building": 0.27, "proximity": 0.18, "regulatory": 0.0, "energy": 0.0},
        "found_agents": ["location", "building", "proximity"],
        "excluded_agents": ["regulatory", "energy"],
    }}}
    logic = ts.extract_ranking_logic(res)
    assert logic["original_weights"]["regulatory"] == 0.32
    assert logic["effective_weights"] == {"location": 0.55, "building": 0.27, "proximity": 0.18}
    assert set(logic["contributing_agents"]) == {"location", "building", "proximity"}
    assert logic["excluded_agents"] == ["regulatory", "energy"]


def test_ranking_logic_tolerates_missing_record():
    logic = ts.extract_ranking_logic({})
    assert logic["effective_weights"] == {} and logic["discovered_agents"] == []


def test_rank_contribution_uses_current_agent_names():
    folder = Path(tempfile.mkdtemp())
    logic = {"original_weights": {"regulatory": 0.5, "location": 0.3, "energy": 0.2},
             "discovered_agents": ["regulatory", "location"]}
    (folder / "query_a.json").write_text(json.dumps({"ranking_logic": logic}))
    out = ts.analyze_rank_contribution_correlation(folder)
    assert set(out["by_agent"]) == WEIGHT_KEYS
    assert out["by_agent"]["regulatory"]["rate"] == 1.0  # was invisible under 'normative'
    assert out["by_agent"]["energy"]["rate"] == 0.0


def test_ranking_impact_reports_na_without_paired_results():
    empty = Path(tempfile.mkdtemp())
    assert ts.analyze_ranking_impact(empty / "all_enabled", empty / "no_ranking")["mean_iou"] is None


def test_ranking_impact_compares_paired_files():
    root = Path(tempfile.mkdtemp())
    _write(root / "all_enabled", "query_Q1.json", ["1", "2", "3"])
    _write(root / "no_ranking", "query_Q1.json", ["1", "2", "3"])
    out = ts.analyze_ranking_impact(root / "all_enabled", root / "no_ranking")
    assert out["mean_iou"] == 1.0 and out["impact"] == 0.0 and out["sample_size"] == 1


def test_consistency_reports_na_without_trials():
    assert ts.analyze_consistency(Path(tempfile.mkdtemp()))["mean_self_iou"] is None


def test_report_formats_missing_metrics_as_na():
    assert ts._fmt_metric(None) == "n/a"
    assert ts._fmt_metric(0.91234) == "0.912"


# --- curated query set (RAG study) -------------------------------------------

def test_configure_run_separates_arms():
    try:
        root_rag = ts.configure_run("curated", "rag")
        assert root_rag.name == "curated_rag" and ts.rag_config.RETRIEVAL_MODE == "rag"
        root_none = ts.configure_run("curated", "none")
        assert root_none.name == "curated_none" and ts.rag_config.RETRIEVAL_MODE == "none"
        assert ts.configure_run("legacy", None) == ts.results_path   # Marco's layout untouched
    finally:
        ts.rag_config.RETRIEVAL_MODE = "rag"
        ts.configure_run("legacy", None)


def test_prune_handles_curated_ids():
    root = Path(tempfile.mkdtemp())
    for name in ("query_PQ-01_tr1.json", "query_PQ-02.json", "query_111000.json"):
        _write(root / "consistency", name, ["1"])
    df = ts.pd.DataFrame({"query_id": ["PQ-01"], "query": ["q"]})
    ts.prune_obsolete_results(df, root)
    assert sorted(f.name for f in root.rglob("*.json")) == ["query_PQ-01_tr1.json"]


def test_curated_csv_keeps_status_and_drops_stale_results():
    root = Path(tempfile.mkdtemp())
    csv = root / "suite.csv"
    items = [{"id": "PQ-01", "query": "first"}, {"id": "PQ-02", "query": "second"}]
    ts.sync_curated_csv(csv, items, root / "outputs")
    df = ts.pd.read_csv(csv)
    df["status_m_full"] = [1, 1]
    df.to_csv(csv, index=False)
    _write(root / "outputs" / "m" / "full", "query_PQ-02.json", ["1"])
    _write(root / "outputs" / "m" / "full", "query_PQ-01.json", ["1"])
    items[1]["query"] = "second, reworded"
    ts.sync_curated_csv(csv, items, root / "outputs")
    df = ts.pd.read_csv(csv)
    assert df["status_m_full"].tolist() == [1, 0]                     # changed row re-runs
    assert not (root / "outputs" / "m" / "full" / "query_PQ-02.json").exists()
    assert (root / "outputs" / "m" / "full" / "query_PQ-01.json").exists()


def test_curated_activation_ignores_optional_agents():
    folder = Path(tempfile.mkdtemp())
    pq06 = next(q for q in ts.load_curated_queries() if q["id"] == "PQ-06")
    assert pq06["optional_agents"] == ["proximity"]
    found = pq06["expected_agents"] + ["proximity"]          # optional agent also fired
    (folder / "query_PQ-06.json").write_text(json.dumps(
        {"query_id": "PQ-06", "ranking_logic": {"discovered_agents": found}}))
    out = ts.analyze_curated_activation(folder, "m")
    assert out["summary"]["perfect_rate"] == 1.0 and out["per_query"]["PQ-06"]["ok"]
    (folder / "query_PQ-06.json").write_text(json.dumps(
        {"query_id": "PQ-06", "ranking_logic": {"discovered_agents": ["location"]}}))
    out = ts.analyze_curated_activation(folder, "m")
    assert out["summary"]["perfect_rate"] == 0.0
    assert out["agent_metrics"]["regulatory"]["recall"] == 0.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
