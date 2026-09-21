"""
Downstream evaluation — does retrieval change what the regulatory agent PRODUCES?

    python -m app.services.llm.rag.eval.downstream [--arms none,dump,rag] [--repeats 1]
                                                   [--json out.json] [--queries path]
                                                   [--dry-run] [--ablate]

``run.py`` scores the *ranking* against a gold set of legal questions. This
runner closes the loop: it executes the actual ``RegulatoryAgent`` (filtering
mode — the LLM extraction step) on production-shaped property-search queries
under each retrieval arm, and scores the extracted requirements. ``_run_ranking``
is deterministic given those requirements, so this is where the pipeline-level
difference is decided.

Arms (``config.RETRIEVAL_MODES``): none = legacy loader (production before this
work — no documents), dump = every in-scope chunk, rag = cascade + semantic top-k.

Per-arm metrics
  activation          expect.activate=true queries where the agent returned
                      found=true with >= 1 usable requirement
  false activation    same on negative controls (expect.activate=false) — lower is better
  value in range      expected-range queries whose main surface value lies inside
                      the standard-derived range (missing = wrong)
  target / operator   main requirement has the expected target_column / operator
  doc grounding       share of produced requirements whose 'regulation' names a
                      document that was really in the prompt (law number / year).
                      Structurally 0 for the none arm: it had no documents.
  value grounding     share of requirements whose quoted per-unit figures
                      (decimals or numbers >= 10 in 'description') occur in the
                      context text
  efficiency          context chars / ~tokens and latency
  consistency         with --repeats N: queries whose main value is identical
                      across all repeats

``--dry-run`` performs retrieval only (no LLM) and prints the hits per arm.
``--ablate`` compares the four extraction-oriented retrieval configurations
(raw / framing / quantitative-only / both) by the rank at which the first
dimensioning chunk (meta.dimensioning_refs) appears — embedding cost only.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.llm.rag import config
from app.services.llm.rag.eval.run import _doc_key_to_name
from app.services.llm.rag.retriever import search

QUERIES_PATH = Path(__file__).resolve().parent / "downstream_queries.json"

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_LAW_NUM_RE = re.compile(r"\d{3,}")


# --------------------------------------------------------------------------
# scoring helpers (pure)
# --------------------------------------------------------------------------
def _to_float(v: Any) -> Optional[float]:
    """Numeric value of a requirement; tolerates strings like '1.050' / '1050,5'."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.count(".") > 1 or (s.count(".") == 1 and "," in s):   # '1.050' / '1.050,5' -> thousands dot
        s = s.replace(".", "")
    m = _NUM_RE.search(s.replace(",", "."))
    return float(m.group(0)) if m else None


def _usable(req: Dict[str, Any], allowed: List[str]) -> bool:
    return req.get("target_column") in allowed and _to_float(req.get("value")) is not None


def _main_requirement(reqs: List[Dict[str, Any]], allowed: List[str], expected_col: Optional[str]) -> Optional[Dict[str, Any]]:
    """The requirement that would drive the ranking for this query: first usable
    one on the expected column, else the first usable one at all."""
    usable = [r for r in reqs if _usable(r, allowed)]
    for r in usable:
        if expected_col and r.get("target_column") == expected_col:
            return r
    return usable[0] if usable else None


def _doc_grounded(regulation: str, hits: List[Dict[str, Any]]) -> bool:
    """'regulation' cites a document that was in context: a law number or year
    (>= 3 digits) from a retrieved doc_name appears in the citation string."""
    if not regulation or not hits:
        return False
    cited = set(_LAW_NUM_RE.findall(regulation))
    for h in hits:
        if cited & set(_LAW_NUM_RE.findall(h.get("doc_name") or "")):
            return True
    return False


def _norm_num(s: str) -> str:
    f = float(s.replace(",", "."))
    return f"{f:g}"


def _value_grounded(description: str, context: str) -> Tuple[Optional[bool], float]:
    """Do the per-unit figures quoted in the description occur in the context?
    Only decimals and numbers >= 10 are checked (small integers are noise).
    Returns (any_found | None if nothing checkable, fraction_found)."""
    if not description or not context:
        return None, 0.0
    ctx = context.replace(",", ".")
    nums = []
    for m in _NUM_RE.findall(description):
        n = _norm_num(m)
        if ("." in n) or float(n) >= 10:
            nums.append(n)
    nums = list(dict.fromkeys(nums))
    if not nums:
        return None, 0.0
    found = [bool(re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", ctx)) for n in nums]
    return any(found), sum(found) / len(found)


def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _rate(flags: List[Optional[bool]]) -> Optional[float]:
    flags = [f for f in flags if f is not None]
    return sum(flags) / len(flags) if flags else None


# --------------------------------------------------------------------------
# one agent call -> one scored record
# --------------------------------------------------------------------------
def _score_run(q: Dict[str, Any], arm: str, raw_text: str, retrieval: Dict[str, Any],
               context_text: str, latency_s: float, allowed: List[str]) -> Dict[str, Any]:
    from app.utils.json_parser import safe_extract_json
    from app.services.llm.agents.schema import RegulatoryResponse

    exp = q.get("expect", {})
    data = safe_extract_json(raw_text, schema=RegulatoryResponse)
    reqs = list(data.requirements) if data else []
    found = bool(data.found) if data else False
    main = _main_requirement(reqs, allowed, exp.get("target_column"))
    activated = found and main is not None
    value = _to_float(main.get("value")) if main else None

    lo, hi = exp.get("value_min"), exp.get("value_max")
    in_range = None
    if lo is not None and hi is not None:
        in_range = (value is not None) and (lo <= value <= hi)

    expect_act = exp.get("activate")
    hits = retrieval.get("hits") or []
    per_req = []
    for r in reqs:
        vg_any, vg_frac = _value_grounded(str(r.get("description") or ""), context_text)
        per_req.append({
            "target_column": r.get("target_column"),
            "operator": r.get("operator"),
            "value": r.get("value"),
            "unit": r.get("unit"),
            "regulation": r.get("regulation"),
            "usable": _usable(r, allowed),
            "doc_grounded": _doc_grounded(str(r.get("regulation") or ""), hits),
            "value_grounded": vg_any,
            "value_grounded_frac": vg_frac,
        })

    return {
        "id": q["id"], "arm": arm, "expect_activate": expect_act,
        "found": found, "n_requirements": len(reqs), "activated": activated,
        "main_value": value,
        "main_target": main.get("target_column") if main else None,
        "main_operator": main.get("operator") if main else None,
        "main_regulation": main.get("regulation") if main else None,
        "target_ok": (main.get("target_column") == exp.get("target_column")) if (main and exp.get("target_column")) else None,
        "operator_ok": (str(main.get("operator", "")).strip() == exp.get("operator")) if (main and exp.get("operator")) else None,
        "value_in_range": in_range,
        # activation correctness relative to the expectation
        "activation_ok": (activated if expect_act is True else (not activated) if expect_act is False else None),
        "requirements": per_req,
        "context_chars": retrieval.get("context_chars", 0),
        "context_tokens_approx": (retrieval.get("context_chars", 0) or 0) // 4,
        "n_chunks": retrieval.get("n_chunks", 0),
        "retrieved": [f"{h.get('doc_name','?')[:28]} {h.get('article_ref','')}" for h in hits],
        "latency_s": round(latency_s, 2),
        "retrieval_error": retrieval.get("error"),
        "raw_text": raw_text,
    }


def _aggregate(records: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    exp_true = [r for r in records if r["expect_activate"] is True]
    exp_false = [r for r in records if r["expect_activate"] is False]
    exp_either = [r for r in records if r["expect_activate"] == "either"]
    all_reqs = [rq for r in records for rq in r["requirements"]]
    usable_reqs = [rq for rq in all_reqs if rq["usable"]]

    agg = {
        "n_runs": len(records),
        "activation_rate": _rate([r["activated"] for r in exp_true]),
        "false_activation_rate": _rate([r["activated"] for r in exp_false]),
        "either_activation_rate": _rate([r["activated"] for r in exp_either]),
        "value_in_range_rate": _rate([bool(r["value_in_range"]) for r in records if r["value_in_range"] is not None]),
        "target_ok_rate": _rate([r["target_ok"] for r in exp_true]),
        "operator_ok_rate": _rate([r["operator_ok"] for r in exp_true]),
        "n_requirements_total": len(all_reqs),
        "n_requirements_usable": len(usable_reqs),
        "doc_grounding_rate": _rate([rq["doc_grounded"] for rq in all_reqs]),
        "value_grounding_rate": _rate([rq["value_grounded"] for rq in all_reqs]),
        "mean_context_tokens": _mean([r["context_tokens_approx"] for r in records]),
        "mean_context_chars": _mean([r["context_chars"] for r in records]),
        "mean_latency_s": _mean([r["latency_s"] for r in records]),
        "retrieval_errors": sum(1 for r in records if r["retrieval_error"]),
    }
    if repeats > 1:
        by_id: Dict[str, List[Optional[float]]] = {}
        for r in records:
            by_id.setdefault(r["id"], []).append(r["main_value"])
        agg["consistency_rate"] = _rate([len(set(v)) == 1 for v in by_id.values()])
        agg["value_cv_mean"] = _mean([
            (st.pstdev(v) / st.mean(v)) if all(x is not None for x in v) and st.mean(v) else None
            for v in by_id.values() if len(v) > 1
        ])
    return agg


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt(x: Optional[float], pct: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x*100:.0f}%" if pct else f"{x:,.0f}"


def _summary_md(aggs: Dict[str, Dict[str, Any]], meta: Dict[str, Any], n_q: int, repeats: int) -> str:
    arms = list(aggs)
    head = "| metric | " + " | ".join(arms) + " |"
    sep = "|---|" + "---|" * len(arms)

    def row(label: str, key: str, pct: bool = True) -> str:
        return f"| {label} | " + " | ".join(_fmt(aggs[a].get(key), pct) for a in arms) + " |"

    lines = [
        "# Downstream evaluation — regulatory agent under three retrieval arms",
        "",
        f"**Scope:** {meta.get('country')} / {meta.get('use_case')} · {n_q} production-shaped queries"
        f" · {repeats} repeat(s) · extraction LLM per `.env` · retrieval flags:"
        f" framing={config.QUERY_FRAMING}, quant_only={config.QUANT_ONLY}, top_k={config.DEFAULT_TOP_K}",
        "",
        "## Does the agent produce a usable, correct, grounded constraint?",
        "",
        head, sep,
        row("activation (expected-true queries)", "activation_rate"),
        row("false activation (negative controls) ↓", "false_activation_rate"),
        row("activation on out-of-corpus use cases", "either_activation_rate"),
        row("value inside standard-derived range", "value_in_range_rate"),
        row("target column correct", "target_ok_rate"),
        row("operator correct", "operator_ok_rate"),
        row("doc grounding (cited doc was in context)", "doc_grounding_rate"),
        row("value grounding (quoted figures in context)", "value_grounding_rate"),
        "",
        "## Cost",
        "",
        head, sep,
        row("~context tokens / query", "mean_context_tokens", pct=False),
        row("latency s / query", "mean_latency_s", pct=False),
        row("requirements produced (total)", "n_requirements_total", pct=False),
        "",
    ]
    if repeats > 1:
        lines += ["## Consistency", "", head, sep, row("identical main value across repeats", "consistency_rate"), ""]
    lines += [
        "Notes: *none* is the pre-RAG production state (no documents reach the agent), so its grounding is",
        "structurally 0 — any requirement it emits comes from model priors. *dump* holds the jurisdiction scope",
        "constant and removes only the semantic ranking. Ranges follow the DM 1256/2021 All. A reading stated in",
        "`downstream_queries.json` and are author-constructed.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# retrieval-config ablation (no LLM)
# --------------------------------------------------------------------------
def ablate(queries: List[Dict[str, Any]], meta: Dict[str, Any], top_k: int) -> Dict[str, Any]:
    """Rank of the first *dimensioning* chunk under the 4 retrieval configs."""
    key2name = _doc_key_to_name()
    targets = {(key2name.get(t["doc_key"], t["doc_key"]), t["article_ref"]) for t in meta.get("dimensioning_refs", [])}
    if not targets:
        print("meta.dimensioning_refs is empty — nothing to ablate.")
        return {}
    configs = [("raw", False, False), ("framing", True, False), ("quant_only", False, True), ("framing+quant", True, True)]
    qs = [q for q in queries if q.get("expect", {}).get("activate") is True]
    out: Dict[str, Any] = {}
    print(f"Retrieval ablation — rank of first dimensioning chunk (top-{top_k}) on {len(qs)} expected-true queries")
    print("  " + f"{'id':6}" + "".join(f"{c[0]:>15}" for c in configs))
    ranks: Dict[str, List[Optional[int]]] = {c[0]: [] for c in configs}
    for q in qs:
        line = f"  {q['id']:6}"
        for name, fr, qo in configs:
            hits = search(q["query"], country=meta["country"], use_case=meta["use_case"], top_k=top_k,
                          mode="rag", frame=fr, quantitative_only=qo)
            rank = next((i + 1 for i, h in enumerate(hits) if (h.get("doc_name"), h.get("article_ref")) in targets), None)
            ranks[name].append(rank)
            line += f"{(str(rank) if rank else 'miss'):>15}"
        print(line)
    print("  " + f"{'hit@k':6}" + "".join(f"{_fmt(_rate([r is not None for r in v])):>15}" for v in ranks.values()))
    print("  " + f"{'MRR':6}" + "".join(f"{(_mean([1/r if r else 0 for r in v]) or 0):>15.3f}" for v in ranks.values()))
    for name, v in ranks.items():
        out[name] = {"ranks": v, "hit_rate": _rate([r is not None for r in v]),
                     "mrr": _mean([1 / r if r else 0 for r in v])}
    return out


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------
def run(arms: List[str], repeats: int, json_out: Optional[str], queries_path: Path,
        dry_run: bool = False, do_ablate: bool = False) -> int:
    gold = json.loads(Path(queries_path).read_text(encoding="utf-8"))
    meta, queries = gold["meta"], gold["queries"]
    country = meta.get("country", config.DEFAULT_COUNTRY)
    use_case = meta.get("use_case", config.DEFAULT_USE_CASE)

    print(f"Downstream eval : {Path(queries_path).name} — {len(queries)} queries, arms={arms}, repeats={repeats}")
    print(f"Retrieval       : mode flags framing={config.QUERY_FRAMING} quant_only={config.QUANT_ONLY} top_k={config.DEFAULT_TOP_K}\n")

    ablation = ablate(queries, meta, config.DEFAULT_TOP_K) if do_ablate else None
    if do_ablate and dry_run and not arms:
        return 0

    from app.services.llm.agents.regulatory_agent import RegulatoryAgent, load_regulatory_context
    from app.core.constants import REGULATORY_AGENT_COLUMNS

    if dry_run:
        for q in queries:
            print(f"\n[{q['id']}] {q['query']}")
            for arm in arms:
                docs, _, _, tr = load_regulatory_context(q["query"], retrieval_mode=arm, country=country, use_case=use_case)
                refs = ", ".join(f"{h['doc_name'][:16]}|{h['article_ref']}" for h in tr["hits"][:8])
                print(f"   {arm:5} n={tr['n_chunks']:3d} chars={tr['context_chars']:6d}  {refs}")
        return 0

    agent = RegulatoryAgent()
    records: List[Dict[str, Any]] = []
    for arm in arms:
        print(f"\n=== arm: {arm} ===")
        print(f"  {'id':6}{'rep':>3}  {'act':>4} {'value':>8} {'range':>6} {'dGr':>4} {'vGr':>4} {'ctx~tok':>8} {'s':>5}  regulation")
        for q in queries:
            for rep in range(repeats):
                t0 = time.time()
                res = agent.run(query=q["query"], available_columns=REGULATORY_AGENT_COLUMNS,
                                statistics=None, retrieval_mode=arm, country=country, use_case=use_case)
                dt = time.time() - t0
                retrieval = res.retrieval or {"mode": arm, "n_chunks": 0, "context_chars": 0, "hits": [],
                                              "error": "agent fallback (no retrieval trace)"}
                # context text as the prompt saw it (for value grounding)
                ctx_text = res.prompt.user if res.prompt else ""
                rec = _score_run(q, arm, res.raw_text, retrieval, ctx_text, dt, REGULATORY_AGENT_COLUMNS)
                rec["repeat"] = rep
                records.append(rec)
                dg = _rate([r["doc_grounded"] for r in rec["requirements"]])
                vg = _rate([r["value_grounded"] for r in rec["requirements"]])
                rng = "—" if rec["value_in_range"] is None else ("ok" if rec["value_in_range"] else "OUT")
                val = f"{rec['main_value']:.0f}" if rec["main_value"] is not None else "-"
                act = "yes" if rec["activated"] else "no"
                print(f"  {rec['id']:6}{rep:>3}  {act:>4} {val:>8} {rng:>6} {_fmt(dg):>4} {_fmt(vg):>4} "
                      f"{rec['context_tokens_approx']:>8,} {rec['latency_s']:>5.1f}  {(rec['main_regulation'] or '')[:60]}")

    aggs = {arm: _aggregate([r for r in records if r["arm"] == arm], repeats) for arm in arms}

    print("\n=== Aggregate ===")
    print(f"  {'metric':44}" + "".join(f"{a:>10}" for a in arms))
    for label, key, pct in [
        ("activation (expected-true)", "activation_rate", True),
        ("false activation (negative controls)", "false_activation_rate", True),
        ("activation on out-of-corpus use cases", "either_activation_rate", True),
        ("value inside standard-derived range", "value_in_range_rate", True),
        ("target column correct", "target_ok_rate", True),
        ("operator correct", "operator_ok_rate", True),
        ("doc grounding", "doc_grounding_rate", True),
        ("value grounding", "value_grounding_rate", True),
        ("~context tokens / query", "mean_context_tokens", False),
        ("latency s / query", "mean_latency_s", False),
    ]:
        print(f"  {label:44}" + "".join(f"{_fmt(aggs[a].get(key), pct):>10}" for a in arms))
    if repeats > 1:
        print(f"  {'consistency (identical value across repeats)':44}" + "".join(f"{_fmt(aggs[a].get('consistency_rate')):>10}" for a in arms))

    if json_out:
        out = Path(json_out)
        out.write_text(json.dumps({
            "meta": {"queries_file": Path(queries_path).name, "country": country, "use_case": use_case,
                     "arms": arms, "repeats": repeats, "n_queries": len(queries),
                     "embed_model": config.EMBED_MODEL, "top_k": config.DEFAULT_TOP_K,
                     "query_framing": config.QUERY_FRAMING, "quant_only": config.QUANT_ONLY},
            "aggregate": aggs,
            "retrieval_ablation": ablation,
            "runs": records,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = out.with_name(out.stem + "_summary.md")
        summary.write_text(_summary_md(aggs, meta, len(queries), repeats), encoding="utf-8")
        print(f"\nWrote {out}\nWrote {summary}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Downstream (agent-output) evaluation of the normative RAG.")
    p.add_argument("--arms", default="none,dump,rag", help="comma-separated retrieval arms (default none,dump,rag)")
    p.add_argument("--repeats", type=int, default=1, help="repeat each query N times (consistency)")
    p.add_argument("--json", default=None, help="write full results to this JSON path (+ _summary.md)")
    p.add_argument("--queries", default=str(QUERIES_PATH), help="downstream query set")
    p.add_argument("--dry-run", action="store_true", help="retrieval only, no LLM calls")
    p.add_argument("--ablate", action="store_true", help="also run the retrieval-config ablation (embedding only)")
    args = p.parse_args(argv)
    arms = [a.strip().lower() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in config.RETRIEVAL_MODES]
    if bad:
        p.error(f"unknown arm(s) {bad}; choose from {config.RETRIEVAL_MODES}")
    return run(arms, args.repeats, args.json, Path(args.queries), dry_run=args.dry_run, do_ablate=args.ablate)


if __name__ == "__main__":
    sys.exit(main())