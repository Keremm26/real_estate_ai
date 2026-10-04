"""
Offline tests for tests/compare_arms.py (no LLM or API calls): metrics on
synthetic suite results, the judge's order-swap combination, the blind list
format, and judge-vs-human agreement.

Run:
    python tests/test_compare_arms.py
"""

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_TMP = Path(tempfile.mkdtemp(prefix="compare_arms_"))
os.environ["EXPERIMENT_RESULTS_DIR"] = str(_TMP)

import tests.compare_arms as ca  # noqa: E402

MODEL = "m"
REG_OK = json.dumps({"found": True, "requirements": [{
    "target_column": "surface_area", "operator": ">=", "value": 750, "regulation": "D.M. MUR 1256/2021 Allegato A"}]})


def _pack(qid, surfaces, found=("location", "building"), reg="{}", start_id=0):
    top = [{"id": str(start_id + i), "surface_area": s, "regulatory_score": 99.0} for i, s in enumerate(surfaces)]
    return {"query_id": qid, "top10": top, "ranking": [{"id": t["id"], "score": 1.0} for t in top],
            "results_count": len(top), "execution_time_ms": 1000,
            "ranking_logic": {"discovered_agents": list(found)}, "regulatory": {"response": reg},
            "retrieval": {"hits": [{"doc_name": "D.M. MUR 1256/2021 — Allegato A", "article_ref": "6.1.1"}]}}


def _write_arm(arm, packs_by_qid):
    root = ca.arm_root(arm, MODEL)
    for qid, packs in packs_by_qid.items():
        for tr, p in enumerate(packs, 1):
            (root / "consistency").mkdir(parents=True, exist_ok=True)
            (root / "consistency" / f"query_{qid}_tr{tr}.json").write_text(json.dumps(p))
        (root / "full").mkdir(parents=True, exist_ok=True)
        (root / "full" / f"query_{qid}.json").write_text(json.dumps(packs[0]))


def _setup_runs():
    # PQ-06: regulation applies (value_min 570). none: 1 of 4 compliant; rag: 3 of 4.
    # PQ-27: no rule applies (energy query); both arms return the same top list.
    _write_arm("none", {
        "PQ-06": [_pack("PQ-06", [600, 100, 200, 300])] * 2,
        "PQ-27": [_pack("PQ-27", [80, 90], found=("energy",))] * 2,
    })
    _write_arm("rag", {
        "PQ-06": [_pack("PQ-06", [600, 700, 800, 300], found=("location", "building", "regulatory"), reg=REG_OK,
                        start_id=10)] * 2,
        "PQ-27": [_pack("PQ-27", [80, 90], found=("energy",))] * 2,
    })


class _Args:
    model, arms = MODEL, ["none", "rag"]


def test_metrics_compliance_and_stability():
    _setup_runs()
    assert ca.cmd_metrics(_Args()) == 0
    rep = json.loads((ca.out_dir(MODEL) / "metrics.json").read_text())
    c10 = rep["compliance"]["@10"]
    assert c10["n"] == 1 and c10["mean_a"] == 0.25 and c10["mean_b"] == 0.75 and c10["b_better"] == 1
    assert rep["stability"]["cross_arm_iou"] == 1.0 and rep["stability"]["n"] == 1
    reg = rep["regulatory"]
    assert reg["rag"]["activation_rate"] == 1.0 and reg["none"]["activation_rate"] == 0.0
    assert reg["rag"]["value_in_range_rate"] == 1.0
    assert reg["rag"]["correct_decline_rate"] == 1.0          # PQ-27: no requirement emitted
    assert rep["pipeline"]["rag"]["activation_perfect_rate"] == 1.0
    assert rep["pipeline"]["none"]["activation_perfect_rate"] == 0.5   # regulatory missing on PQ-06


def test_compliance_counts_missing_surface_as_not_compliant():
    assert ca.compliance({"top10": [{"surface_area": None}, {"surface_area": 900}]}, 570, 10) == 0.5
    assert ca.compliance({"top10": []}, 570, 10) is None


def test_representative_is_the_most_typical_trial():
    typical_a = _pack("Q", [600, 700, 800], start_id=0)
    typical_b = _pack("Q", [600, 700, 900], start_id=0)
    typical_b["top10"][2]["id"] = "x"                          # differs slightly from typical_a
    outlier = _pack("Q", [45], start_id=50)                    # broken run: one unrelated building
    assert ca.representative([outlier, typical_a, typical_b]) is typical_a
    assert ca.representative([typical_b, outlier, typical_a]) is typical_b   # ties -> earliest trial
    assert ca.representative([outlier]) is outlier


def test_padding_buildings_are_not_results():
    # 2 matches, then non-matching map padding with score 0
    pack = {"results_count": 2, "top10": [{"id": "a", "surface_area": 900}, {"id": "b", "surface_area": 800}]
            + [{"id": str(i), "surface_area": 50} for i in range(8)]}
    assert ca.top_ids(pack) == ["a", "b"]
    assert ca.compliance(pack, 570, 10) == 1.0
    assert ca.format_list(pack).count("\n") == 1                 # two lines shown to the judge
    assert ca.compliance({"results_count": 0, "top10": pack["top10"]}, 570, 10) is None


def test_blind_list_hides_scores():
    text = ca.format_list(_pack("PQ-06", [1250.0]))
    assert "1,250 m²" in text and "99" not in text and "score" not in text
    assert ca.format_list({"top10": []}) == "(no properties returned)"


def _state():
    reqs = {}
    for qid in ("PQ-06", "PQ-14", "PQ-15"):
        reqs[f"{qid}__ab"] = {"qid": qid, "A": "none", "B": "rag"}
        reqs[f"{qid}__ba"] = {"qid": qid, "A": "rag", "B": "none"}
    return {"arms": ["none", "rag"], "judge_model": ca.JUDGE_MODEL, "effort": "medium", "use_reference": True,
            "requests": reqs, "identical": ["PQ-27"]}


def _verdict(v):
    return {"verdict": {"request_fit": {"reason": "", "verdict": v}, "legal_adequacy": {"reason": "", "verdict": v},
                        "ranking_quality": {"reason": "", "verdict": v}, "rationale": "", "overall": v},
            "status": "succeeded", "usage": {"input": 1000, "output": 200}}


def test_judge_combines_swapped_orders():
    raw = {
        "PQ-06__ab": _verdict("B"), "PQ-06__ba": _verdict("A"),      # both say rag -> rag
        "PQ-14__ab": _verdict("A"), "PQ-14__ba": _verdict("A"),      # position bias -> tie, inconsistent
        "PQ-15__ab": _verdict("B"), "PQ-15__ba": {"status": "refused"},  # one order refused -> single verdict
    }
    items = ca.load_items()
    pairs = ca._combine(_state(), raw, items)
    assert pairs["PQ-06"]["verdicts"]["overall"] == "rag" and pairs["PQ-06"]["consistent"]["overall"] is True
    assert pairs["PQ-14"]["verdicts"]["overall"] == "tie" and pairs["PQ-14"]["consistent"]["overall"] is False
    assert pairs["PQ-15"]["verdicts"]["overall"] == "rag" and pairs["PQ-15"]["consistent"]["overall"] is None
    assert pairs["PQ-27"]["identical"] and pairs["PQ-27"]["verdicts"]["overall"] == "tie"
    summ = ca._judge_summary(_state(), pairs, raw)
    o = summ["criteria"]["overall"]
    assert (o["rag"], o["none"], o["tie"], o["missing"]) == (2, 0, 2, 0)
    assert o["position_consistency"] == 0.5
    assert summ["requests"] == {"succeeded": 5, "refused": 1}
    assert abs(summ["tokens"]["cost_usd"] - (5000 / 1e6 * 1.0 + 1000 / 1e6 * 5.0)) < 1e-12


def test_cohen_kappa():
    assert ca.cohen_kappa(["rag", "none", "tie"], ["rag", "none", "tie"]) == 1.0
    assert ca.cohen_kappa([], []) is None
    k = ca.cohen_kappa(["rag", "rag", "none", "none"], ["rag", "none", "none", "rag"])
    assert abs(k) < 1e-9                                     # chance-level agreement


def test_human_sample_and_agreement_round_trip():
    _setup_runs()

    class A(_Args):
        n, seed = 5, 1
    assert ca.cmd_human_sample(A()) == 0
    d = ca.out_dir(MODEL)
    rows = list(csv.DictReader(open(d / "human_sample.csv", encoding="utf-8")))
    key = {r["pair_id"]: r for r in csv.DictReader(open(d / "human_sample_key.csv", encoding="utf-8"))}
    assert [r["pair_id"] for r in rows] == ["H01"]           # PQ-27 lists are identical: not sampled
    assert key["H01"]["query_id"] == "PQ-06"
    # the human prefers rag on every criterion; the judge said the same
    rag_label = "A" if key["H01"]["A"] == "rag" else "B"
    for col in ca.HUMAN_COLS:
        rows[0][col] = rag_label
    filled = d / "human_filled.csv"
    with open(filled, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (d / "judge_results.json").write_text(json.dumps({"pairs": {"PQ-06": {"verdicts": {c: "rag" for c in ca.CRITERIA}}}}))

    class G(_Args):
        human = str(filled)
    assert ca.cmd_agreement(G()) == 0
    out = json.loads((d / "judge_human_agreement.json").read_text())
    assert out["overall"]["n"] == 1 and out["overall"]["agreement"] == 1.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
