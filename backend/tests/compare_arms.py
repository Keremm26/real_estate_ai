"""
RAG study — compare two regulatory-retrieval arms of the full MURENA pipeline
on the curated query set (tests/benchmark_queries_turin.json).

Run the suite once per arm first (results land in results/curated_<arm>/):
    python tests/test_suite.py --model gpt-5.4-mini --queries curated --arm none
    python tests/test_suite.py --model gpt-5.4-mini --queries curated --arm rag

Then:
    python tests/compare_arms.py metrics      [--model M] [--arms none,rag]
    python tests/compare_arms.py judge        [--model M] [--ids PQ-01,..] [--no-wait] [--no-reference]
    python tests/compare_arms.py human-sample [--model M] [--n 15]
    python tests/compare_arms.py agreement    [--model M] --human <filled human_sample.csv>

metrics (no LLM calls)
  regulatory   the pipeline's regulatory output per query and trial, scored with
               regulatory_scoring against the item's `regulatory` expectation
               (sources / precedence taken from the mirrored downstream item)
  compliance   share of the top-10 (and top-3) whose surface_area reaches the
               item's lowest legal minimum (value_min), on activate=true items;
               paired per query (mean over trials), Wilcoxon signed-rank test
  stability    top-10 IoU between the arms on activate=false items (retrieval
               should not move rankings when no rule applies), next to each
               arm's own trial-to-trial IoU (the noise floor)
  activation   agents that found a scorable requirement vs expected_agents
               (optional agents ignored); zero-result rate; latency
judge
  Blind pairwise LLM judge (Claude Sonnet 5.5 via the Message Batches API): the
  user request + each arm's most typical trial top-10 (see representative), labelled A / B, arms and scores
  hidden. Every pair is judged twice with the order swapped; a verdict counts
  only when both orders agree, otherwise it is a tie (position inconsistency).
  Criteria: request fit, legal adequacy, ranking quality, overall. Identical
  lists are recorded as ties without an API call. Refusals / errors are missing.
  By default the judge sees the evaluation key's regulatory reference (minimum
  surface range for the intended use) so legal adequacy is judged against the
  same basis as the compliance metric, not the judge's own legal priors.
human-sample / agreement
  Export a blind sample for a human judge (key in a separate file), then report
  per-criterion agreement and Cohen's kappa between the judge and the human.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations, product
from pathlib import Path
from typing import Any, Dict, List, Optional

suite_path = Path(__file__).resolve().parent
backend_dir = suite_path.parent
sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(backend_dir / ".env")   # ANTHROPIC_API_KEY for the judge

import numpy as np  # noqa: E402
from scipy import stats  # noqa: E402

from app.core.constants import REGULATORY_AGENT_COLUMNS  # noqa: E402
from app.services.llm.rag.eval import regulatory_scoring as rs  # noqa: E402

RESULTS = Path(os.environ.get("EXPERIMENT_RESULTS_DIR", str(suite_path / "results")))
QUERIES_PATH = suite_path / "benchmark_queries_turin.json"
DOWNSTREAM_PATH = backend_dir / "app" / "services" / "llm" / "rag" / "eval" / "downstream_queries_turin.json"

JUDGE_MODEL = "claude-sonnet-5-5"
JUDGE_EFFORT = "medium"
JUDGE_MAX_TOKENS = 8000
BATCH_PRICE_PER_MTOK = {"input": 1.0, "output": 5.0}   # Sonnet 5.5 at the 50% batch discount
CRITERIA = ("request_fit", "legal_adequacy", "ranking_quality", "overall")

_TRIAL_RE = re.compile(r"query_(?P<qid>[A-Za-z0-9-]+?)_tr(?P<tr>\d+)\.json")


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load_items() -> Dict[str, Dict[str, Any]]:
    """Curated pipeline items, with the mirrored downstream item's sources /
    precedence attached (the pipeline file does not repeat them)."""
    items = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    mirrors = {q["id"]: q for q in json.loads(DOWNSTREAM_PATH.read_text(encoding="utf-8"))["queries"]}
    for q in items:
        m = mirrors.get(q.get("mirrors") or "") or {}
        q["_sources"], q["_precedence"] = m.get("sources"), m.get("precedence")
    return {q["id"]: q for q in items}


def arm_root(arm: str, model: str) -> Path:
    return RESULTS / f"curated_{arm}" / "outputs" / "benchmarks" / model


def load_runs(arm: str, model: str) -> Dict[str, Dict[str, Any]]:
    """{'full': {qid: suite consensus pack}, 'trials': {qid: [trial packs]},
        'rep': {qid: representative pack (see representative)}}"""
    root = arm_root(arm, model)
    full: Dict[str, Any] = {}
    for f in sorted((root / "full").glob("query_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        full[str(d.get("query_id") or f.stem[len("query_"):])] = d
    trials: Dict[str, List[Any]] = defaultdict(list)
    for f in sorted((root / "consistency").glob("query_*_tr*.json")):
        m = _TRIAL_RE.fullmatch(f.name)
        if m:
            trials[m["qid"]].append(json.loads(f.read_text(encoding="utf-8")))
    rep = {**full, **{qid: representative(packs) for qid, packs in trials.items()}}
    return {"full": full, "trials": dict(trials), "rep": rep}


def representative(packs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The most typical trial of a query: the one whose top-10 overlaps most with
    the other trials (mean IoU; ties -> earliest trial). The suite's own
    consensus (`full/`) keeps the most frequent EXACT top-10, which is an arbitrary
    pick whenever all trials differ slightly — often an outlier trial."""
    if len(packs) < 3:
        return packs[0]
    typicality = [np.mean([iou(top_ids(p), top_ids(o)) for o in packs if o is not p]) for p in packs]
    return packs[int(np.argmax(typicality))]


def out_dir(model: str) -> Path:
    d = RESULTS / "curated_compare" / model
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# per-pack measures
# --------------------------------------------------------------------------
def matches(pack: Dict[str, Any], key: str = "top10") -> List[Dict[str, Any]]:
    """The ranked buildings that MATCH the query. The pipeline's building list
    puts the matches first and then pads it with non-matching buildings (score 0,
    for the map), so only the first ``results_count`` entries are results."""
    rows = (pack.get(key) or [])[:10]
    n = pack.get("results_count")
    return rows if n is None else rows[:max(0, n)]


def top_ids(pack: Dict[str, Any]) -> List[str]:
    return [str(b["id"]) for b in (matches(pack) or matches(pack, "ranking"))]


def iou(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def compliance(pack: Dict[str, Any], value_min: float, k: int) -> Optional[float]:
    """Share of the top-k matching buildings whose surface reaches value_min. A
    missing surface counts as not compliant; no results means no compliance (None)."""
    top = matches(pack)[:k]
    if not top:
        return None
    return sum((rs.to_float(b.get("surface_area")) or 0.0) >= value_min for b in top) / len(top)


def score_regulatory(item: Dict[str, Any], pack: Dict[str, Any], key2name: Dict[str, str]) -> Dict[str, Any]:
    exp = item["regulatory"]
    expect = {k: exp.get(k) for k in ("activate", "target_column", "operator", "value_min", "value_max")}
    retrieval = pack.get("retrieval") or {}
    rec = rs.score_output(
        expect, (pack.get("regulatory") or {}).get("response") or "", retrieval.get("hits") or [],
        REGULATORY_AGENT_COLUMNS, user_values=exp.get("user_values") or [], sources=item.get("_sources"),
        precedence=item.get("_precedence"), key2name=key2name)
    rec.pop("requirements", None)
    return rec


def activation_ok(item: Dict[str, Any], pack: Dict[str, Any]) -> bool:
    expected, optional = set(item["expected_agents"]), set(item.get("optional_agents", []))
    found = set((pack.get("ranking_logic") or {}).get("discovered_agents") or [])
    return expected <= found and (found - optional) <= expected


def _paired(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, Any]:
    """Paired comparison of per-query values (b - a)."""
    qids = sorted(set(a) & set(b))
    if not qids:
        return {"n": 0}
    diffs = np.array([b[q] - a[q] for q in qids])
    out = {"n": len(qids), "mean_a": float(np.mean([a[q] for q in qids])),
           "mean_b": float(np.mean([b[q] for q in qids])), "mean_diff": float(diffs.mean()),
           "b_better": int((diffs > 1e-9).sum()), "ties": int((np.abs(diffs) <= 1e-9).sum()),
           "a_better": int((diffs < -1e-9).sum()), "wilcoxon_p": None}
    if out["b_better"] + out["a_better"] > 0:
        try:
            out["wilcoxon_p"] = float(stats.wilcoxon(diffs[np.abs(diffs) > 1e-9]).pvalue)
        except ValueError:
            pass
    return out


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def cmd_metrics(args) -> int:
    from app.services.llm.rag.eval.run import _doc_key_to_name

    items, key2name = load_items(), _doc_key_to_name()
    a, b = args.arms
    runs = {arm: load_runs(arm, args.model) for arm in (a, b)}
    for arm in (a, b):
        if not runs[arm]["trials"]:
            print(f"No trial results for arm '{arm}' under {arm_root(arm, args.model)}")
            return 1

    report: Dict[str, Any] = {"meta": {"model": args.model, "arms": [a, b], "generated": datetime.now().isoformat(timespec="seconds"),
                                       "n_queries": {arm: len(runs[arm]["trials"]) for arm in (a, b)}}}
    per_query: Dict[str, Dict[str, Any]] = defaultdict(dict)

    # regulatory output, activation, zero results, latency — over all trials
    report["regulatory"], report["pipeline"] = {}, {}
    for arm in (a, b):
        recs, act, zero, lat = [], [], [], []
        for qid, packs in runs[arm]["trials"].items():
            item = items.get(qid)
            if not item:
                continue
            for p in packs:
                r = score_regulatory(item, p, key2name)
                r["id"] = qid
                recs.append(r)
                act.append(activation_ok(item, p))
                zero.append((p.get("results_count") or 0) == 0)
                lat.append(p.get("execution_time_ms"))
            per_query[qid][arm] = {"regulatory_ok": rs.rate(x["activation_ok"] for x in recs if x["id"] == qid),
                                   "values": [x["main_value"] for x in recs if x["id"] == qid]}
        report["regulatory"][arm] = rs.aggregate([{**r, "requirements": []} for r in recs])
        report["pipeline"][arm] = {"n_runs": len(recs), "activation_perfect_rate": rs.rate(act),
                                   "zero_result_rate": rs.rate(zero), "mean_latency_s": (rs.mean(lat) or 0) / 1000}
        report.setdefault("regulatory_runs", {})[arm] = recs

    # compliance on activate=true items (mean over trials, paired per query)
    report["compliance"] = {}
    for k in (10, 3):
        vals = {arm: {} for arm in (a, b)}
        for arm in (a, b):
            for qid, packs in runs[arm]["trials"].items():
                item = items.get(qid)
                if not item or item["regulatory"].get("activate") is not True or item["regulatory"].get("value_min") is None:
                    continue
                c = [compliance(p, item["regulatory"]["value_min"], k) for p in packs]
                c = [x for x in c if x is not None]
                if c:
                    vals[arm][qid] = float(np.mean(c))
                    per_query[qid][arm][f"compliance@{k}"] = vals[arm][qid]
        report["compliance"][f"@{k}"] = _paired(vals[a], vals[b])

    # stability on activate=false items: cross-arm IoU vs within-arm noise floor
    cross, self_iou = {}, {arm: {} for arm in (a, b)}
    for qid, item in items.items():
        if item["regulatory"].get("activate") is not False:
            continue
        ta, tb = runs[a]["trials"].get(qid) or [], runs[b]["trials"].get(qid) or []
        if ta and tb:
            cross[qid] = float(np.mean([iou(top_ids(x), top_ids(y)) for x, y in product(ta, tb)]))
            per_query[qid]["cross_arm_iou"] = cross[qid]
        for arm, tt in ((a, ta), (b, tb)):
            if len(tt) > 1:
                self_iou[arm][qid] = float(np.mean([iou(top_ids(x), top_ids(y)) for x, y in combinations(tt, 2)]))
    report["stability"] = {"n": len(cross), "cross_arm_iou": rs.mean(cross.values()),
                           **{f"self_iou_{arm}": rs.mean(self_iou[arm].values()) for arm in (a, b)}}
    report["per_query"] = per_query

    d = out_dir(args.model)
    (d / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md = _metrics_md(report, a, b)
    (d / "metrics.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {d / 'metrics.json'}\nWrote {d / 'metrics.md'}")
    return 0


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _metrics_md(rep: Dict[str, Any], a: str, b: str) -> str:
    reg = rep["regulatory"]
    rows = [("right regulatory outcome", "activation_ok_rate"), ("activation (expected-true)", "activation_rate"),
            ("correct decline (expected-false)", "correct_decline_rate"), ("value inside expected range", "value_in_range_rate"),
            ("echo of a user-stated number ↓", "echo_rate"), ("source article retrieved", "source_in_context_rate"),
            ("precedence winner cited", "winner_cited_rate"), ("citation names a retrieved doc", "main_doc_grounding_rate")]
    lines = [f"# RAG study — full pipeline, {a} vs {b} ({rep['meta']['model']})", "",
             f"Queries with results: {rep['meta']['n_queries']} · generated {rep['meta']['generated']}", "",
             "## Regulatory output inside the pipeline (all trials)", "", f"| metric | {a} | {b} |", "|---|---|---|"]
    lines += [f"| {label} | {_pct(reg[a].get(k))} | {_pct(reg[b].get(k))} |" for label, k in rows]
    pa, pb = rep["pipeline"][a], rep["pipeline"][b]
    lines += [f"| agent activation as expected | {_pct(pa['activation_perfect_rate'])} | {_pct(pb['activation_perfect_rate'])} |",
              f"| runs with zero results | {_pct(pa['zero_result_rate'])} | {_pct(pb['zero_result_rate'])} |",
              f"| mean latency (s) | {pa['mean_latency_s']:.1f} | {pb['mean_latency_s']:.1f} |", "",
              "## Top-k legal compliance (activate=true items, mean over trials, paired per query)", "",
              f"| k | n | {a} | {b} | mean diff | {b} better / tie / {a} better | Wilcoxon p |", "|---|---|---|---|---|---|---|"]
    for k, c in rep["compliance"].items():
        if not c.get("n"):
            lines.append(f"| {k} | 0 | — | — | — | — | — |")
            continue
        p = "—" if c["wilcoxon_p"] is None else f"{c['wilcoxon_p']:.3f}"
        lines.append(f"| {k} | {c['n']} | {_pct(c['mean_a'])} | {_pct(c['mean_b'])} | {c['mean_diff'] * 100:+.1f} pp | "
                     f"{c['b_better']} / {c['ties']} / {c['a_better']} | {p} |")
    s = rep["stability"]
    f = lambda x: "—" if x is None else f"{x:.3f}"  # noqa: E731
    lines += ["", "## Ranking stability where no rule applies (activate=false items)", "",
              f"Top-10 IoU {a} vs {b}: **{f(s['cross_arm_iou'])}** (n={s['n']}) · noise floor (same arm, trial vs trial): "
              f"{a} {f(s.get(f'self_iou_{a}'))}, {b} {f(s.get(f'self_iou_{b}'))}", "",
              "A cross-arm IoU close to the noise floor means retrieval does not perturb rankings when no rule applies.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# blind pairwise judge
# --------------------------------------------------------------------------
JUDGE_SYSTEM = """You evaluate property-search results for a public real-estate portfolio in Turin, Italy.
A user described what they need. Two systems, A and B, each returned their top-10 properties from the same
dataset, best first. Decide which list serves the user better, criterion by criterion.

Criteria
1. request_fit: do the properties match the explicit request (property type, place / distance from the
   landmark, surface, energy class, nearby services, intended use)?
2. legal_adequacy: for the intended use, are the properties legally suitable, mainly large enough for the
   stated capacity? Use the REFERENCE note when one is given; it states what the applicable rules imply.
   When the request implies no regulatory constraint, answer tie unless a list contains clearly unsuitable
   properties.
3. ranking_quality: are the most suitable properties placed at the top?
4. overall: which list would you hand to this user?

Rules
- Judge only from the data shown. Missing fields are unknown, not bad.
- Lists may overlap. A shorter or empty list is worse only if the missing properties would have been relevant.
- The order in which the lists are shown (A first, B second) carries no information.
- Answer tie when the difference is negligible.
- Give a short reason for each criterion before its verdict."""

_VERDICT = {"type": "string", "enum": ["A", "B", "tie"]}
_CRIT = {"type": "object", "properties": {"reason": {"type": "string"}, "verdict": _VERDICT},
         "required": ["reason", "verdict"], "additionalProperties": False}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"request_fit": _CRIT, "legal_adequacy": _CRIT, "ranking_quality": _CRIT,
                   "rationale": {"type": "string"}, "overall": _VERDICT},
    "required": ["request_fit", "legal_adequacy", "ranking_quality", "rationale", "overall"],
    "additionalProperties": False,
}

# fields shown to the judge — no scores (the regulatory score would reveal the arm)
_FIELDS = [("type", "property_type", ""), ("surface", "surface_area", " m²"), ("energy class", "energy_class", ""),
           ("zone", "omi_zone", ""), ("address", "address", ""), ("distance", "distance_km", " km"),
           ("from", "proximity_reference", ""), ("built", "construction_year", ""), ("rooms", "rooms", ""),
           ("legal nature", "legal_nature", ""), ("heritage constraint", "cultural_constraint", ""),
           ("purpose", "purpose", ""), ("greenery index", "greenery", ""), ("mobility index", "mobility", ""),
           ("education index", "education", "")]


def format_list(pack: Dict[str, Any]) -> str:
    rows = matches(pack)
    if not rows:
        return "(no properties returned)"
    lines = []
    for i, bld in enumerate(rows, 1):
        parts = [f"id {bld.get('id')}"]
        for label, key, unit in _FIELDS:
            v = bld.get(key)
            if v is None or v == "" or (isinstance(v, float) and v != v):
                continue
            if key == "surface_area":
                v = f"{float(v):,.0f}"
            elif isinstance(v, float):
                v = f"{v:.2f}"
            parts.append(f"{label} {v}{unit}")
        lines.append(f"{i}. " + " | ".join(parts))
    return "\n".join(lines)


def reference_note(item: Dict[str, Any]) -> str:
    r = item["regulatory"]
    rng = (f"{r['value_min']:,.0f}–{r['value_max']:,.0f} m²"
           if r.get("value_min") is not None and r.get("value_max") is not None else None)
    if r.get("activate") is True and rng:
        return (f"REFERENCE: for this request the applicable rules require a minimum total surface between {rng}, "
                f"depending on the room mix ({r.get('basis')}). Properties below the lower value are not legally "
                f"adequate for the intended use.")
    if r.get("activate") == "either" and rng:
        return (f"REFERENCE: a regulatory minimum may apply to this request; if it does, it lies between {rng} "
                f"({r.get('basis')}).")
    return "REFERENCE: no specific regulatory surface minimum applies to this request."


def judge_prompt(item: Dict[str, Any], pack_a: Dict[str, Any], pack_b: Dict[str, Any], use_reference: bool) -> str:
    ref = f"{reference_note(item)}\n\n" if use_reference else ""
    return (f"USER REQUEST\n{item['query']}\n\n{ref}LIST A\n{format_list(pack_a)}\n\n"
            f"LIST B\n{format_list(pack_b)}\n\nCompare the two lists.")


def _judge_state_path(model: str) -> Path:
    return out_dir(model) / "judge_state.json"


def _submit(args, items, runs, a: str, b: str) -> Dict[str, Any]:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    qids = sorted(set(runs[a]["rep"]) & set(runs[b]["rep"]) & set(items))
    if args.ids:
        qids = [q for q in qids if q in set(args.ids)]
    requests, mapping, identical = [], {}, []
    for qid in qids:
        pa, pb = runs[a]["rep"][qid], runs[b]["rep"][qid]
        if top_ids(pa) == top_ids(pb):
            identical.append(qid)          # same list: a tie by construction, no API call
            continue
        for order, (x, y) in (("ab", (a, b)), ("ba", (b, a))):
            cid = f"{qid}__{order}"
            mapping[cid] = {"qid": qid, "A": x, "B": y}
            requests.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
                model=JUDGE_MODEL, max_tokens=JUDGE_MAX_TOKENS, system=JUDGE_SYSTEM,
                messages=[{"role": "user", "content": judge_prompt(items[qid], runs[x]["rep"][qid],
                                                                   runs[y]["rep"][qid], not args.no_reference)}],
                output_config={"effort": JUDGE_EFFORT, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            )))
    state = {"arms": [a, b], "judge_model": JUDGE_MODEL, "effort": JUDGE_EFFORT, "use_reference": not args.no_reference,
             "representative": "most typical trial (max mean top-10 IoU to the other trials)",
             "created": datetime.now().isoformat(timespec="seconds"), "requests": mapping, "identical": identical,
             "batch_id": None}
    if requests:
        batch = anthropic.Anthropic().messages.batches.create(requests=requests)
        state["batch_id"] = batch.id
        print(f"Submitted batch {batch.id}: {len(requests)} requests ({len(requests) // 2} pairs × 2 orders); "
              f"{len(identical)} identical pairs recorded as ties.")
    else:
        print(f"Nothing to submit ({len(identical)} identical pairs).")
    _judge_state_path(args.model).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _collect(state: Dict[str, Any], model: str, wait: bool) -> Optional[Dict[str, Any]]:
    import anthropic

    raw_path = out_dir(model) / "judge_raw.json"
    if not state.get("batch_id"):
        return {}
    client = anthropic.Anthropic()
    while True:
        batch = client.messages.batches.retrieve(state["batch_id"])
        if batch.processing_status == "ended":
            break
        if not wait:
            print(f"Batch {batch.id}: {batch.processing_status} "
                  f"(processing={batch.request_counts.processing}). Re-run `judge` later to collect.")
            return None
        print(f"  batch {batch.processing_status}: processing={batch.request_counts.processing} "
              f"succeeded={batch.request_counts.succeeded}", flush=True)
        time.sleep(30)

    raw: Dict[str, Any] = {}
    for res in client.messages.batches.results(state["batch_id"]):
        entry: Dict[str, Any] = {"status": res.result.type}
        if res.result.type == "succeeded":
            msg = res.result.message
            entry["stop_reason"] = msg.stop_reason
            entry["usage"] = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
            if msg.stop_reason == "refusal":
                entry["status"] = "refused"
            else:
                text = next((blk.text for blk in msg.content if blk.type == "text"), "")
                try:
                    entry["verdict"] = json.loads(text)
                except json.JSONDecodeError:
                    entry["status"], entry["text"] = "unparsable", text[:2000]
        elif res.result.type == "errored":
            entry["error"] = res.result.error.type
        raw[res.custom_id] = entry
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return raw


def _combine(state: Dict[str, Any], raw: Dict[str, Any], items: Dict[str, Any]) -> Dict[str, Any]:
    """Arm-level verdicts per pair: both orders must agree, else tie."""
    by_q: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)
    for cid, m in state["requests"].items():
        v = (raw.get(cid) or {}).get("verdict")
        if not v:
            continue
        labels = {c: (v.get(c) or {}).get("verdict") if c != "overall" else v.get("overall") for c in CRITERIA}
        by_q[m["qid"]][cid.rsplit("__", 1)[1]] = {c: (m[l] if l in ("A", "B") else "tie") for c, l in labels.items() if l}
    pairs: Dict[str, Any] = {}
    for qid in state.get("identical", []):
        pairs[qid] = {"verdicts": {c: "tie" for c in CRITERIA}, "identical": True, "consistent": {c: True for c in CRITERIA}}
    for qid in {m["qid"] for m in state["requests"].values()}:
        orders = by_q.get(qid, {})
        verdicts, consistent = {}, {}
        for c in CRITERIA:
            vs = [orders[o][c] for o in ("ab", "ba") if o in orders and c in orders[o]]
            if not vs:
                verdicts[c], consistent[c] = None, None
            elif len(vs) == 1:
                verdicts[c], consistent[c] = vs[0], None          # one order missing: single judgment
            else:
                verdicts[c], consistent[c] = (vs[0], True) if vs[0] == vs[1] else ("tie", False)
        pairs[qid] = {"verdicts": verdicts, "identical": False, "consistent": consistent,
                      "activate": str(items[qid]["regulatory"].get("activate"))}
    for qid, p in pairs.items():
        p.setdefault("activate", str(items[qid]["regulatory"].get("activate")))
    return pairs


def _judge_summary(state: Dict[str, Any], pairs: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    a, b = state["arms"]
    summ: Dict[str, Any] = {"n_pairs": len(pairs), "identical_pairs": len(state.get("identical", [])), "criteria": {}}
    for c in CRITERIA:
        vals = [p["verdicts"].get(c) for p in pairs.values()]
        cnt = Counter(v if v else "missing" for v in vals)
        decided = cnt[a] + cnt[b]
        p_sign = float(stats.binomtest(cnt[b], decided, 0.5).pvalue) if decided else None
        cons = [p["consistent"].get(c) for p in pairs.values() if not p["identical"] and p["consistent"].get(c) is not None]
        by_act = {}
        for act in ("True", "False", "either"):
            sub = Counter(p["verdicts"].get(c) or "missing" for p in pairs.values() if p["activate"] == act)
            by_act[act] = {a: sub[a], b: sub[b], "tie": sub["tie"], "missing": sub["missing"]}
        summ["criteria"][c] = {a: cnt[a], b: cnt[b], "tie": cnt["tie"], "missing": cnt["missing"],
                               f"{b}_win_rate_decided": (cnt[b] / decided) if decided else None,
                               "sign_test_p": p_sign, "position_consistency": rs.rate(cons), "by_activate": by_act}
    statuses = Counter(e["status"] for e in raw.values())
    tok_in = sum((e.get("usage") or {}).get("input", 0) for e in raw.values())
    tok_out = sum((e.get("usage") or {}).get("output", 0) for e in raw.values())
    summ["requests"] = dict(statuses)
    summ["tokens"] = {"input": tok_in, "output": tok_out,
                      "cost_usd": tok_in / 1e6 * BATCH_PRICE_PER_MTOK["input"] + tok_out / 1e6 * BATCH_PRICE_PER_MTOK["output"]}
    return summ


def _judge_md(state: Dict[str, Any], summ: Dict[str, Any], model: str) -> str:
    a, b = state["arms"]
    lines = [f"# Blind pairwise judge — {a} vs {b} ({model})", "",
             f"Judge {state['judge_model']} (effort {state['effort']}, Batch API) · reference note "
             f"{'shown' if state['use_reference'] else 'hidden'} · {summ['n_pairs']} pairs "
             f"({summ['identical_pairs']} identical → tie) · requests {summ['requests']} · "
             f"cost ≈ ${summ['tokens']['cost_usd']:.2f}", "",
             f"| criterion | {b} wins | {a} wins | tie | missing | {b} win rate (decided) | sign test p | position consistency |",
             "|---|---|---|---|---|---|---|---|"]
    for c, s in summ["criteria"].items():
        wr = s[f"{b}_win_rate_decided"]
        p_val = "—" if s["sign_test_p"] is None else f"{s['sign_test_p']:.3f}"
        lines.append(f"| {c} | {s[b]} | {s[a]} | {s['tie']} | {s['missing']} | {_pct(wr)} | "
                     f"{p_val} | {_pct(s['position_consistency'])} |")
    lines += ["", "## Overall verdict by regulatory expectation", "",
              f"| activate | {b} wins | {a} wins | tie | missing |", "|---|---|---|---|---|"]
    for act, s in summ["criteria"]["overall"]["by_activate"].items():
        lines.append(f"| {act} | {s[b]} | {s[a]} | {s['tie']} | {s['missing']} |")
    lines += ["", "A verdict counts only when both presentation orders agree; disagreement is scored as a tie.", ""]
    return "\n".join(lines)


def cmd_judge(args) -> int:
    items = load_items()
    a, b = args.arms
    state_path = _judge_state_path(args.model)
    state = json.loads(state_path.read_text()) if state_path.exists() and not args.resubmit else None
    if state is None:
        runs = {arm: load_runs(arm, args.model) for arm in (a, b)}
        if not runs[a]["rep"] or not runs[b]["rep"]:
            print("Results missing for one of the arms; run the suite first.")
            return 1
        state = _submit(args, items, runs, a, b)
        if args.no_wait:
            return 0
    else:
        print(f"Resuming judge batch {state.get('batch_id')} (use --resubmit to start over).")
    raw = _collect(state, args.model, wait=not args.no_wait)
    if raw is None:
        return 0
    pairs = _combine(state, raw, items)
    summ = _judge_summary(state, pairs, raw)
    d = out_dir(args.model)
    (d / "judge_results.json").write_text(json.dumps({"state": {k: v for k, v in state.items() if k != "requests"},
                                                      "summary": summ, "pairs": pairs}, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    md = _judge_md(state, summ, args.model)
    (d / "judge_summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Wrote {d / 'judge_results.json'}\nWrote {d / 'judge_summary.md'}")
    return 0


# --------------------------------------------------------------------------
# human validation of the judge
# --------------------------------------------------------------------------
HUMAN_COLS = ["human_request_fit", "human_legal_adequacy", "human_ranking_quality", "human_overall"]


def cmd_human_sample(args) -> int:
    items = load_items()
    a, b = args.arms
    runs = {arm: load_runs(arm, args.model) for arm in (a, b)}
    qids = [q for q in sorted(set(runs[a]["rep"]) & set(runs[b]["rep"]) & set(items))
            if top_ids(runs[a]["rep"][q]) != top_ids(runs[b]["rep"][q])]
    rng = random.Random(args.seed)
    reg = [q for q in qids if items[q]["regulatory"].get("activate") is True]
    other = [q for q in qids if q not in reg]
    n_reg = min(len(reg), (args.n + 1) // 2)
    sample = rng.sample(reg, n_reg) + rng.sample(other, min(len(other), args.n - n_reg))
    rng.shuffle(sample)
    d = out_dir(args.model)
    with open(d / "human_sample.csv", "w", newline="", encoding="utf-8") as fs, \
         open(d / "human_sample_key.csv", "w", newline="", encoding="utf-8") as fk:
        ws, wk = csv.writer(fs), csv.writer(fk)
        ws.writerow(["pair_id", "query", "reference", "list_A", "list_B", *HUMAN_COLS, "notes"])
        wk.writerow(["pair_id", "query_id", "A", "B"])
        for i, qid in enumerate(sample, 1):
            x, y = (a, b) if rng.random() < 0.5 else (b, a)
            pid = f"H{i:02d}"
            ws.writerow([pid, items[qid]["query"], reference_note(items[qid]),
                         format_list(runs[x]["rep"][qid]), format_list(runs[y]["rep"][qid]), "", "", "", "", ""])
            wk.writerow([pid, qid, x, y])
    print(f"Wrote {d / 'human_sample.csv'} ({len(sample)} pairs) — fill the human_* columns with A, B or tie.")
    print(f"Wrote {d / 'human_sample_key.csv'} — keep it away from the human judge.")
    return 0


def cohen_kappa(x: List[str], y: List[str]) -> Optional[float]:
    n = len(x)
    if not n:
        return None
    po = sum(p == q for p, q in zip(x, y)) / n
    pe = sum((x.count(lab) / n) * (y.count(lab) / n) for lab in set(x) | set(y))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def cmd_agreement(args) -> int:
    d = out_dir(args.model)
    judged = json.loads((d / "judge_results.json").read_text(encoding="utf-8"))["pairs"]
    key = {r["pair_id"]: r for r in csv.DictReader(open(d / "human_sample_key.csv", encoding="utf-8"))}
    human = list(csv.DictReader(open(args.human, encoding="utf-8")))
    out = {}
    for col in HUMAN_COLS:
        crit = col[len("human_"):]
        hs, js = [], []
        for row in human:
            lab = (row.get(col) or "").strip()
            k = key.get(row["pair_id"])
            jv = ((judged.get(k["query_id"]) or {}).get("verdicts") or {}).get(crit) if k else None
            if not k or lab not in ("A", "B", "tie") or not jv:
                continue
            hs.append(k[lab] if lab in ("A", "B") else "tie")
            js.append(jv)
        out[crit] = {"n": len(hs), "agreement": (sum(h == j for h, j in zip(hs, js)) / len(hs)) if hs else None,
                     "cohen_kappa": cohen_kappa(hs, js)}
    (d / "judge_human_agreement.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{'criterion':18}{'n':>4}{'agreement':>11}{'kappa':>8}")
    for c, r in out.items():
        ag = "—" if r["agreement"] is None else f"{r['agreement'] * 100:.0f}%"
        ka = "—" if r["cohen_kappa"] is None else f"{r['cohen_kappa']:.2f}"
        print(f"{c:18}{r['n']:>4}{ag:>11}{ka:>8}")
    print(f"Wrote {d / 'judge_human_agreement.json'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Compare two regulatory-retrieval arms of the full pipeline.")
    p.add_argument("command", choices=["metrics", "judge", "human-sample", "agreement"])
    p.add_argument("--model", default="gpt-5.4-mini", help="agent model whose suite results to compare")
    p.add_argument("--arms", default="none,rag", help="baseline arm, treatment arm (default none,rag)")
    p.add_argument("--ids", default=None, help="judge: comma-separated query ids (default: all)")
    p.add_argument("--no-wait", action="store_true", help="judge: submit / check the batch and exit")
    p.add_argument("--resubmit", action="store_true", help="judge: ignore a saved batch and submit a new one")
    p.add_argument("--no-reference", action="store_true", help="judge: hide the regulatory reference note")
    p.add_argument("--n", type=int, default=15, help="human-sample: number of pairs")
    p.add_argument("--seed", type=int, default=7, help="human-sample: sampling / order seed")
    p.add_argument("--human", default=None, help="agreement: the filled human_sample.csv")
    args = p.parse_args(argv)
    args.arms = [x.strip() for x in args.arms.split(",")]
    if len(args.arms) != 2:
        p.error("--arms takes exactly two arms")
    args.ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    if args.command == "agreement" and not args.human:
        p.error("agreement needs --human")
    return {"metrics": cmd_metrics, "judge": cmd_judge, "human-sample": cmd_human_sample,
            "agreement": cmd_agreement}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
