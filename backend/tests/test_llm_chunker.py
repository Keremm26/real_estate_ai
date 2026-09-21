"""Hierarchical chunking mechanics, with the detector stubbed (no LLM, no cache).

    python -m tests.test_llm_chunker      (from backend/)
"""
from __future__ import annotations

import re

from app.services.llm.rag import config
from app.services.llm.rag import llm_chunker as lc
from app.services.llm.rag.chunker import REF_SEP, top_level_ref

# One "decree": Art. 1 is short (stays whole); Art. 2 is long with commi, and
# comma 2 is itself long with lettere; Art. 3 is long prose with no structure.
_FILLER = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 6   # ~340 chars


def _doc() -> str:
    lettere = "\n".join(f"{l}) Lettera {l} text. {_FILLER}" for l in "abcd")
    art2 = f"Art. 2\nDefinizioni\nIntro sentence of the article before any comma.\n1. Comma 1 text. {_FILLER}\n2. Comma 2 text, long one:\n{lettere}\n" + \
           "\n".join(f"{n}. Comma {n} text. {_FILLER}" for n in (3, 4))
    art3 = "Art. 3\nProse\n" + (_FILLER + "\n") * 8
    return "TITOLO PRIMO\nIndex line\n" + "Art. 1\nOggetto\nShort article, 2 commi but tiny.\n1. a\n2. b\n" + art2 + "\n" + art3


_LEVELS = [
    {"marker_regex": r"^(Art\.\s*\d+)\s*$", "unit_name": "articolo"},
    {"marker_regex": r"^(\d+)\.\s", "unit_name": "comma"},
    {"marker_regex": r"^([a-z]\))\s", "unit_name": "lettera"},
]


def _stub(specs_for_depth):
    """A detect_structure stand-in: records (depth, parent_ref) and answers
    from ``specs_for_depth(depth, call_index)``."""
    calls = []

    def fake(sample, *, parent=None, parent_ref="", feedback=""):
        depth = 0 if parent is None else next(i for i, lv in enumerate(_LEVELS)
                                              if lv["marker_regex"] == parent["marker_regex"]) + 1
        calls.append((depth, parent_ref))
        spec = specs_for_depth(depth, len(calls) - 1)
        return dict(spec) if spec else None

    return fake, calls


def _run(target: int, max_depth: int = 3):
    fake, calls = _stub(lambda depth, _: _LEVELS[depth] if depth < len(_LEVELS) else None)
    orig = (lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH)
    lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH = fake, target, max_depth
    try:
        return lc.chunk_document_llm(_doc()), calls
    finally:
        lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH = orig


def test_disabled_reproduces_single_level():
    chunks, calls = _run(target=0)
    assert [c.article_ref for c in chunks] == ["Art. 1", "Art. 2", "Art. 3"]
    assert calls == [(0, "")]                      # only the root detection
    assert not any(c.text.startswith("[") for c in chunks)


def test_descends_only_into_oversized_units():
    chunks, calls = _run(target=600)
    refs = [c.article_ref for c in chunks]
    assert refs[0] == "Art. 1"                                     # short: untouched
    assert "Art. 2" not in refs                                    # short preamble -> context only, not a chunk
    assert f"Art. 2{REF_SEP}1" in refs
    assert f"Art. 2{REF_SEP}2{REF_SEP}a)" in refs                  # comma 2 was long -> lettere
    assert f"Art. 2{REF_SEP}3" in refs and f"Art. 2{REF_SEP}4" in refs
    assert refs[-1] == "Art. 3"                                    # no structure: kept whole
    # lazy detection: one call per level, each sampled from units the previous level produced
    assert [d for d, _ in calls] == [0, 1, 2]
    assert calls[1][1] in ("Art. 2", "Art. 3") and calls[2][1] == f"Art. 2{REF_SEP}2"


def test_context_line_and_lossless():
    chunks, _ = _run(target=600)
    by_ref = {c.article_ref: c for c in chunks}
    intro = "Art. 2 Definizioni Intro sentence of the article before any comma."
    a = by_ref[f"Art. 2{REF_SEP}2{REF_SEP}a)"]
    assert a.text.splitlines()[0] == f"[{intro} › 2. Comma 2 text, long one:]"
    assert a.text.splitlines()[1].startswith("a) Lettera a text.")
    assert by_ref[f"Art. 2{REF_SEP}1"].text.splitlines()[0] == f"[{intro}]"
    # every line of Art. 2 survives: sub-unit bodies as chunks, the short
    # preamble inside the context line
    body_lines = {ln.strip() for ln in _doc().split("Art. 2\n", 1)[1].split("Art. 3\n")[0].splitlines() if ln.strip()}
    kept_text = "\n".join(c.text for c in chunks if c.article_ref.startswith("Art. 2"))
    assert all(ln in kept_text for ln in body_lines)


def test_successor_shapes():
    ok = lc._is_successor
    k = lambda r: lc._seq_key(r, roman=False)
    assert ok(k("1"), k("2")) and ok(k("2"), k("4")) and not ok(k("3"), k("8"))   # gap <= 2
    assert ok(k("1"), k("1-bis")) and ok(k("1-bis"), k("1-ter")) and ok(k("1-ter"), k("2"))
    assert ok(k("7.1"), k("7.2")) and ok(k("8.1.9"), k("8.2.1")) and not ok(k("8.1.9"), k("8.4.1"))
    assert ok(k("a)"), k("b)")) and ok(k("A"), k("B")) and not ok(k("b)"), k("a)"))
    assert not ok(k("3"), k("3"))                                                   # duplicate is not a boundary
    r = lambda s: lc._seq_key(s, roman=True)
    assert ok(r("iii"), r("iv")) and ok(r("i"), r("ii"))


def _runs(pattern, raw):
    ms = list(re.compile(pattern, re.M).finditer(raw))
    return [lc._mref(m) for m in lc._local_runs(ms, len(raw))]


def test_local_runs():
    # NUEA Art. 1: real commi 1,2,3,(3 repeated),4 among page numbers, a row, a date
    nuea = "\n".join(["1 Le finalità", "2 L'uso", "3 Hanno carattere", "8 Art. 1 (running header)",
                      "3 del D.Lgs.", "27 fogli, a colori;", "9082018 [*] Nota variante", "3 Hanno (dup)",
                      "27 fogli", "9 Art. 1 (running header)", "4 Nelle rappresentazioni", "10 Art. 2"])
    assert _runs(r"^(\d+)\s", nuea) == ["1", "2", "3", "4"]
    assert _runs(r"^(\d+)\s", "27 fogli\n9 page\n" + nuea) == ["1", "2", "3", "4"]   # noise before the run
    assert _runs(r"^(\d+)\s", "27 fogli\n9 page\n2024 anno") == []                    # no run: level n/a
    assert _runs(r"^(\d+)\s", "90 page\n91 page\n92 page") == []                      # pages do not start a run
    assert _runs(r"^(\d+\.\d+)\s", "7.1 a\n7.2 b\n7.3 c") == ["7.1", "7.2", "7.3"]
    # HMO regs: '(1)' shares the heading line so the run starts at (2); the main
    # run wins over the nested '(2)..(5)' and '(1) (2)' restarts inside it
    hmo = "\n".join(f"({n}) t" for n in [2, 3, 4, 5, 6, 7, 8, 9, 10, 2, 3, 4, 5, 1, 2])
    assert _runs(r"^\((\d+)\)", hmo) == [str(n) for n in range(2, 11)]
    # PGOUM título: its own index (CAPÍTULO 1.1-1.4, short lines) precedes the
    # bodies (same refs, long) — both runs kept, so merge-by-ref joins them
    pg = "\n".join([f"CAPÍTULO 1.{i}. T{i}" for i in (1, 2, 3, 4)] +
                   [f"CAPÍTULO 1.{i}. T{i}\n" + _FILLER for i in (1, 2, 3, 4)])
    assert _runs(r"^(CAPÍTULO\s+\d+\.\d+)\.", pg) == ["CAPÍTULO 1.1", "CAPÍTULO 1.2", "CAPÍTULO 1.3", "CAPÍTULO 1.4"] * 2
    # GPDO Class G: an outer (a)(b)(c)(d) list with nested (i)(ii)(iv)(v) items, plus a stray (e)
    g = "\n".join(f"({t}) text" for t in ["e", "a", "b", "c", "i", "ii", "d", "i", "ii", "iv", "v"])
    assert _runs(r"^\(?([a-z]{1,2})\)", g) == ["a", "b", "c", "d"]
    rom = "\n".join(f"({t}) text" for t in ["i", "ii", "iii", "iv", "v", "vi"])
    assert _runs(r"^\(([a-z]+)\)", rom) == ["i", "ii", "iii", "iv", "v", "vi"]
    # a regex with one group per alternative: the participating group is the ref
    ms = list(re.compile(r"^\[F\d+\((\d+)\)|^\((\d+)\)", re.M).finditer("(1) a\n[F2(2) b\n(3) c"))
    assert [lc._mref(m) for m in ms] == ["1", "2", "3"]
    # nested capturing groups are ambiguous and rejected; alternation and (?:) are not nesting
    assert lc._capturing_depth(r"^(Art\.\s*(\d+))\s*$") == 2
    assert lc._capturing_depth(r"^\[F\d+\((\d+)\)|^\((\d+)\)") == 1
    assert lc._capturing_depth(r"^(Art\.\s*\d+(?:-bis|-ter)?)(?=\s|\(|-|$)") == 1
    assert lc._capturing_depth(r"^([a-z(])\)") == 1                                  # '(' inside a class


def test_index_fitted_regex_is_retried_from_the_body():
    """A root regex that matches only the index leaves every body in one piece;
    the retry is sampled from that piece and the better answer wins."""
    body = "\n".join(f"Art. {n}. Titolo {n}\n{_FILLER}" for n in (1, 2, 3, 4))
    doc = "INDICE\nArt. 1\nArt. 2\nArt. 3\nArt. 4\n" + body
    answers = [{"marker_regex": r"^(Art\.\s*\d+)\s*$", "unit_name": "articolo"},     # index lines only
               {"marker_regex": r"^(Art\.\s*\d+)\.?", "unit_name": "articolo"}]     # index + body
    samples = []

    def fake(sample, *, parent=None, parent_ref="", feedback=""):
        samples.append((sample, feedback))
        return dict(answers[len(samples) - 1])

    orig = (lc.detect_structure, config.CHUNK_TARGET_CHARS)
    lc.detect_structure, config.CHUNK_TARGET_CHARS = fake, 0
    try:
        chunks = lc.chunk_document_llm(doc)
    finally:
        lc.detect_structure, config.CHUNK_TARGET_CHARS = orig
    assert len(samples) == 2 and samples[0][1] == "" and "one piece" in samples[1][1]
    assert samples[1][0].startswith("Art. 4")                      # retry sampled from the swallowing piece
    assert [c.article_ref for c in chunks] == ["Art. 1", "Art. 2", "Art. 3", "Art. 4"]
    assert all(len(c.text) > 300 for c in chunks)                 # index line merged with its body


def test_kept_preamble_is_not_split_into_colliding_refs():
    """A long intro before the first sub-unit is kept under the parent's own
    ref; the next level must not carve it into 'Art. 1 > 1' twins of the real
    sub-units (Chroma rejected exactly such duplicate ids)."""
    intro = "Intro paragraph. " * 40                                   # > 160 chars: kept as a chunk
    art = "Art. 1\n" + intro + "\n" + "\n".join(f"{n}. Comma {n}. {_FILLER}" for n in (1, 2, 3))
    doc = "Art. 1\nArt. 2\n" + art + "\nArt. 2\nShort.\n1. x\n2. y"
    fake, _ = _stub(lambda depth, _: _LEVELS[depth] if depth < len(_LEVELS) else None)
    orig = (lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH)
    lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH = fake, 300, 3
    try:
        chunks = lc.chunk_document_llm(doc)
    finally:
        lc.detect_structure, config.CHUNK_TARGET_CHARS, config.CHUNK_MAX_DEPTH = orig
    refs = [c.article_ref for c in chunks]
    assert refs.count("Art. 1") == 1 and f"Art. 1{REF_SEP}1" in refs
    assert len(refs) == len(set(refs)), refs                           # ids stay unique


def test_top_level_ref():
    assert top_level_ref("Art. 7 > 7.1 (part 2)") == "Art. 7"
    assert top_level_ref("Policy H15 (part 1)") == "Policy H15"
    assert top_level_ref("Class MA") == "Class MA"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
