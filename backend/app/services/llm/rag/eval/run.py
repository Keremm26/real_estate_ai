"""
Retrieval evaluation runner — turns gold_queries.json into the numbers.

    python -m app.services.llm.rag.eval.run [--k 1,3,5,8] [--json out.json]

For every gold query it runs the retriever, marks which retrieved chunks are
relevant (by doc + article), and reports recall@k / hit@k / nDCG@k / precision@k
(averaged) plus MRR. It also computes the dump-vs-rag context cost so the
"same coverage, far fewer tokens" story is quantified.

Requires an ingested store and a reachable embedding model (``ollama pull
bge-m3`` + ``python -m app.services.llm.rag.ingest --city turin``). Until then
it prints a clear "store empty" message instead of crashing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app.services.llm.rag import config
from app.services.llm.rag.manifest import MANIFESTS
from app.services.llm.rag.retriever import _format, search
from app.services.llm.rag.eval import metrics

GOLD_PATH = Path(__file__).resolve().parent / "gold_queries.json"


def _doc_key_to_name() -> Dict[str, str]:
    """Map every manifest doc_key to its official doc_name (how it's stored)."""
    out: Dict[str, str] = {}
    for specs in MANIFESTS.values():
        for spec in specs:
            out[spec["doc_key"]] = spec["doc_name"]
    return out


def _relevant(query: Dict[str, Any], key2name: Dict[str, str]) -> Tuple[Set[Tuple[str, str]], Set[str]]:
    """Gold labels split into article-level {(doc_name, article_ref)} and
    doc-level {doc_name}. A relevant entry with article_ref '*' is doc-level
    ('any chunk from this document counts'), used for annex docs like the DM 1256
    Allegato whose sections have no stable, citable article numbers."""
    articles: Set[Tuple[str, str]] = set()
    docs: Set[str] = set()
    for r in query["relevant"]:
        name = key2name.get(r["doc_key"], r["doc_key"])
        if r["article_ref"] == "*":
            docs.add(name)
        else:
            articles.add((name, r["article_ref"]))
    return articles, docs


def _flags(hits: List[Dict[str, Any]], articles: Set[Tuple[str, str]], docs: Set[str]) -> List[bool]:
    """Per-rank relevance. Each doc-level (wildcard) label is satisfied at most
    once — by its first retrieved chunk — so recall never exceeds num_relevant."""
    seen: Set[str] = set()
    out: List[bool] = []
    for h in hits:
        dn, ar = h.get("doc_name"), h.get("article_ref")
        if (dn, ar) in articles:
            out.append(True)
        elif dn in docs and dn not in seen:
            seen.add(dn)
            out.append(True)
        else:
            out.append(False)
    return out


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — enough for a relative comparison."""
    return max(0, len(text) // 4)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _summary_md(agg: Dict[str, Any], k_values: List[int], country: str,
                use_case: str, n_queries: int, n_chunks: int) -> str:
    """Compact, human-readable overall summary (good for the write-up / slides)."""
    ks = " | ".join(f"@{k}" for k in k_values)

    def row(name: str, field: str) -> str:
        return "| " + name + " | " + " | ".join(f"{agg[field][k]:.3f}" for k in k_values) + " |"

    return "\n".join([
        "# Retrieval evaluation — normative RAG",
        "",
        f"**Scope:** {country} / {use_case} · embed `{config.EMBED_MODEL}` · "
        f"{n_queries} gold queries · {n_chunks} chunks in scope",
        "",
        "## Overall retrieval quality (mean over queries)",
        "",
        f"| metric | {ks} |",
        "|" + "---|" * (len(k_values) + 1),
        row("recall", "recall"),
        row("hit@k", "hit"),
        row("nDCG", "ndcg"),
        row("precision*", "precision"),
        "",
        f"**MRR = {agg['mrr']:.3f}**",
        "",
        "\\* precision is capped at 1/k for single-relevant queries — reported, not headline.",
        "",
        "## Efficiency (context sent to the LLM)",
        "",
        "| mode | ~tokens/query |",
        "|---|---|",
        f"| dump (all {n_chunks} in-scope chunks) | {agg['dump_tokens']:,} |",
        f"| rag (top-{config.DEFAULT_TOP_K}) | {agg['rag_avg_tokens']:,.0f} |",
        "",
        f"**{agg['reduction_pct']:.1f}% smaller context with RAG.**",
        "",
    ])


def run(k_values: List[int], json_out: str | None, gold_path: Path = GOLD_PATH) -> int:
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    meta = gold["meta"]
    queries = gold["queries"]
    country = meta.get("country", config.DEFAULT_COUNTRY)
    use_case = meta.get("use_case", config.DEFAULT_USE_CASE)
    key2name = _doc_key_to_name()
    depth = max(k_values + [config.DEFAULT_TOP_K])

    print(f"Corpus scope : country={country} use_case={use_case}")
    print(f"Embed model  : {config.EMBED_MODEL}   |   retrieval depth={depth}")
    print(f"Gold queries : {len(queries)}   ({Path(gold_path).name})\n")

    # dump baseline is query-independent — compute its cost once
    dump_hits = search("", country=country, use_case=use_case, mode="dump")
    if not dump_hits:
        print("!! Store is empty (or embedding backend unreachable).")
        print("   Run:  ollama pull bge-m3  &&  python -m app.services.llm.rag.ingest --city turin --rebuild")
        return 1
    dump_ctx, _ = _format(dump_hits)
    dump_tokens = _approx_tokens(dump_ctx)

    per_query: List[Dict[str, Any]] = []
    rag_token_list: List[int] = []

    for q in queries:
        articles, docs = _relevant(q, key2name)
        hits = search(q["query"], country=country, use_case=use_case, top_k=depth, mode="rag")
        flags = _flags(hits, articles, docs)
        num_rel = len(articles) + len(docs)

        rag_ctx, _ = _format(hits[: config.DEFAULT_TOP_K])
        rag_tokens = _approx_tokens(rag_ctx)
        rag_token_list.append(rag_tokens)

        first_rank = next((i + 1 for i, f in enumerate(flags) if f), None)
        top1 = f"{hits[0].get('doc_name','?')[:22]} {hits[0].get('article_ref','')}" if hits else "-"

        per_query.append({
            "id": q["id"], "lang": q["lang"], "num_relevant": num_rel,
            "first_hit_rank": first_rank, "top1": top1,
            "rr": metrics.reciprocal_rank(flags),
            "recall": {k: metrics.recall_at_k(flags, num_rel, k) for k in k_values},
            "hit": {k: metrics.hit_at_k(flags, k) for k in k_values},
            "ndcg": {k: metrics.ndcg_at_k(flags, num_rel, k) for k in k_values},
            "precision": {k: metrics.precision_at_k(flags, k) for k in k_values},
            "rag_tokens": rag_tokens,
        })

    # ---- per-query detail (doubles as the qualitative "what RAG selected" view) ----
    print("Per-query (rank of first correct article, top-1 retrieved):")
    print(f"  {'id':4} {'lang':4} {'rank':>4}  {'RR':>4}  top-1 retrieved")
    for r in per_query:
        rank = r["first_hit_rank"] if r["first_hit_rank"] else "miss"
        print(f"  {r['id']:4} {r['lang']:4} {str(rank):>4}  {r['rr']:.2f}  {r['top1']}")

    # ---- aggregate table ----
    print("\nAggregate (mean over queries):")
    header = "  metric      " + "".join(f"@{k:<6}" for k in k_values)
    print(header)
    for name, field in [("recall", "recall"), ("hit", "hit"), ("nDCG", "ndcg"), ("precision*", "precision")]:
        row = "".join(f"{_mean([r[field][k] for r in per_query]):<7.3f}" for k in k_values)
        print(f"  {name:<11} {row}")
    mrr = _mean([r["rr"] for r in per_query])
    print(f"\n  MRR        {mrr:.3f}")
    print("  * precision is capped at 1/k for single-relevant queries — reported, not headline.")

    # ---- efficiency: dump vs rag context cost ----
    rag_avg = _mean([float(t) for t in rag_token_list])
    reduction = (1 - rag_avg / dump_tokens) * 100 if dump_tokens else 0.0
    print("\nContext cost (approx tokens sent to the LLM):")
    print(f"  dump (all in-scope) : {len(dump_hits)} chunks, ~{dump_tokens} tokens")
    print(f"  rag  (top-{config.DEFAULT_TOP_K})        : ~{rag_avg:.0f} tokens/query  ->  {reduction:.1f}% smaller")

    aggregate = {
        "recall": {k: _mean([r["recall"][k] for r in per_query]) for k in k_values},
        "hit": {k: _mean([r["hit"][k] for r in per_query]) for k in k_values},
        "ndcg": {k: _mean([r["ndcg"][k] for r in per_query]) for k in k_values},
        "precision": {k: _mean([r["precision"][k] for r in per_query]) for k in k_values},
        "mrr": mrr,
        "dump_tokens": dump_tokens, "rag_avg_tokens": rag_avg, "reduction_pct": reduction,
    }

    if json_out:
        out = Path(json_out)
        out.write_text(json.dumps({
            "meta": {"country": country, "use_case": use_case, "embed_model": config.EMBED_MODEL,
                     "k_values": k_values, "n_queries": len(queries),
                     "n_chunks_in_scope": len(dump_hits)},
            "aggregate": aggregate,
            "per_query": per_query,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = out.with_name(out.stem + "_summary.md")
        summary.write_text(
            _summary_md(aggregate, k_values, country, use_case, len(queries), len(dump_hits)),
            encoding="utf-8",
        )
        print(f"\nWrote {out}\nWrote {summary}")

    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evaluate normative retrieval against the gold set.")
    p.add_argument("--k", default="1,3,5,8", help="comma-separated k values (default 1,3,5,8)")
    p.add_argument("--json", default=None, help="also write full results to this JSON path")
    p.add_argument("--gold", default=str(GOLD_PATH),
                   help="gold-set JSON to evaluate against (default: the Italian set)")
    args = p.parse_args(argv)
    k_values = sorted({int(x) for x in args.k.split(",") if x.strip()})
    return run(k_values, args.json, Path(args.gold))


if __name__ == "__main__":
    sys.exit(main())
