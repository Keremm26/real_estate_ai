"""
LLM-assisted structure detection for chunking (optional strategy).

Motivation: a per-country regex zoo (IT ``Art.``, ES ``Artículo`` + CTE ``DB-SI``
sections, EN ``Section``/``Regulation``/``Policy H15``/Approved-Document numbering,
...) does not scale to many jurisdictions, and the deterministic fallback can
silently corrupt (duplicate section refs -> chunk_id collisions).

Approach: a model inspects only *samples* of the text and returns a **boundary
regex** — where each self-contained unit (article/section) begins. We then slice
the full text deterministically at those offsets. The model never returns the
legal text, so nothing is paraphrased, dropped, or invented; the segmentation
stays lossless and citable. Detected regexes are cached per document, so the
model runs once and re-ingestion is reproducible.

Hierarchy: the top-level unit is the right granularity for a decree article
(~1k chars) but not for a plan policy or a building-code section (10-300k). So a
unit still longer than ``config.CHUNK_TARGET_CHARS`` is split again at the
text's NEXT structural level (comma/lettera, policy clause, capítulo), recursively.
Every level is handled by the same procedure — the document is simply the root
unit — with two principled differences below the root: sub-unit numbering is
LOCAL (it restarts inside every parent), so sub-unit boundaries must form a
numbering run; and a unit's preamble is normative text (its heading and intro),
not front matter. Sub-unit refs are joined with ``REF_SEP`` ("Art. 7 > 7.1") and
carry their ancestors' headings as a leading context line.

Every detected regex is validated on the real text before it is trusted: the
split it produces is assessed (enough pieces, none swallowing the unit, no run of
bare headers), and a bad answer gets one retry with that feedback and a sample
drawn from where the missed structure lives. If nothing usable comes back the
level is recorded as absent (cached too), and the blind size cap remains the
last resort. If even the top level fails, the deterministic chunker takes over.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from app.services.llm.rag import config
from app.services.llm.rag.chunker import (
    REF_SEP,
    ArticleChunk,
    _normalise_ref,
    _QUANT_RE,
    chunk_document,
)
from app.services.llm.rag.fetch import _slug

# ==========================================================================
# Prompts
# ==========================================================================

# JSON schema the model must fill (Ollama structured output). ``marker_regex``
# may be null below the root: "this unit has no consistent finer structure".
_SCHEMA = {
    "type": "object",
    "properties": {
        "marker_regex": {"type": ["string", "null"]},
        "example_headers": {"type": "array", "items": {"type": "string"}},
        "unit_name": {"type": ["string", "null"]},
    },
    "required": ["marker_regex", "example_headers"],
}

_ANSWER_FORMAT = """Return ONLY a JSON object with:
- "marker_regex": a Python `re` regex (used with re.MULTILINE) that matches the START LINE of each {what}, with exactly ONE capturing group around its short identifier (e.g. {ids}). Anchor to line start with ^. Match only real headers — NOT every numbered line, list item, table row, page number, or cross-reference.
- "example_headers": 2-4 exact header strings copied from the text that your regex must match.
- "unit_name": what the {what}s are called (e.g. {names}).

Guidance:
- The excerpts are separated by "[...]". The regex must match headers as they literally appear at the start of a line, in the BODY of the text; an index / table of contents at the beginning has lines that look like headers but are not — matching those too is fine, matching only those is wrong.
- Generalise the identifier: use \\\\d+, [A-Z], [a-z], not the specific values you saw ([A-D], (1|2|3)) — the excerpts show only a few of them.
- Keep the regex simple and linear (avoid nested quantifiers) to stay fast.
- Escape backslashes correctly for JSON (e.g. \\\\d for a digit)."""

_SYSTEM_ROOT = """You analyse excerpts of a legal/regulatory document and work out how it is divided into its smallest self-contained normative units (articles, sections, regulations, annex clauses). Prefer the coarsest real unit (a whole article/section), not sub-paragraphs.

""" + _ANSWER_FORMAT.format(what="unit", ids='"Art. 5", "Artículo 12", "Section 3", "6.1.2"',
                            names='"articolo", "artículo", "section", "regulation"')

# Filled per call with the parent level's name, an example ref and its regex.
_SYSTEM_SUB = """You are given excerpts of one or more units of the same level ({unit_name}, e.g. "{example}") from a legal/regulatory document. Work out how such a unit is divided into its IMMEDIATE sub-units — the level directly below it (e.g. numbered paragraphs/commi "1.", "2."; lettered points "a)", "b)"; lettered clauses "A", "B"; numbered subsections "7.1.", "7.2."; chapters inside a title). Choose the convention SHARED by the units shown, not one unit's peculiarity. When several nested levels are visible (e.g. capítulo > artículo > apartado inside a título), return the OUTERMOST — never a deeper one. The level ABOVE is matched by {parent_regex}; do not return that.

If the unit has NO consistent sub-unit structure (continuous prose, a table), return {{"marker_regex": null, "example_headers": [], "unit_name": null}}.

""" + _ANSWER_FORMAT.format(what="sub-unit", ids='"1", "a)", "7.1", "B"',
                            names='"comma", "lettera", "clause", "subsection", "apartado"'
                            ).replace("{", "{{").replace("}", "}}")


# ==========================================================================
# Model I/O
# ==========================================================================

class DetectError(RuntimeError):
    """The detector could not be reached — unlike "no structure", not cacheable."""


def _loads_lenient(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _openai_key() -> Optional[str]:
    """API key from the environment, falling back to the app settings (.env)."""
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    try:
        from app.core.config import settings

        return getattr(settings, "OPENAI_API_KEY", None)
    except Exception:
        return None


def _ask(system: str, user: str) -> str:
    """One chat completion, JSON answer. ``openai`` deliberately uses the real
    OpenAI base, not the institutional vLLM base ``OPENAI_API_BASE`` points at
    for gemma; ``ollama`` uses structured output via the JSON-schema ``format``."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        if (config.CHUNK_BACKEND or "openai").lower() == "openai":
            key = _openai_key()
            if not key:
                raise DetectError("OPENAI_API_KEY not set")
            r = httpx.post(f"{config.CHUNK_OPENAI_BASE.rstrip('/')}/chat/completions",
                           json={"model": config.CHUNK_MODEL, "messages": messages,
                                 "response_format": {"type": "json_object"}},
                           headers={"Authorization": f"Bearer {key}"},
                           timeout=config.CHUNK_DETECT_TIMEOUT)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        r = httpx.post(f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                       json={"model": config.CHUNK_MODEL, "messages": messages, "stream": False,
                             "format": _SCHEMA, "options": {"temperature": 0}},
                       timeout=config.CHUNK_DETECT_TIMEOUT)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
    except DetectError:
        raise
    except Exception as e:  # noqa: BLE001 — transport/HTTP errors of either backend
        raise DetectError(str(e)) from e


def detect_structure(sample: str, *, parent: Optional[dict] = None, parent_ref: str = "",
                     feedback: str = "") -> Optional[dict]:
    """Ask the model for a boundary regex for ``sample``. ``parent`` is the
    spec of the level above (None at the root). ``feedback`` is what was wrong
    with a previous answer. ``None`` means the model found no structure.
    Raises ``DetectError`` when the model could not be reached."""
    if parent is None:
        system = _SYSTEM_ROOT
    else:
        system = _SYSTEM_SUB.format(unit_name=parent.get("unit_name") or "unit",
                                    example=parent_ref or "Art. 7",
                                    parent_regex=repr(parent["marker_regex"]))
    user = f"Text excerpts:\n\n{sample}"
    if feedback:
        user += f"\n\n{feedback}"
    spec = _loads_lenient(_ask(system, user) or "")
    if not spec or not spec.get("marker_regex"):
        return None
    return {"marker_regex": spec["marker_regex"], "example_headers": spec.get("example_headers") or [],
            "unit_name": spec.get("unit_name")}


def _compile(pattern: str):
    for flags in (re.MULTILINE, re.MULTILINE | re.IGNORECASE):
        try:
            return re.compile(pattern, flags)
        except re.error:
            continue
    return None


# ==========================================================================
# Sampling
# ==========================================================================

def _sample(text: str, chars: int) -> str:
    """Head plus two passages from the body (at 1/3 and 2/3), on line
    boundaries. The head of a document (or of a 300k-char título) is an index
    or a prose summary; the structure the regex must match lives in the body."""
    if len(text) <= chars:
        return text
    parts = [text[: chars // 2]]
    for frac in (1 / 3, 2 / 3):
        at = text.rfind("\n", 0, int(len(text) * frac)) + 1
        parts.append(text[at: at + chars // 4])
    return "\n\n[...]\n\n".join(parts)


def _sample_units(units: List[ArticleChunk]) -> str:
    return "\n\n[...]\n\n".join(_sample(u.text, config.CHUNK_SAMPLE_CHARS) for u in units)


# ==========================================================================
# Identifiers and local numbering runs
# ==========================================================================

# An Italian article header the model may capture as "Art. 1." / "Articolo 24-bis".
# Normalised to the SAME canonical form the deterministic chunker emits, so refs
# stay citable and the gold-set labels keep matching across both strategies.
_IT_ART = re.compile(r"^art(?:icolo)?\.?\s*\d+", re.IGNORECASE)


def _mref(m: "re.Match") -> str:
    """The captured identifier: the participating group (with alternatives
    each branch has its own group and only one participates), else the
    whole match. Nested capturing groups are rejected at detection time —
    whether the identifier is the outer one ('(Art\\. (\\d+))') or the inner
    one ('(Sección (SI 3).*)') is not decidable here."""
    return next((g for g in m.groups() if g), None) or m.group(0)


def _capturing_depth(pattern: str) -> int:
    """Deepest nesting of capturing groups ('(a(b))' -> 2, '(a)|(b)' -> 1)."""
    stack: List[bool] = []
    depth = deepest = 0
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":                                  # skip a character class
            j = i + 1
            while j < len(pattern) and (pattern[j] != "]" or pattern[j - 1] == "\\"):
                j += 1
            i = j + 1
            continue
        if c == "(":
            capturing = pattern[i + 1: i + 2] != "?" or pattern[i + 1: i + 3] == "?P"
            stack.append(capturing)
            if capturing:
                depth += 1
                deepest = max(deepest, depth)
        elif c == ")" and stack:
            if stack.pop():
                depth -= 1
        i += 1
    return deepest


def _normalise_llm_ref(raw: str, index: int) -> str:
    """Canonicalise a model-captured reference. Italian articles are forced to
    'Art. N' / 'Art. N-bis'; other refs pass through cleaned but intact
    (e.g. 'Artículo 5', 'Disposición transitoria primera', '7.1', 'a)')."""
    ref = re.sub(r"\s+", " ", raw or "").strip().strip(" .:;—-")
    if not ref:
        return f"Unit {index}"
    if _IT_ART.match(ref):
        return _normalise_ref(ref)
    return ref[:60]


# Sub-unit identifiers form a run — 1, 2, 3 / a), b), c) / 7.1, 7.2 / 1, 1-bis, 2
# / 8.1.9, 8.2.1 — in every jurisdiction and language, and the run restarts
# inside every parent. A sub-level regex is necessarily loose ("a number at
# line start") and in PDF text page numbers, dates and table rows satisfy it
# too; nothing lexical separates "4 Nelle rappresentazioni" (a comma) from
# "27 fogli, a colori;" (a row) — the run does. Top-level numbering is global
# and may have gaps (repealed articles, "Class AA"), so it is not filtered.
_ORDINALS = {"bis": 1, "ter": 2, "quater": 3, "quinquies": 4, "sexies": 5,
             "septies": 6, "octies": 7, "novies": 8, "decies": 9}
_ROMAN_MULTI = re.compile(r"^[ivxl]{2,}$", re.IGNORECASE)
_ROMAN_VAL = {"i": 1, "v": 5, "x": 10, "l": 50}
_MAX_GAP = 2       # one missing/repealed number is tolerated, a jump to a page number is not
_RUN_START = 2     # 1, a), 7.1 — or 2 when the first sub-unit shares the heading's line


def _roman(tok: str) -> int:
    total, prev = 0, 0
    for ch in reversed(tok.lower()):
        v = _ROMAN_VAL[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _seq_key(ref: str, roman: bool) -> Tuple[int, ...]:
    """'1' -> (1,)  '1-bis' -> (1, 1)  '7.1' -> (7, 1)  'a)' -> (1,)  'B' -> (2,)
    'iv' -> (4,) when the run is roman. Words ('Class', 'Policy') carry no
    order and are skipped."""
    key: List[int] = []
    for tok in re.findall(r"\d+|[^\W\d_]+", ref):
        if tok.isdigit():
            key.append(int(tok))
        elif tok.lower() in _ORDINALS:
            key.append(_ORDINALS[tok.lower()])
        elif roman and (len(tok) == 1 and tok.lower() in _ROMAN_VAL or _ROMAN_MULTI.match(tok)):
            key.append(_roman(tok))
        elif len(tok) == 1:
            key.append(ord(tok.lower()) - ord("a") + 1)
    return tuple(key)


def _is_successor(prev: Tuple[int, ...], cur: Tuple[int, ...]) -> bool:
    if not prev or not cur or cur == prev:
        return False
    if len(cur) == len(prev):
        for i in range(len(cur)):
            if cur[i] != prev[i]:
                # first differing component steps forward; anything below resets
                return 0 < cur[i] - prev[i] <= _MAX_GAP and all(c <= 1 for c in cur[i + 1:])
        return False
    if len(cur) == len(prev) + 1:                    # 1 -> 1-bis, 7 -> 7.1
        return cur[:-1] == prev and cur[-1] <= 1
    if len(cur) == len(prev) - 1:                    # 1-ter -> 2
        return cur[:-1] == prev[:-2] and 0 < cur[-1] - prev[-2] <= _MAX_GAP
    return False


def _local_runs(matches: List["re.Match"], text_len: int) -> List["re.Match"]:
    """Keep the boundaries that form the unit's numbering run. Empty when no
    run exists — this level does not apply to the unit."""
    refs = [_mref(m) for m in matches]
    # A run is roman only when it has multi-letter romans AND its single
    # letters are mostly i/v/x/l — a loose regex can conflate an outer
    # a) b) c) d) list with nested (i) (ii) (iv) (v) items, and there the
    # single 'v' must stay the 22nd letter, not 5.
    toks = [t for r in refs for t in re.findall(r"[^\W\d_]+", r)]
    singles = [t for t in toks if len(t) == 1]
    roman = any(_ROMAN_MULTI.match(t) for t in toks) and (
        not singles or 2 * sum(t.lower() in _ROMAN_VAL for t in singles) > len(singles))
    keys = [_seq_key(r, roman) for r in refs]
    if sum(1 for k in keys if k) < 2:
        return matches  # identifiers carry no order (words only): nothing to check

    # Greedy runs, each starting near 1 (page numbers 90, 91, 92 are
    # sequential too but do not start there); each match joins one run.
    runs: List[List[int]] = []
    used: set = set()
    for start in range(len(keys) - 1):
        k0 = keys[start]
        if start in used or not k0 or k0[-1] > _RUN_START or not _is_successor(k0, keys[start + 1]):
            continue
        run, last = [start], k0
        for j in range(start + 1, len(keys)):
            if j not in used and _is_successor(last, keys[j]):
                run.append(j)
                last = keys[j]
        used.update(run)
        runs.append(run)
    if not runs:
        return []

    # The MAIN run covers the most text — the sub-units proper, not the
    # unit's own index (a few short lines). A run BEFORE the main run is that
    # index: kept, so merge-by-ref joins each index line to its body. A run
    # restarting inside or after the main run is a nested list: body text.
    def span(run: List[int]) -> int:
        nxt = run[-1] + 1
        return (matches[nxt].start() if nxt < len(matches) else text_len) - matches[run[0]].start()

    main = max(runs, key=span)
    keep = sorted(i for run in runs if run is main or run[-1] < main[0] for i in run)
    return [matches[i] for i in keep]


# ==========================================================================
# Splitting one unit and judging the result
# ==========================================================================

@dataclass
class Assessment:
    ok: bool            # the split can be used
    suspicious: bool    # usable, but looks like an index-fitted regex: worth one retry
    why: str            # feedback for the model when not ok / suspicious
    bare: int = 0       # pieces that are a bare header with no body
    collisions: int = 0  # refs assembled from several substantial pieces (numbering restarts)
    top_share: float = 0.0  # share of the unit held by the largest piece
    bare_refs: Tuple[str, ...] = ()

    def better_than(self, other: "Assessment") -> bool:
        # A unit's refs should be unique: a regex that has to merge "1.2" from
        # six sections sits below the level asked for, however few bare
        # headers it leaves.
        return (self.ok, not self.suspicious, -self.bare, -self.collisions, -self.top_share) > \
               (other.ok, not other.suspicious, -other.bare, -other.collisions, -other.top_share)


_BARE_CHARS = 60      # a piece shorter than this is a header without a body
_DOMINANCE = 0.85     # one piece holding more than this did not split the unit
_MIN_PIECE = 150      # more pieces than chars/150 is fragmentation, not structure


Pieces = List[Tuple[str, List[str]]]   # (ref, pieces carrying that ref) in document order


def _split(text: str, rx, *, local: bool) -> Tuple[str, Pieces]:
    """Cut ``text`` at every header match. Returns the preamble (text before
    the first header) and, per ref in document order, every piece carrying
    that ref — the caller joins them into one chunk. ``local`` (below the
    root) keeps only the matches that form the unit's numbering run.

    A ref legitimately recurs in three ways: an index repeats the heading
    before the body; a PDF running header repeats it on every page of a long
    article (NUEA Torino 'Art. 8' x41); and some regulations restart the
    numbering per Titolo (Regolamento d'Igiene). Keeping only the longest
    piece — the previous behaviour — silently dropped the rest (47% of CTE
    DB-SUA, 44% of the NUEA). Merging is lossless."""
    matches = list(rx.finditer(text))
    if local:
        matches = _local_runs(matches, len(text))
    if len(matches) < 2:
        return text, []
    merged: Dict[str, List[str]] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start():end].strip()
        if body:
            merged.setdefault(_normalise_llm_ref(_mref(m), i + 1), []).append(body)
    return text[: matches[0].start()], list(merged.items())


def _assess(groups: Pieces, span: int, *, root: bool) -> Assessment:
    n = len(groups)
    if n < 2:
        return Assessment(False, False, "Your regex matched fewer than two unit headers in the text. "
                          "Look again at how the headers literally begin their lines.")
    if n > max(4, span // _MIN_PIECE):
        return Assessment(False, False, f"Your regex matched {n} lines — far too many: it matches "
                          "list items or table rows, not unit headers.")
    sizes = [sum(len(p) for p in pieces) for _, pieces in groups]
    top = max(sizes) / span
    if top > _DOMINANCE:
        # Below the root this level simply does not split the unit (kept
        # whole; the next level or the cap deals with it). At the root the
        # alternative is the deterministic chunker with no hierarchy at all,
        # so the split stays usable — the sub-levels carve up the big piece —
        # and is merely worth a retry.
        return Assessment(root, True, f"Your regex left {top:.0%} of the text in one piece: the lines "
                          "it matched are an opening list or an index, not the unit's subdivision. "
                          "Find the headers that recur through the body.", top_share=top)
    bare_refs = tuple(ref for (ref, _), size in zip(groups, sizes) if size < _BARE_CHARS)
    # A ref assembled from several substantial pieces: an index line or a
    # running header adds nothing substantial; a numbering restart (six CTE
    # sections each with their own "1.2") does.
    collisions = sum(1 for _, pieces in groups if sum(len(p) >= _BARE_CHARS for p in pieces) > 1)
    a = Assessment(True, False, "", bare=len(bare_refs), collisions=collisions, top_share=top,
                   bare_refs=bare_refs)
    if a.bare >= 3 and a.bare * 10 >= n:
        a.suspicious = True
        a.why = (f"{a.bare} of the {n} pieces your regex produced are a bare header with no body "
                 f"(e.g. {', '.join(repr(r) for r in bare_refs[:4])}): it fits the index lines but "
                 "not the headers as written in the body, which may carry a title, a dot, or a "
                 "different prefix — see the lines quoted below that mention the same identifiers.")
    return a


_CONTEXT_CHARS = 160


def _children(unit: ArticleChunk, rx, *, root: bool) -> Tuple[Optional[List[ArticleChunk]], Assessment]:
    """Split one unit at its (sub-)unit headers. At the root the preamble is
    front matter (title page, index) and is dropped. Below the root it is the
    unit's heading and intro — normative text: a substantial preamble becomes
    a chunk with the parent's own ref, and its opening is the context every
    child carries (the text's own structure says what the heading is)."""
    preamble, groups = _split(unit.text, rx, local=not root)
    a = _assess(groups, len(unit.text), root=root)
    if not a.ok:
        return None, a
    pairs = [(ref, "\n".join(pieces)) for ref, pieces in groups]
    if root:
        return [ArticleChunk(ref, body, bool(_QUANT_RE.search(body))) for ref, body in pairs], a
    heading = re.sub(r"\s+", " ", preamble).strip()[:_CONTEXT_CHARS]
    context = f"{unit.context} › {heading}" if unit.context else heading
    out: List[ArticleChunk] = []
    pre = preamble.strip()
    if len(pre) > _CONTEXT_CHARS:
        out.append(ArticleChunk(unit.article_ref, pre, bool(_QUANT_RE.search(pre)), unit.context))
    out += [ArticleChunk(f"{unit.article_ref}{REF_SEP}{ref}", body, bool(_QUANT_RE.search(body)), context)
            for ref, body in pairs]
    return out, a


# ==========================================================================
# Detecting one level: ask, validate on the real text, retry once
# ==========================================================================

def _apply(spec: Optional[dict], units: List[ArticleChunk], parent: Optional[dict]) -> Assessment:
    """How well ``spec`` splits the sampled units: the best result among them
    (a sub-level regex legitimately applies to some units and not others)."""
    root = parent is None
    rx = _compile(spec["marker_regex"]) if spec else None
    if rx is None:
        return Assessment(False, False, "The regex did not compile.")
    if _capturing_depth(spec["marker_regex"]) > 1:
        return Assessment(False, False, "Your regex has nested capturing groups, so the identifier is "
                          "ambiguous. Use exactly ONE capturing group, around the short identifier, "
                          "and make any other group non-capturing with (?:...).")
    if parent and spec["marker_regex"] == parent["marker_regex"]:
        return Assessment(False, False, "You returned the regex of the level ABOVE. Return the level "
                          "below it, or null if this unit has no finer structure.")
    best: Optional[Assessment] = None
    for u in units:
        _, a = _children(u, rx, root=root)
        if best is None or a.better_than(best):
            best = a
    return best  # type: ignore[return-value]


def _retry_sample(spec: dict, units: List[ArticleChunk], a: Assessment, *, root: bool) -> str:
    """Where the missed structure lives. The biggest piece the spec produced
    (the last index line that swallowed every body; the opening list's last
    item holding the whole título) — and, when headers came out bare, the
    lines elsewhere that mention those same identifiers: six section
    headings in 234k chars are not found by sampling passages, but
    "Sección SI 3 ..." is found by looking for "SI 3"."""
    rx = _compile(spec["marker_regex"])
    largest = max((( "\n".join(p) for u in units for _, p in _split(u.text, rx, local=not root)[1])),
                  key=len, default=units[0].text)
    sample = _sample(largest, config.CHUNK_SAMPLE_CHARS)
    if a.bare_refs:
        ids = [re.escape(re.sub(r"\s+", " ", r)) for r in a.bare_refs[:6]]
        finder = re.compile(r"^.*\b(?:" + "|".join(ids) + r")\b.*$", re.MULTILINE)
        lines = list(dict.fromkeys(m.group(0).strip() for u in units for m in finder.finditer(u.text)))
        if lines:
            sample += "\n\n[Lines mentioning the identifiers of the bare headers]\n\n" + \
                      "\n".join(lines[:40])[: config.CHUNK_SAMPLE_CHARS]
    return sample


_RETRIES = 2


def _detect_level(units: List[ArticleChunk], parent: Optional[dict], *,
                  initial: Optional[dict] = None) -> Optional[dict]:
    """One usable spec for the level below ``parent``, or ``None`` when the
    units have no consistent finer structure. ``initial`` is a cached spec to
    validate instead of asking first. A bad or suspicious answer gets a retry
    carrying the assessment as feedback and sampled from where the missed
    structure lives; the best answer wins (the model is not deterministic, so
    a second retry is cheap insurance against caching a miss). Raises
    ``DetectError`` when the model cannot be reached."""
    root = parent is None
    ref = units[0].article_ref
    spec = initial if initial is not None else detect_structure(_sample_units(units), parent=parent, parent_ref=ref)
    if spec is None:
        return None
    a = _apply(spec, units, parent)
    for _ in range(_RETRIES):
        if a.ok and not a.suspicious:
            break
        sample = _retry_sample(spec, units, a, root=root) if a.ok or a.top_share else _sample_units(units)
        alt = detect_structure(sample, parent=parent, parent_ref=ref, feedback=a.why)
        if alt is None:
            continue
        b = _apply(alt, units, parent)
        if b.better_than(a):
            spec, a = alt, b
    return spec if a.ok else None


# ==========================================================================
# Hierarchy
# ==========================================================================

def _refine(chunks: List[ArticleChunk], levels: List[Optional[dict]], depth: int,
            save) -> List[ArticleChunk]:
    """Descend while any chunk is longer than the target and levels remain.
    ``levels[depth]`` is detected lazily and cached (``None`` too, so a level
    with no finer structure is not re-queried on every ingestion)."""
    target = config.CHUNK_TARGET_CHARS
    if target <= 0 or depth >= config.CHUNK_MAX_DEPTH:
        return chunks
    # A level applies to the units the PREVIOUS level produced (ref depth ==
    # depth) — that is where the next finer structure lives. A unit the
    # previous level could not split stays whole for the cap; so does a
    # parent's kept preamble, which carries the parent's own ref: splitting
    # it here would name its pieces like the parent's real sub-units.
    big = [c for c in chunks if len(c.text) > target and c.article_ref.count(REF_SEP) + 1 == depth]
    if not big:
        return chunks
    # Detect from several of them: one unit can be the atypical one (an
    # abrogation list in lettere among articles in commi).
    cands = sorted(big, key=lambda c: len(c.text), reverse=True)[:3]
    if len(levels) <= depth or (levels[depth] and not levels[depth].get("verified")):
        cached = levels[depth] if len(levels) > depth else None
        try:
            spec = _detect_level(cands, levels[depth - 1], initial=cached)
        except DetectError:
            return chunks  # transient: leave this level undetected, do not cache
        if spec:
            spec["verified"] = True
        levels[depth: depth + 1] = [spec]
        save(levels)
    spec = levels[depth]
    rx = _compile(spec["marker_regex"]) if spec else None
    if rx is None:
        return chunks
    out: List[ArticleChunk] = []
    for c in chunks:
        subs = _children(c, rx, root=False)[0] if c in big else None
        out.extend(subs if subs else [c])
    return _refine(out, levels, depth + 1, save)


def _pack_lines(text: str, max_chars: int) -> List[str]:
    """Split text into <=max_chars parts on line boundaries (never mid-line)."""
    parts, buf, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) > max_chars and buf:
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def _cap(chunks: List[ArticleChunk], max_chars: int) -> List[ArticleChunk]:
    """Last resort once levels are exhausted: sub-split oversized chunks so one
    huge section cannot swamp the top-k budget. The ref is kept, parts numbered."""
    out: List[ArticleChunk] = []
    for c in chunks:
        if len(c.text) <= max_chars:
            out.append(c)
            continue
        parts = _pack_lines(c.text, max_chars)
        for i, body in enumerate(parts, 1):
            ref = c.article_ref if len(parts) == 1 else f"{c.article_ref} (part {i})"
            out.append(ArticleChunk(ref, body, bool(_QUANT_RE.search(body)), c.context))
    return out


def _materialise(chunks: List[ArticleChunk]) -> List[ArticleChunk]:
    """Fold the context line into the stored/embedded text."""
    return [ArticleChunk(c.article_ref, f"[{c.context}]\n{c.text}" if c.context else c.text, c.has_quantitative)
            for c in chunks]


# ==========================================================================
# Spec cache: {"levels": [root_spec, sub_spec_or_null, ...]}; each spec carries
# "verified": true once validated on the real text. The original single-spec
# file ({"marker_regex": ...}) is read as one unverified level.
# ==========================================================================

def _spec_cache_path(cache_key: str) -> Path:
    return config.RAW_CACHE_DIR / f"{_slug(cache_key)}.chunkspec.json"


def _load_levels(cache_path: Optional[Path]) -> List[Optional[dict]]:
    if not cache_path or not cache_path.exists():
        return []
    data = _loads_lenient(cache_path.read_text(encoding="utf-8")) or {}
    levels = data["levels"] if "levels" in data else [data]
    return [lv if lv and lv.get("marker_regex") else None for lv in levels]


def _saver(cache_path: Optional[Path]):
    def save(levels: List[Optional[dict]]) -> None:
        if cache_path and levels:
            cache_path.write_text(json.dumps({"levels": levels}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return save


# ==========================================================================
# Entry point
# ==========================================================================

def chunk_document_llm(
    text: str,
    *,
    cache_key: Optional[str] = None,
    force_detect: bool = False,
) -> List[ArticleChunk]:
    """LLM-detected boundaries -> deterministic slice, with deterministic
    fallback. ``cache_key`` (e.g. the doc_key) caches the detected regexes so
    the model runs once per level and re-ingestion is reproducible."""
    cache_path = _spec_cache_path(cache_key) if cache_key else None
    save = _saver(cache_path)
    levels = [] if force_detect else _load_levels(cache_path)
    root = ArticleChunk("", text, False)

    if not levels or (levels[0] and not levels[0].get("verified")):
        try:
            spec = _detect_level([root], None, initial=levels[0] if levels else None)
        except DetectError:
            spec = levels[0] if levels else None  # transient: use what we have, cache nothing
        else:
            if spec:
                spec["verified"] = True
            levels[0:1] = [spec]
            save(levels)

    rx = _compile(levels[0]["marker_regex"]) if levels and levels[0] else None
    chunks = _children(root, rx, root=True)[0] if rx else None
    if chunks:
        chunks = _refine(chunks, levels, 1, save)
        return _materialise(_cap(chunks, config.CHUNK_MAX_CHARS))

    # no usable top-level structure -> deterministic splitter
    return chunk_document(text)
