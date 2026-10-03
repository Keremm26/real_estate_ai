"""
Retrieval evaluation runner — turns a gold set into the numbers.

    python -m app.services.llm.rag.eval.run [--gold FILE] [--k 1,3,5,8] [--json out.json] [--include-superseded]

For every gold item it runs the retriever once per query language, marks which
retrieved chunks are relevant (by doc + article), and reports recall@k / hit@k /
nDCG@k / precision@k (averaged over runs) plus MRR — overall and broken down by
language, item type and jurisdiction tier. Items that carry a ``precedence``
block (conflict / temporal cases) also get two ranking-order numbers:

  pair_recall   both the winning and the losing rule surfaced within depth
  winner_first  the winning rule was retrieved and ranks above the losing one

It also computes the dump-vs-rag context cost so the "same coverage, far fewer
tokens" story is quantified.

Gold item schema (v2):

    {"id": "to_017", "type": "factual", "tier": "national",
     "queries": {"en": "...", "it": "..."},          # one run per language
     "relevant": [{"doc_key": ..., "article_ref": ...}],   # ref may name several levels
     "precedence": {"winner": {...}, "loser": {...}, "rule": "..."},   # optional
     "gold_value": "...", "note": "..."}

The v1 shape (``lang`` + ``query``, no ``type``/``tier``) still loads: it is
read as a single-language item of type ``factual`` whose tier is that of its
first relevant document.

Requires an ingested store and a reachable embedding model (``ollama pull
bge-m3`` + ``python -m app.services.llm.rag.ingest --city turin``). Until then
it prints a clear "store empty" message instead of crashing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.llm.rag import config
from app.services.llm.rag.chunker import REF_SEP
from app.services.llm.rag.manifest import MANIFESTS
from app.services.llm.rag.retriever import _format, search
from app.services.llm.rag.eval import metrics

GOLD_PATH = Path(__file__).resolve().parent / "gold_queries_turin.json"

ITEM_TYPES = ("factual", "multi_article", "lex_specialis", "conflict", "temporal")
PRECEDENCE_RULES = ("hierarchy", "stricter_minimum", "delegated_derogation", "lex_specialis", "lex_posterior")

# A gold label, normalised to what the store carries: ("article", doc_name, *path),
# ("doc", doc_name) for the '*' wildcard, or ("anchor", doc_name, text) for a
# wildcard narrowed to chunks containing a phrase.
Label = Tuple[str, ...]


def _doc_key_to_name() -> Dict[str, str]:
    """Map every manifest doc_key to its official doc_name (how it's stored)."""
    out: Dict[str, str] = {}
    for specs in MANIFESTS.values():
        for spec in specs:
            out[spec["doc_key"]] = spec["doc_name"]
    return out


def _doc_key_to_tier() -> Dict[str, str]:
    return {spec["doc_key"]: spec["jurisdiction_level"].value
            for specs in MANIFESTS.values() for spec in specs}


_UNIT_WORD = re.compile(r"^[^\W\d_]+\.?\s+(?=\S)")


def _path(ref: str) -> Tuple[str, ...]:
    """A reference as the tuple of its normalised segments:
    'Art. 7 > 7.1 (part 2)' -> ('7', '7.1'), 'TÍTULO 7 > 7.3 > 7.3.4' -> ('7', '7.3', '7.3.4').
    A gold label names as many segments as it needs to be unambiguous — one for a
    document whose articles are top-level units, three for a code whose top level
    is a título holding hundreds of chunks."""
    return tuple(_ident(re.sub(r"\s*\(part \d+\)$", "", seg).strip())
                 for seg in (ref or "").split(REF_SEP) if seg.strip())


def _ident(ref: str) -> str:
    """The identifier a label and a chunk ref share, independent of whether the
    detector captured the unit word: 'Art. 3' / 'Articolo 3' / '3' -> '3',
    'Class MA' / 'MA' -> 'MA', 'Policy H15' -> 'H15'. Detectors differ in what
    they capture, so matching on the bare identifier keeps the score about
    retrieval rather than about capture style. Matching stays within one
    document, so a bare '3' can only meet that document's article 3."""
    return _UNIT_WORD.sub("", (ref or "").strip(), count=1).casefold()


def _norm(text: str) -> str:
    """Whitespace-collapsed, case-folded text: an anchor phrase must match the
    chunk even though the source wraps it across lines ('55% of average\nstudent
    income')."""
    return re.sub(r"\s+", " ", text or "").casefold()


def _label(entry: Dict[str, str], key2name: Dict[str, str]) -> Label:
    """Gold entry -> normalised label. article_ref '*' is doc-level ('any chunk
    from this document counts'), used for annex docs whose sections have no
    stable, citable article numbers. '*' plus ``anchor`` narrows that to the
    chunks whose text contains the phrase (whitespace-insensitively) — the chunker-independent way to
    label one policy inside a plan whose detected units differ per detector
    ('Policy H15' sits in a 'Policy SD10 (part 10)' unit under one chunker and
    a '4.15' unit under another)."""
    name = key2name.get(entry["doc_key"], entry["doc_key"])
    if entry.get("anchor"):
        return ("anchor", name, _norm(entry["anchor"]))
    if entry["article_ref"] == "*":
        return ("doc", name)
    return ("article", name, *_path(entry["article_ref"]))


def _hit_matches(hit: Dict[str, Any], label: Label) -> bool:
    """An article label matches a hit whose reference path STARTS WITH the label's
    path, so sub-units ('Art. N > 2') and oversized parts ('Art. N (part k)') count
    as Art. N, and a deeper label ('TÍTULO 7 > 7.3 > 7.3.4') matches only that
    article and its children."""
    if hit.get("doc_name") != label[1]:
        return False
    if label[0] == "doc":
        return True
    if label[0] == "anchor":
        return label[2] in _norm(hit.get("text"))
    want = label[2:]
    return _path(hit.get("article_ref") or "")[: len(want)] == want


def _flags(hits: List[Dict[str, Any]], labels: Iterable[Label]) -> List[bool]:
    """Per-rank relevance. Each label is satisfied at most once — by its first
    retrieved chunk — so recall never exceeds num_relevant."""
    pending: List[Label] = list(dict.fromkeys(labels))
    out: List[bool] = []
    for h in hits:
        match = next((lb for lb in pending if _hit_matches(h, lb)), None)
        if match is not None:
            pending.remove(match)
        out.append(match is not None)
    return out


def _first_rank(hits: List[Dict[str, Any]], label: Label) -> Optional[int]:
    """1-based rank of the first chunk matching ``label``; None if not retrieved."""
    return next((i + 1 for i, h in enumerate(hits) if _hit_matches(h, label)), None)


def _normalise_item(item: Dict[str, Any], key2tier: Dict[str, str]) -> Dict[str, Any]:
    """Accept both the v1 (``lang`` + ``query``) and v2 (``queries`` map) shapes
    and fill the descriptive fields the breakdowns group on."""
    out = dict(item)
    if "queries" not in out:
        out["queries"] = {out.get("lang", "en"): out["query"]}
    out.setdefault("type", "factual")
    if out["type"] not in ITEM_TYPES:
        raise ValueError(f"{out['id']}: unknown type {out['type']!r}; expected one of {ITEM_TYPES}")
    if "tier" not in out:
        out["tier"] = key2tier.get(out["relevant"][0]["doc_key"], "?")
    prec = out.get("precedence")
    if prec and prec.get("rule") not in PRECEDENCE_RULES:
        raise ValueError(f"{out['id']}: unknown precedence rule {prec.get('rule')!r}; expected one of {PRECEDENCE_RULES}")
    return out


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — enough for a relative comparison."""
    return max(0, len(text) // 4)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _aggregate(runs: List[Dict[str, Any]], k_values: List[int]) -> Dict[str, Any]:
    """Mean of every ranking metric over a list of runs."""
    agg: Dict[str, Any] = {
        field: {k: _mean([r[field][k] for r in runs]) for k in k_values}
        for field in ("recall", "hit", "ndcg", "precision")
    }
    agg["mrr"] = _mean([r["rr"] for r in runs])
    agg["n"] = len(runs)
    prec = [r["precedence"] for r in runs if r.get("precedence")]
    if prec:
        agg["precedence"] = {
            "n": len(prec),
            "pair_recall": _mean([float(p["pair_retrieved"]) for p in prec]),
            "winner_first": _mean([float(p["winner_first"]) for p in prec]),
        }
    return agg


def _breakdown(runs: List[Dict[str, Any]], field: str, k_values: List[int]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in runs:
        groups[str(r[field])].append(r)
    return {g: _aggregate(rs, k_values) for g, rs in sorted(groups.items())}


def _metric_table(agg: Dict[str, Any], k_values: List[int]) -> List[str]:
    ks = " | ".join(f"@{k}" for k in k_values)
    rows = ["| metric | " + ks + " |", "|" + "---|" * (len(k_values) + 1)]
    for name, field in (("recall", "recall"), ("hit@k", "hit"), ("nDCG", "ndcg"), ("precision*", "precision")):
        rows.append("| " + name + " | " + " | ".join(f"{agg[field][k]:.3f}" for k in k_values) + " |")
    return rows


def _breakdown_table(title: str, groups: Dict[str, Dict[str, Any]], k_values: List[int]) -> List[str]:
    """One row per group: n · recall@k for each k · MRR."""
    ks = " | ".join(f"recall@{k}" for k in k_values)
    rows = [f"### by {title}", "", f"| {title} | n | {ks} | MRR |", "|" + "---|" * (len(k_values) + 3)]
    for g, agg in groups.items():
        rec = " | ".join(f"{agg['recall'][k]:.3f}" for k in k_values)
        rows.append(f"| {g} | {agg['n']} | {rec} | {agg['mrr']:.3f} |")
    rows.append("")
    return rows


def _summary_md(result: Dict[str, Any], k_values: List[int]) -> str:
    """Compact, human-readable summary (good for the write-up / slides)."""
    meta, agg = result["meta"], result["aggregate"]
    lines = [
        "# Retrieval evaluation — normative RAG",
        "",
        f"**Scope:** {meta['country']} / {meta['use_case']} · embed `{meta['embed_model']}` · "
        f"{meta['n_items']} gold items / {meta['n_runs']} runs · {meta['n_chunks_in_scope']} chunks in scope"
        + (" · superseded documents INCLUDED" if meta.get("include_superseded") else ""),
        "",
        "## Overall retrieval quality (mean over runs)",
        "",
        *_metric_table(agg, k_values),
        "",
        f"**MRR = {agg['mrr']:.3f}**",
        "",
        "\\* precision is capped at 1/k for single-relevant items — reported, not headline.",
        "",
    ]
    for field in ("lang", "type", "tier"):
        groups = result["breakdown"][field]
        if len(groups) > 1:
            lines += _breakdown_table(field, groups, k_values)
    if "precedence" in agg:
        p = agg["precedence"]
        lines += [
            "## Precedence (conflict / temporal items)",
            "",
            f"| runs | pair_recall | winner_first |",
            "|---|---|---|",
            f"| {p['n']} | {p['pair_recall']:.3f} | {p['winner_first']:.3f} |",
            "",
            "pair_recall = both rules surfaced within depth; winner_first = the prevailing rule "
            "was retrieved and ranks above the overridden one.",
            "",
        ]
    lines += [
        "## Efficiency (context sent to the LLM)",
        "",
        "| mode | ~tokens/query |",
        "|---|---|",
        f"| dump (all {meta['n_chunks_in_scope']} in-scope chunks) | {agg['dump_tokens']:,} |",
        f"| rag (top-{config.DEFAULT_TOP_K}) | {agg['rag_avg_tokens']:,.0f} |",
        "",
        f"**{agg['reduction_pct']:.1f}% smaller context with RAG.**",
        "",
    ]
    return "\n".join(lines)


def _score_run(item: Dict[str, Any], lang: str, query: str, hits: List[Dict[str, Any]],
               labels: List[Label], k_values: List[int], key2name: Dict[str, str]) -> Dict[str, Any]:
    flags = _flags(hits, labels)
    num_rel = len(set(labels))
    rag_ctx, _ = _format(hits[: config.DEFAULT_TOP_K])
    first_rank = next((i + 1 for i, f in enumerate(flags) if f), None)
    top1 = f"{hits[0].get('doc_name', '?')[:22]} {hits[0].get('article_ref', '')}" if hits else "-"

    run: Dict[str, Any] = {
        "id": item["id"], "lang": lang, "type": item["type"], "tier": item["tier"],
        "query": query, "num_relevant": num_rel,
        "first_hit_rank": first_rank, "top1": top1,
        "rr": metrics.reciprocal_rank(flags),
        "recall": {k: metrics.recall_at_k(flags, num_rel, k) for k in k_values},
        "hit": {k: metrics.hit_at_k(flags, k) for k in k_values},
        "ndcg": {k: metrics.ndcg_at_k(flags, num_rel, k) for k in k_values},
        "precision": {k: metrics.precision_at_k(flags, k) for k in k_values},
        "rag_tokens": _approx_tokens(rag_ctx),
    }
    prec = item.get("precedence")
    if prec:
        w = _first_rank(hits, _label(prec["winner"], key2name))
        l = _first_rank(hits, _label(prec["loser"], key2name))
        run["precedence"] = {
            "rule": prec["rule"], "winner_rank": w, "loser_rank": l,
            "pair_retrieved": w is not None and l is not None,
            "winner_first": w is not None and (l is None or w < l),
        }
    return run


def run(k_values: List[int], json_out: str | None, gold_path: Path = GOLD_PATH,
        include_superseded: bool = False) -> int:
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    meta = gold["meta"]
    country = meta.get("country", config.DEFAULT_COUNTRY)
    use_case = meta.get("use_case", config.DEFAULT_USE_CASE)
    key2name = _doc_key_to_name()
    key2tier = _doc_key_to_tier()
    items = [_normalise_item(q, key2tier) for q in gold["queries"]]
    depth = max(k_values + [config.DEFAULT_TOP_K])
    n_runs = sum(len(it["queries"]) for it in items)

    print(f"Corpus scope : country={country} use_case={use_case}")
    print(f"Embed model  : {config.EMBED_MODEL}   |   retrieval depth={depth}"
          f"   |   superseded docs {'INCLUDED' if include_superseded else 'excluded'}")
    print(f"Gold items   : {len(items)}  ->  {n_runs} runs   ({Path(gold_path).name})\n")

    # dump baseline is query-independent — compute its cost once
    dump_hits = search("", country=country, use_case=use_case, mode="dump",
                       include_superseded=include_superseded)
    if not dump_hits:
        print("!! Store is empty (or embedding backend unreachable).")
        print("   Run:  ollama pull bge-m3  &&  python -m app.services.llm.rag.ingest --city turin --rebuild")
        return 1
    dump_ctx, _ = _format(dump_hits)
    dump_tokens = _approx_tokens(dump_ctx)

    runs: List[Dict[str, Any]] = []
    for item in items:
        labels = [_label(r, key2name) for r in item["relevant"]]
        for lang, query in item["queries"].items():
            hits = search(query, country=country, use_case=use_case, top_k=depth, mode="rag",
                          include_superseded=include_superseded)
            runs.append(_score_run(item, lang, query, hits, labels, k_values, key2name))

    # ---- per-run detail (doubles as the qualitative "what RAG selected" view) ----
    print("Per-run (rank of first correct article, top-1 retrieved):")
    print(f"  {'id':6} {'lang':4} {'type':13} {'tier':9} {'rank':>4}  {'RR':>4}  top-1 retrieved")
    for r in runs:
        rank = r["first_hit_rank"] if r["first_hit_rank"] else "miss"
        prec = ""
        if r.get("precedence"):
            p = r["precedence"]
            prec = f"   [prec w={p['winner_rank'] or '-'} l={p['loser_rank'] or '-'} {'ok' if p['winner_first'] else 'FAIL'}]"
        print(f"  {r['id']:6} {r['lang']:4} {r['type']:13} {r['tier']:9} {str(rank):>4}  {r['rr']:.2f}  {r['top1']}{prec}")

    # ---- aggregate table ----
    aggregate = _aggregate(runs, k_values)
    breakdown = {f: _breakdown(runs, f, k_values) for f in ("lang", "type", "tier")}

    print("\nAggregate (mean over runs):")
    print("  metric      " + "".join(f"@{k:<6}" for k in k_values))
    for name, field in [("recall", "recall"), ("hit", "hit"), ("nDCG", "ndcg"), ("precision*", "precision")]:
        print(f"  {name:<11} " + "".join(f"{aggregate[field][k]:<7.3f}" for k in k_values))
    print(f"\n  MRR        {aggregate['mrr']:.3f}")
    print("  * precision is capped at 1/k for single-relevant items — reported, not headline.")

    for field in ("lang", "type", "tier"):
        if len(breakdown[field]) > 1:
            print(f"\nBy {field}:")
            for g, a in breakdown[field].items():
                rec = "".join(f"{a['recall'][k]:<7.3f}" for k in k_values)
                print(f"  {g:<14} n={a['n']:<3} recall {rec} MRR {a['mrr']:.3f}")
    if "precedence" in aggregate:
        p = aggregate["precedence"]
        print(f"\nPrecedence ({p['n']} runs): pair_recall {p['pair_recall']:.3f}   winner_first {p['winner_first']:.3f}")

    # ---- efficiency: dump vs rag context cost ----
    rag_avg = _mean([float(r["rag_tokens"]) for r in runs])
    reduction = (1 - rag_avg / dump_tokens) * 100 if dump_tokens else 0.0
    print("\nContext cost (approx tokens sent to the LLM):")
    print(f"  dump (all in-scope) : {len(dump_hits)} chunks, ~{dump_tokens} tokens")
    print(f"  rag  (top-{config.DEFAULT_TOP_K})        : ~{rag_avg:.0f} tokens/query  ->  {reduction:.1f}% smaller")
    aggregate.update({"dump_tokens": dump_tokens, "rag_avg_tokens": rag_avg, "reduction_pct": reduction})

    result = {
        "meta": {"country": country, "use_case": use_case, "embed_model": config.EMBED_MODEL,
                 "k_values": k_values, "n_items": len(items), "n_runs": len(runs),
                 "n_chunks_in_scope": len(dump_hits), "gold": Path(gold_path).name,
                 "include_superseded": include_superseded},
        "aggregate": aggregate,
        "breakdown": breakdown,
        "per_run": runs,
    }
    if json_out:
        out = Path(json_out)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = out.with_name(out.stem + "_summary.md")
        summary.write_text(_summary_md(result, k_values), encoding="utf-8")
        print(f"\nWrote {out}\nWrote {summary}")

    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evaluate normative retrieval against a gold set.")
    p.add_argument("--k", default="1,3,5,8", help="comma-separated k values (default 1,3,5,8)")
    p.add_argument("--json", default=None, help="also write full results to this JSON path")
    p.add_argument("--gold", default=str(GOLD_PATH),
                   help="gold-set JSON to evaluate against (default: the Italian set)")
    p.add_argument("--include-superseded", action="store_true",
                   help="let repealed documents compete (the production cascade excludes them); "
                        "compare precedence numbers with and without to measure the temporal filter")
    args = p.parse_args(argv)
    k_values = sorted({int(x) for x in args.k.split(",") if x.strip()})
    return run(k_values, args.json, Path(args.gold), include_superseded=args.include_superseded)


if __name__ == "__main__":
    sys.exit(main())
