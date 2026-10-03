"""
Gold-set validator — every label must name something that exists.

    python -m app.services.llm.rag.eval.validate [gold.json ...]

A gold label that matches no chunk in the store scores 0 for every query that
carries it, which looks exactly like a retrieval failure. This separates the two:
it reports labels that cannot be satisfied at all (a typo in an article_ref, an
anchor phrase that is not in the document, a document not ingested) so a "miss"
in the evaluation can be read as what it is — the retriever not finding text that
is demonstrably there.

A label may legitimately be unmatched when the chunk detector failed to produce
the unit the law actually has (London uk04 'Class L' under the gemma-4 detector);
those are listed too, since the distinction is a finding about the detector, not
about the gold set.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from app.services.llm.rag import store
from app.services.llm.rag.eval.run import _doc_key_to_name, _hit_matches, _label

DEFAULT_GOLDS = sorted(Path(__file__).resolve().parent.glob("gold_queries_*.json"))


def _entries(item: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every label an item carries: its relevant set plus both sides of a precedence pair."""
    out = list(item["relevant"])
    prec = item.get("precedence")
    if prec:
        out += [prec["winner"], prec["loser"]]
    return out


def validate(paths: List[Path]) -> int:
    col = store.get_collection()
    if col.count() == 0:
        print("!! Store is empty — ingest first.")
        return 1
    key2name = _doc_key_to_name()
    cache: Dict[str, List[Dict[str, Any]]] = {}

    def chunks(doc_name: str) -> List[Dict[str, Any]]:
        if doc_name not in cache:
            res = col.get(where={"doc_name": doc_name}, include=["metadatas", "documents"])
            cache[doc_name] = [{**m, "text": t} for m, t in zip(res["metadatas"], res["documents"])]
        return cache[doc_name]

    unmatched = 0
    for path in paths:
        gold = json.loads(path.read_text(encoding="utf-8"))
        items = gold["queries"]
        print(f"\n{path.name}: {len(items)} items, "
              f"{sum(len(q.get('queries', {'': ''})) for q in items)} runs")
        for item in items:
            for entry in _entries(item):
                doc_name = key2name.get(entry["doc_key"], entry["doc_key"])
                label = _label(entry, key2name)
                if not any(_hit_matches(h, label) for h in chunks(doc_name)):
                    where = entry.get("anchor") or entry["article_ref"]
                    print(f"  !! {item['id']}: {entry['doc_key']} {where!r} matches no chunk")
                    unmatched += 1
    print(f"\n{unmatched} unmatched label(s)")
    return 1 if unmatched else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check that every gold label matches a stored chunk.")
    p.add_argument("gold", nargs="*", default=[str(p_) for p_ in DEFAULT_GOLDS],
                   help="gold-set JSON files (default: every gold_queries_*.json next to this file)")
    args = p.parse_args(argv)
    return validate([Path(g) for g in args.gold])


if __name__ == "__main__":
    sys.exit(main())
