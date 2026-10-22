"""
Citation following — one hop from the retrieved chunks to the articles they cite.

A rule often states no figure itself and points elsewhere ("las residencias
comunitarias cumplirán las condiciones ... de los artículos 7.3.3, 7.3.4 y
7.3.5"): the semantic top-k finds the pointer but not the article holding the
numbers, and QUANT_ONLY drops the pointer when no retrieved chunk contains it.
``follow`` reads the citations in the retrieved text and adds the cited chunks.

Document-agnostic: a citation is a citation word in Italian, Spanish or English
(article, policy, class, section, table) followed by one identifier or a list
("artículos 7.3.3, 7.3.4 y 7.3.5"), resolved against the store's own article
labels. A multi-level article number (7.3.4) is unique enough to be followed
into another document of the same country (a FAQ citing the planning norms);
anything else ("Art. 3", "Policy H16", "Table 3.1") is resolved in the citing
document only. A citation to a paragraph (7.3.11.2, 7.3.4.2.c) selects that
paragraph when the store has it, otherwise the whole article.

Cited chunks are kept within the jurisdiction scope but not narrowed to
quantitative chunks — the pointer is exactly the chunk without numbers. They
are ranked by specificity (paragraph before article), by the rank of the
citing chunk, then by similarity to the query, and capped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.llm.rag import store

_FAMILIES = (
    (r"art(?:s|t)?\.?|art[íi]culos?|articol[oi]|articles?", "art"),
    (r"polic(?:y|ies)", "policy"),
    (r"class(?:es)?", "class"),
    (r"secci[óo]n(?:es)?|sections?|sezion[ei]", "section"),
    (r"tablas?|tabell[ae]|tables?", "table"),
)
_WORDS = "|".join(p for p, _ in _FAMILIES)
_ID = r"[A-Z]{0,3}\d+[A-Za-z]?(?:[.\-](?:\d+|[a-z]\b|bis|ter|quater))*"
_CITE = re.compile(rf"(?i)\b({_WORDS})\s+({_ID}(?:\s*(?:,|\by\b|\be\b|\band\b|\bor\b|\bo\b|\bed\b|&)\s*{_ID})*)")
_SEG = re.compile(rf"(?i)^({_WORDS})\s+(.+)$")
_PART = re.compile(r"\s*\(part \d+\)$")

Citation = Tuple[str, str]            # (family, identifier) e.g. ("art", "7.3.4")
Ref = Tuple[str, str]                 # (doc_name, article_ref)


def _family(word: str) -> str:
    return next(f for p, f in _FAMILIES if re.fullmatch(p, word, re.IGNORECASE))


def _segments(article_ref: str) -> List[str]:
    return [_PART.sub("", s.strip()) for s in article_ref.split(" > ")]


def _named(segment: str) -> Optional[Citation]:
    m = _SEG.match(segment)
    return (_family(m.group(1)), m.group(2).lower()) if m else None


def _dotted(ident: str) -> bool:
    parts = ident.split(".")
    return len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit()


def citations(text: str) -> List[Citation]:
    """Every (family, identifier) cited in ``text``, in order, without repeats."""
    out: List[Citation] = []
    for m in _CITE.finditer(text or ""):
        fam = _family(m.group(1))
        for ident in re.findall(_ID, m.group(2)):
            c = (fam, ident.lower())
            if c not in out:
                out.append(c)
    return out


# ---- the store's article labels, per country (rebuilt when the store changes)
_index: Dict[str, Any] = {"count": None, "refs": {}}


def _refs(country: str) -> List[Tuple[str, str, List[str]]]:
    col = store.get_collection()
    n = col.count()
    if _index["count"] != n:
        _index["count"], _index["refs"] = n, {}
    if country not in _index["refs"]:
        r = col.get(where={"country": country}, include=["metadatas"])
        _index["refs"][country] = sorted({(m["doc_name"], m.get("article_ref") or "") for m in r["metadatas"]})
        _index["refs"][country] = [(d, a, _segments(a)) for d, a in _index["refs"][country]]
    return _index["refs"][country]


def resolve(cite: Citation, citing: Dict[str, Any], country: str,
            context_docs: List[str]) -> Tuple[bool, List[Ref]]:
    """(specific, chunks) cited by ``cite`` in the chunk ``citing``."""
    fam, ident = cite
    refs = _refs(country)
    if fam == "art" and _dotted(ident):
        comps = ident.split(".")
        # the citing document first, then the documents already in context, then any
        for docs in ([citing["doc_name"]], context_docs, None):
            for k in range(len(comps), 1, -1):
                art, rest = ".".join(comps[:k]), comps[k:]
                under = [(d, a, s) for d, a, s in refs if art in s and (docs is None or d in docs)]
                if not under:
                    continue
                if rest:
                    child = [(d, a) for d, a, s in under
                             if s.index(art) + 1 < len(s) and s[s.index(art) + 1].lower() == rest[0]]
                    if child:
                        return True, child
                return False, [(d, a) for d, a, _ in under]
        return False, []
    own = [_named(s) for s in _segments(citing.get("article_ref") or "")]
    if cite in own:                                   # an article citing itself
        return False, []
    return False, [(d, a) for d, a, s in refs if d == citing["doc_name"] and cite in (_named(x) for x in s)]


def follow(hits: List[Dict[str, Any]], *, query_embedding: List[float], where: Dict[str, Any],
           country: str, limit: int) -> List[Dict[str, Any]]:
    """Up to ``limit`` chunks cited by ``hits`` and not among them, in scope
    (``where``), each carrying ``cited_by`` = the citing chunk."""
    if limit <= 0 or not hits:
        return []
    have = {(h.get("doc_name"), h.get("article_ref")) for h in hits}
    context_docs = list(dict.fromkeys(h.get("doc_name") for h in hits))
    best: Dict[Ref, Tuple[int, int, str]] = {}           # ref -> (not specific, citing rank, cited by)
    for rank, h in enumerate(hits):
        for c in citations(h.get("text") or ""):
            specific, refs = resolve(c, h, country, context_docs)
            for ref in refs:
                key = (0 if specific else 1, rank, f"{h.get('doc_name')} {h.get('article_ref')}")
                if ref not in have and (ref not in best or key[:2] < best[ref][:2]):
                    best[ref] = key
    if not best:
        return []
    scope = {"$and": [*where["$and"], {"doc_name": {"$in": sorted({d for d, _ in best})}},
                      {"article_ref": {"$in": sorted({a for _, a in best})}}]}
    res = store.get_collection().query(query_embeddings=[query_embedding], n_results=min(len(best), 100),
                                       where=scope)
    found = []
    for doc, md, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        key = best.get((md.get("doc_name"), md.get("article_ref")))
        if key:
            found.append((key[0], key[1], dist, {**md, "text": doc, "distance": dist, "cited_by": key[2]}))
    found.sort(key=lambda x: x[:3])
    return [h for *_, h in found[:limit]]
