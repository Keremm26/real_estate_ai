"""
Scoring of one regulatory-agent output against a per-query expectation.

Shared by the downstream evaluation (regulatory agent alone,
``downstream.py``) and the full-pipeline benchmark (``tests/test_suite.py`` /
``tests/compare_arms.py``), so both report the same quantities computed the
same way.

Expectation shape (both query files):
    expect = {activate: true | false | "either", target_column, operator,
              value_min, value_max}
plus, per item: user_values (numbers the user stated — echo guard), sources
(gold-v2 labels of the justifying articles), precedence ({winner, loser, rule})
and the expected router use_case.

All functions are pure: data in, values out.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_LAW_NUM_RE = re.compile(r"\d{3,}")
ECHO_TOLERANCE = 0.5  # m2: a value this close to a user-stated number is an echo


# --------------------------------------------------------------------------
# requirement helpers
# --------------------------------------------------------------------------
def to_float(v: Any) -> Optional[float]:
    """Numeric value of a requirement; tolerates strings like '1.050,5' / '1.050.000'
    / '1050,5'. A single dot without a comma is a decimal point ('12.5')."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.count(".") > 1 or (s.count(".") == 1 and "," in s):   # '1.050' / '1.050,5' -> thousands dot
        s = s.replace(".", "")
    m = _NUM_RE.search(s.replace(",", "."))
    return float(m.group(0)) if m else None


def usable(req: Dict[str, Any], allowed: List[str]) -> bool:
    return req.get("target_column") in allowed and to_float(req.get("value")) is not None


def main_requirement(reqs: List[Dict[str, Any]], allowed: List[str],
                     expected_col: Optional[str]) -> Optional[Dict[str, Any]]:
    """The requirement that would drive the ranking: first usable one on the
    expected column, else the first usable one at all."""
    candidates = [r for r in reqs if usable(r, allowed)]
    for r in candidates:
        if expected_col and r.get("target_column") == expected_col:
            return r
    return candidates[0] if candidates else None


def parse_output(raw_text: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """(found, requirements) from the agent's raw JSON answer."""
    from app.services.llm.agents.schema import RegulatoryResponse
    from app.utils.json_parser import safe_extract_json

    data = safe_extract_json(raw_text or "", schema=RegulatoryResponse)
    if not data:
        return False, []
    return bool(data.found), list(data.requirements)


# --------------------------------------------------------------------------
# grounding / faithfulness checks
# --------------------------------------------------------------------------
_ACT_NUM_RE = re.compile(r"\bn\.?\s*(\d+)\b", re.IGNORECASE)


def doc_ids(text: str) -> set:
    """Identifiers of a legal act in a name or citation: numbers / years with
    >= 3 digits, plus the act number written as 'n. 30' (short act numbers such
    as municipal regulations would otherwise be invisible)."""
    return set(_LAW_NUM_RE.findall(text or "")) | {f"n{d}" for d in _ACT_NUM_RE.findall(text or "")}


def doc_grounded(regulation: str, hits: List[Dict[str, Any]]) -> bool:
    """'regulation' cites a document that was in context: an identifier of a
    retrieved doc_name (see doc_ids) appears in the citation string."""
    if not regulation or not hits:
        return False
    cited = doc_ids(regulation)
    return any(cited & doc_ids(h.get("doc_name") or "") for h in hits)


def _norm_num(s: str) -> str:
    return f"{float(s.replace(',', '.')):g}"


def value_grounded(description: str, context: str) -> Tuple[Optional[bool], float]:
    """Do the per-unit figures quoted in the description occur in the context?
    Only decimals and numbers >= 10 are checked (small integers are noise).
    The context must be the RETRIEVED DOCUMENTS only — not the user query,
    otherwise a figure the user stated counts as 'grounded'.
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


def is_echo(value: Optional[float], user_values: Iterable[Any]) -> Optional[bool]:
    """The main value repeats a number the user stated (within ECHO_TOLERANCE).
    None when there is nothing to compare."""
    nums = [to_float(u) for u in (user_values or [])]
    nums = [n for n in nums if n is not None]
    if value is None or not nums:
        return None
    return any(abs(value - n) <= ECHO_TOLERANCE for n in nums)


def source_in_context(sources: List[Dict[str, Any]], hits: List[Dict[str, Any]],
                      key2name: Dict[str, str]) -> Optional[bool]:
    """At least one expected source label is among the retrieved chunks.
    Separates retrieval misses from extraction misses. None when the item
    declares no source."""
    if not sources:
        return None
    from app.services.llm.rag.eval.run import _hit_matches, _label

    labels = [_label(s, key2name) for s in sources]
    return any(_hit_matches(h, lab) for h in hits for lab in labels)


def winner_cited(regulation: Optional[str], precedence: Optional[Dict[str, Any]],
                 key2name: Dict[str, str]) -> Optional[bool]:
    """On precedence items: the main requirement's 'regulation' names the
    winner document (by its law number / year). None when not applicable."""
    if not precedence or not precedence.get("winner"):
        return None
    if not regulation:
        return False
    return bool(doc_ids(key2name.get(precedence["winner"], precedence["winner"])) & doc_ids(regulation))


# --------------------------------------------------------------------------
# one output -> one scored record
# --------------------------------------------------------------------------
def score_output(
    expect: Dict[str, Any],
    raw_text: str,
    hits: List[Dict[str, Any]],
    allowed: List[str],
    *,
    user_values: Iterable[Any] = (),
    sources: Optional[List[Dict[str, Any]]] = None,
    precedence: Optional[Dict[str, Any]] = None,
    expected_use_case: Optional[str] = None,
    routing: Optional[Dict[str, Any]] = None,
    context_text: Optional[str] = None,
    key2name: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Score the agent's answer for one query against its expectation.

    ``hits`` = retrieval trace hits (doc_name / article_ref); ``routing`` =
    trace['use_case_routing'] (None when the use case was given, not routed);
    ``context_text`` = retrieved documents text for value grounding (None to
    skip that check, e.g. in the pipeline where the prompt is not stored).
    """
    if key2name is None:
        from app.services.llm.rag.eval.run import _doc_key_to_name
        key2name = _doc_key_to_name()

    found, reqs = parse_output(raw_text)
    main = main_requirement(reqs, allowed, expect.get("target_column"))
    activated = found and main is not None
    value = to_float(main.get("value")) if main else None
    regulation = (main.get("regulation") if main else None) or None

    lo, hi = expect.get("value_min"), expect.get("value_max")
    in_range = None
    if lo is not None and hi is not None and expect.get("activate") is True:
        in_range = (value is not None) and (lo <= value <= hi)
    elif lo is not None and hi is not None and expect.get("activate") == "either" and activated:
        in_range = value is not None and lo <= value <= hi

    expect_act = expect.get("activate")
    main_grounded = doc_grounded(regulation or "", hits) if activated else None

    per_req = []
    for r in reqs:
        vg_any, vg_frac = (value_grounded(str(r.get("description") or ""), context_text)
                           if context_text is not None else (None, 0.0))
        per_req.append({
            "target_column": r.get("target_column"), "operator": r.get("operator"),
            "value": r.get("value"), "unit": r.get("unit"), "regulation": r.get("regulation"),
            "usable": usable(r, allowed),
            "doc_grounded": doc_grounded(str(r.get("regulation") or ""), hits),
            "value_grounded": vg_any, "value_grounded_frac": vg_frac,
        })

    if expect_act is True:
        activation_ok = activated
    elif expect_act is False:
        activation_ok = not activated
    else:  # 'either': declining is fine; activating is fine only if grounded and in range
        activation_ok = (not activated) or bool(main_grounded and in_range is not False)

    routed = (routing or {}).get("use_case") if routing else None
    return {
        "expect_activate": expect_act,
        "found": found,
        "n_requirements": len(reqs),
        "activated": activated,
        "activation_ok": activation_ok,
        "main_value": value,
        "main_target": main.get("target_column") if main else None,
        "main_operator": main.get("operator") if main else None,
        "main_regulation": regulation,
        "target_ok": (main.get("target_column") == expect.get("target_column")) if (main and expect.get("target_column")) else None,
        "operator_ok": (str(main.get("operator", "")).strip() == expect.get("operator")) if (main and expect.get("operator")) else None,
        "value_in_range": in_range,
        "echo": is_echo(value, user_values),
        "main_doc_grounded": main_grounded,
        "source_in_context": source_in_context(sources or [], hits, key2name),
        "winner_cited": winner_cited(regulation, precedence, key2name) if activated else (None if not precedence else False),
        "routed_use_case": routed,
        "routing_ok": (routed == expected_use_case) if (routed is not None and expected_use_case) else None,
        "requirements": per_req,
    }


# --------------------------------------------------------------------------
# aggregation helpers
# --------------------------------------------------------------------------
def rate(flags: Iterable[Optional[bool]]) -> Optional[float]:
    vals = [bool(f) for f in flags if f is not None]
    return sum(vals) / len(vals) if vals else None


def mean(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headline metrics over scored records (any subset: an arm, a language, a type)."""
    exp_true = [r for r in records if r["expect_activate"] is True]
    exp_false = [r for r in records if r["expect_activate"] is False]
    exp_either = [r for r in records if r["expect_activate"] == "either"]
    all_reqs = [rq for r in records for rq in r["requirements"]]
    return {
        "n_runs": len(records),
        "activation_ok_rate": rate(r["activation_ok"] for r in records),
        "activation_rate": rate(r["activated"] for r in exp_true),
        "correct_decline_rate": rate(not r["activated"] for r in exp_false),
        "either_ok_rate": rate(r["activation_ok"] for r in exp_either),
        "value_in_range_rate": rate(r["value_in_range"] for r in records),
        "echo_rate": rate(r["echo"] for r in records if r["activated"]),
        "target_ok_rate": rate(r["target_ok"] for r in exp_true),
        "operator_ok_rate": rate(r["operator_ok"] for r in exp_true),
        "main_doc_grounding_rate": rate(r["main_doc_grounded"] for r in records),
        "value_grounding_rate": rate(rq["value_grounded"] for rq in all_reqs),
        "source_in_context_rate": rate(r["source_in_context"] for r in records),
        "winner_cited_rate": rate(r["winner_cited"] for r in records),
        "routing_ok_rate": rate(r["routing_ok"] for r in records),
        "n_requirements_total": len(all_reqs),
    }
