"""
Downstream evaluation — does retrieval change what the regulatory agent PRODUCES?

    python -m app.services.llm.rag.eval.downstream [--arms none,rag] [--langs it,en]
                                                   [--ids DQ-01,DQ-20] [--repeats 1]
                                                   [--routing router|oracle] [--json out.json]
                                                   [--queries path] [--dry-run] [--ablate]

``run.py`` scores the *ranking* against a gold set of legal questions. This
runner closes the loop: it executes the actual ``RegulatoryAgent`` (filtering
mode — the LLM extraction step) on production-shaped property requests under
each retrieval arm, and scores the extracted requirement against the item's
expectation (``downstream_queries_turin.json``, schema downstream-v2).

Arms (``config.RETRIEVAL_MODES``): none = legacy loader (production before this
work — no documents), rag = router + cascade + semantic top-k. (dump = every
in-scope chunk, unranked — still available, not run by default.)

Each item is run once per language (``queries: {it, en}``). Scoring lives in
``regulatory_scoring.py`` (shared with the pipeline benchmark):
  activation_ok        right outcome: activate / decline / ('either': decline, or
                       activate with a grounded, in-range value)
  value in range       main surface value inside the item's expected range
  echo rate            activated runs whose value repeats a number the user stated
  source in context    an expected source article was among the retrieved chunks
                       (separates retrieval misses from extraction misses)
  winner cited         precedence items: the regulation cited is the winner
  routing ok           the router's use case matches the item's (rag arm, --routing router)
  doc / value grounding citation names a retrieved document / quoted figures occur
                       in the retrieved documents (not in the user query)
Breakdowns by language, type and tier.

``--routing oracle`` passes the item's use case instead of routing, isolating
extraction from routing errors. ``--dry-run`` performs retrieval only (no LLM).
``--ablate`` compares the four extraction-oriented retrieval configurations by
the rank of the first chunk matching each item's sources — embedding cost only.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.llm.rag import config
from app.services.llm.rag.eval import regulatory_scoring as rs
from app.services.llm.rag.eval.run import _doc_key_to_name, _hit_matches, _label
from app.services.llm.rag.retriever import search

QUERIES_PATH = Path(__file__).resolve().parent / "downstream_queries_turin.json"

HEADLINE = [
    ("right outcome (activate / decline)", "activation_ok_rate", True),
    ("activation (expected-true items)", "activation_rate", True),
    ("correct decline (expected-false items)", "correct_decline_rate", True),
    ("'either' items handled correctly", "either_ok_rate", True),
    ("value inside expected range", "value_in_range_rate", True),
    ("echo of a user-stated number ↓", "echo_rate", True),
    ("target column correct", "target_ok_rate", True),
    ("operator correct", "operator_ok_rate", True),
    ("source article in retrieved context", "source_in_context_rate", True),
    ("precedence winner cited", "winner_cited_rate", True),
    ("router use case correct", "routing_ok_rate", True),
    ("main requirement cites a retrieved doc", "main_doc_grounding_rate", True),
    ("quoted figures found in retrieved docs", "value_grounding_rate", True),
    ("~context tokens / run", "mean_context_tokens", False),
    ("latency s / run", "mean_latency_s", False),
]


def _fmt(x: Optional[float], pct: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x*100:.0f}%" if pct else f"{x:,.0f}"


def _aggregate(records: List[Dict[str, Any]], repeats: int) -> Dict[str, Any]:
    agg = rs.aggregate(records)
    agg["mean_context_tokens"] = rs.mean(r["context_tokens_approx"] for r in records)
    agg["mean_latency_s"] = rs.mean(r["latency_s"] for r in records)
    agg["retrieval_errors"] = sum(1 for r in records if r["retrieval_error"])
    if repeats > 1:
        by_run: Dict[tuple, List[Optional[float]]] = defaultdict(list)
        for r in records:
            by_run[(r["id"], r["lang"])].append(r["main_value"])
        agg["consistency_rate"] = rs.rate(len(set(v)) == 1 for v in by_run.values())
        agg["value_cv_mean"] = rs.mean(
            (st.pstdev(v) / st.mean(v)) if all(x is not None for x in v) and st.mean(v) else None
            for v in by_run.values() if len(v) > 1
        )
    return agg


def _breakdown(records: List[Dict[str, Any]], field: str) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[str(r.get(field))].append(r)
    return {k: {"n_runs": len(v), "activation_ok_rate": rs.rate(x["activation_ok"] for x in v),
                "value_in_range_rate": rs.rate(x["value_in_range"] for x in v),
                "echo_rate": rs.rate(x["echo"] for x in v if x["activated"])}
            for k, v in sorted(groups.items())}


def _summary_md(aggs: Dict[str, Dict[str, Any]], breakdowns: Dict[str, Dict[str, Any]],
                meta: Dict[str, Any], n_items: int, langs: List[str], repeats: int, routing: str) -> str:
    arms = list(aggs)
    head = "| metric | " + " | ".join(arms) + " |"
    sep = "|---|" + "---|" * len(arms)
    lines = [
        "# Downstream evaluation — regulatory agent, none vs rag",
        "",
        f"**Scope:** {meta.get('country')} · {n_items} items × {len(langs)} language(s) ({', '.join(langs)})"
        f" · {repeats} repeat(s) · routing={routing} · extraction LLM = agents' model"
        f" · retrieval: quant_only={config.QUANT_ONLY}, top_k={config.DEFAULT_TOP_K}",
        "",
        head, sep,
    ]
    lines += [f"| {label} | " + " | ".join(_fmt(aggs[a].get(key), pct) for a in arms) + " |"
              for label, key, pct in HEADLINE]
    if repeats > 1:
        lines.append("| identical value across repeats | " + " | ".join(_fmt(aggs[a].get("consistency_rate")) for a in arms) + " |")
    for field in ("lang", "type"):
        lines += ["", f"## Right outcome by {field}", "",
                  f"| {field} | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
        keys = sorted({k for a in arms for k in breakdowns[a][field]})
        for k in keys:
            lines.append(f"| {k} | " + " | ".join(
                _fmt((breakdowns[a][field].get(k) or {}).get("activation_ok_rate")) for a in arms) + " |")
    lines += [
        "",
        "Notes: *none* is the pre-RAG production state (no documents reach the agent): any requirement it",
        "emits comes from model priors, so its grounding is structurally 0. Items and ranges are defined in",
        "`downstream_queries_turin.json` (author-constructed; see its meta.caveats).",
        "",
    ]
    return "\n".join(lines)


def ablate(queries: List[Dict[str, Any]], country: str, top_k: int) -> Dict[str, Any]:
    """Rank of the first chunk matching each expected-true item's sources, under
    the four extraction-oriented retrieval configurations (no LLM)."""
    key2name = _doc_key_to_name()
    configs = [("raw", False, False), ("framing", True, False), ("quant_only", False, True), ("framing+quant", True, True)]
    items = [q for q in queries if q["expect"]["activate"] is True and q.get("sources")]
    ranks: Dict[str, List[Optional[int]]] = {c[0]: [] for c in configs}
    print(f"Retrieval ablation — rank of first source chunk (top-{top_k}) on {len(items)} expected-true items")
    print("  " + f"{'id':7}" + "".join(f"{c[0]:>15}" for c in configs))
    for q in items:
        labels = [_label(s, key2name) for s in q["sources"]]
        line = f"  {q['id']:7}"
        for name, fr, qo in configs:
            hits = search(q["queries"]["it"], country=country, use_case=q.get("use_case") or config.DEFAULT_USE_CASE,
                          top_k=top_k, mode="rag", frame=fr, quantitative_only=qo)
            rank = next((i + 1 for i, h in enumerate(hits) if any(_hit_matches(h, lab) for lab in labels)), None)
            ranks[name].append(rank)
            line += f"{(str(rank) if rank else 'miss'):>15}"
        print(line)
    out = {name: {"ranks": v, "hit_rate": rs.rate(r is not None for r in v),
                  "mrr": rs.mean(1 / r if r else 0 for r in v)} for name, v in ranks.items()}
    print("  " + f"{'hit@k':7}" + "".join(f"{_fmt(o['hit_rate']):>15}" for o in out.values()))
    return out


def run(arms: List[str], repeats: int, json_out: Optional[str], queries_path: Path, *,
        langs: List[str], ids: Optional[List[str]] = None, routing: str = "router",
        dry_run: bool = False, do_ablate: bool = False) -> int:
    gold = json.loads(Path(queries_path).read_text(encoding="utf-8"))
    meta, queries = gold["meta"], gold["queries"]
    if ids:
        queries = [q for q in queries if q["id"] in set(ids)]
    country = meta.get("country", config.DEFAULT_COUNTRY)
    key2name = _doc_key_to_name()

    print(f"Downstream eval : {Path(queries_path).name} — {len(queries)} items × {langs}, arms={arms}, "
          f"repeats={repeats}, routing={routing}")
    print(f"Retrieval       : quant_only={config.QUANT_ONLY} framing={config.QUERY_FRAMING} top_k={config.DEFAULT_TOP_K}\n")

    ablation = ablate(queries, country, config.DEFAULT_TOP_K) if do_ablate else None

    from app.core.constants import REGULATORY_AGENT_COLUMNS
    from app.services.llm.agents.regulatory_agent import RegulatoryAgent, load_regulatory_context

    def use_case_for(q: Dict[str, Any]) -> Optional[str]:
        return q.get("use_case") if routing == "oracle" else None   # None -> the LLM router decides

    if dry_run:
        for q in queries:
            for lang in langs:
                text = q["queries"][lang]
                print(f"\n[{q['id']} {lang}] {text[:110]}")
                for arm in arms:
                    _, _, _, tr = load_regulatory_context(text, retrieval_mode=arm, country=country,
                                                          use_case=use_case_for(q))
                    refs = ", ".join(f"{h['doc_name'][:16]}|{h['article_ref']}" for h in tr["hits"][:6])
                    print(f"   {arm:5} n={tr['n_chunks']:3d} routed={(tr.get('use_case_routing') or {}).get('use_case')}  {refs}")
        return 0

    agent = RegulatoryAgent()
    records: List[Dict[str, Any]] = []
    for arm in arms:
        print(f"\n=== arm: {arm} ===")
        print(f"  {'id':7}{'lang':>4}{'rep':>4}  {'ok':>3} {'act':>4} {'value':>8} {'range':>6} {'echo':>5} {'src':>4} {'s':>5}  regulation")
        for q in queries:
            for lang in langs:
                text = q["queries"][lang]
                for rep in range(repeats):
                    t0 = time.time()
                    res = agent.run(query=text, available_columns=REGULATORY_AGENT_COLUMNS, statistics=None,
                                    retrieval_mode=arm, country=country, use_case=use_case_for(q))
                    dt = time.time() - t0
                    retrieval = res.retrieval or {"mode": arm, "n_chunks": 0, "context_chars": 0, "hits": [],
                                                  "error": "agent fallback (no retrieval trace)"}
                    # Value grounding must look at the retrieved documents only: drop the user query
                    # (echoed in the prompt) so a figure the user stated cannot count as grounded.
                    ctx_text = (res.prompt.user if res.prompt else "").replace(text, "")
                    scored = rs.score_output(
                        q["expect"], res.raw_text, retrieval.get("hits") or [], REGULATORY_AGENT_COLUMNS,
                        user_values=q.get("user_values") or [], sources=q.get("sources"),
                        precedence=q.get("precedence"), expected_use_case=q.get("use_case"),
                        routing=retrieval.get("use_case_routing"), context_text=ctx_text, key2name=key2name)
                    rec = {"id": q["id"], "lang": lang, "type": q["type"], "tier": q.get("tier"),
                           "arm": arm, "repeat": rep, **scored,
                           "context_chars": retrieval.get("context_chars", 0) or 0,
                           "context_tokens_approx": (retrieval.get("context_chars", 0) or 0) // 4,
                           "n_chunks": retrieval.get("n_chunks", 0),
                           "retrieved": [f"{h.get('doc_name','?')[:28]} {h.get('article_ref','')}"
                                         for h in retrieval.get("hits") or []],
                           "latency_s": round(dt, 2), "retrieval_error": retrieval.get("error"),
                           "raw_text": res.raw_text}
                    records.append(rec)
                    val = f"{rec['main_value']:.0f}" if rec["main_value"] is not None else "-"
                    rng = "—" if rec["value_in_range"] is None else ("ok" if rec["value_in_range"] else "OUT")
                    echo = "—" if rec["echo"] is None else ("ECHO" if rec["echo"] else "no")
                    src = "—" if rec["source_in_context"] is None else ("y" if rec["source_in_context"] else "n")
                    print(f"  {q['id']:7}{lang:>4}{rep:>4}  {('✓' if rec['activation_ok'] else '✗'):>3} "
                          f"{('yes' if rec['activated'] else 'no'):>4} {val:>8} {rng:>6} {echo:>5} {src:>4} "
                          f"{rec['latency_s']:>5.1f}  {(rec['main_regulation'] or '')[:55]}")

    aggs = {arm: _aggregate([r for r in records if r["arm"] == arm], repeats) for arm in arms}
    breakdowns = {arm: {f: _breakdown([r for r in records if r["arm"] == arm], f) for f in ("lang", "type", "tier")}
                  for arm in arms}

    print("\n=== Aggregate ===")
    print(f"  {'metric':44}" + "".join(f"{a:>10}" for a in arms))
    for label, key, pct in HEADLINE:
        print(f"  {label:44}" + "".join(f"{_fmt(aggs[a].get(key), pct):>10}" for a in arms))

    if json_out:
        out = Path(json_out)
        out.write_text(json.dumps({
            "meta": {"queries_file": Path(queries_path).name, "country": country, "arms": arms,
                     "langs": langs, "repeats": repeats, "routing": routing, "n_items": len(queries),
                     "embed_model": config.EMBED_MODEL, "top_k": config.DEFAULT_TOP_K,
                     "query_framing": config.QUERY_FRAMING, "quant_only": config.QUANT_ONLY},
            "aggregate": aggs, "breakdowns": breakdowns, "retrieval_ablation": ablation, "runs": records,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = out.with_name(out.stem + "_summary.md")
        summary.write_text(_summary_md(aggs, breakdowns, meta, len(queries), langs, repeats, routing), encoding="utf-8")
        print(f"\nWrote {out}\nWrote {summary}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Downstream (agent-output) evaluation of the normative RAG.")
    p.add_argument("--arms", default="none,rag", help="comma-separated retrieval arms (default none,rag)")
    p.add_argument("--langs", default="it,en", help="query languages to run (default it,en)")
    p.add_argument("--ids", default=None, help="comma-separated item ids to run (default: all)")
    p.add_argument("--repeats", type=int, default=1, help="repeat each run N times (consistency)")
    p.add_argument("--routing", choices=["router", "oracle"], default="router",
                   help="router = LLM use-case router (production); oracle = item's use case")
    p.add_argument("--json", default=None, help="write full results to this JSON path (+ _summary.md)")
    p.add_argument("--queries", default=str(QUERIES_PATH), help="downstream query set")
    p.add_argument("--dry-run", action="store_true", help="retrieval only, no LLM calls")
    p.add_argument("--ablate", action="store_true", help="also run the retrieval-config ablation (embedding only)")
    args = p.parse_args(argv)
    arms = [a.strip().lower() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in config.RETRIEVAL_MODES]
    if bad:
        p.error(f"unknown arm(s) {bad}; choose from {config.RETRIEVAL_MODES}")
    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    return run(arms, args.repeats, args.json, Path(args.queries), langs=langs, ids=ids, routing=args.routing,
               dry_run=args.dry_run, do_ablate=args.ablate)


if __name__ == "__main__":
    sys.exit(main())
