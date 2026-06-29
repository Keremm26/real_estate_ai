"""
Score a run's ConstraintIR against the phrase-level gold
(tests/constraint_ground_truth.json).

This measures IR-vs-intent: given the phrases that went into a query and the
ConstraintIR the pipeline actually produced, how many of the constraints we
EXPECTED actually showed up (recall), and did any "future-use" phrase wrongly add
a current-typology filter (a precision check).

Pure functions — no model calls, no pipeline imports beyond the IR types and the
shared column-alias helpers.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from app.services.llm.constraint_ir import ConstraintIR
from app.services.llm.constraint_validation import (
    COLUMN_ALIASES,
    TYPOLOGY_COLUMN,
    _build_reverse,
    _canonical,
)


def load_gold(path: str) -> Dict[str, Any]:
    """Load the gold JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def score_query(
    *,
    phrases: List[str],
    ir: ConstraintIR,
    gold: Dict[str, Any],
    column_aliases: Optional[Dict[str, Set[str]]] = None,
) -> Dict[str, Any]:
    """Score one query's IR against the expected constraints for its phrases.

    Args:
        phrases: the gold phrases that were combined into this query.
        ir: the ConstraintIR the pipeline produced for the query.
        gold: the loaded gold dict (has a "phrases" map).
        column_aliases: alias map (defaults to the shared COLUMN_ALIASES).

    Returns a dict with: expected count, captured count, recall, the list of
    missed items, and any "should-have-no-typology" violations.
    """
    aliases = COLUMN_ALIASES if column_aliases is None else column_aliases
    reverse = _build_reverse(aliases)

    gphrases = gold.get("phrases", {})

    # Canonical columns present in the IR.
    ir_cols = {
        _canonical(e.target_column, reverse)
        for e in ir.predicates()
        if e.target_column
    }
    has_geo = len(ir.geo()) > 0
    typ_canon = _canonical(TYPOLOGY_COLUMN, reverse)
    has_typology = typ_canon in ir_cols

    expected: List[str] = []
    captured: List[str] = []
    missed: List[str] = []
    no_typology_violations: List[str] = []

    for ph in phrases:
        spec = gphrases.get(ph)
        if not spec:
            continue

        # "future use" phrases must NOT add a current-typology filter.
        if spec.get("expected_no_typology") and has_typology:
            no_typology_violations.append(ph)

        for e in spec.get("expected", []):
            if e.get("kind") == "geo":
                desc = f"{ph}:GEO"
                expected.append(desc)
                (captured if has_geo else missed).append(desc)
            elif e.get("column"):
                col = _canonical(e["column"], reverse)
                desc = f"{ph}:{e['column']}"
                expected.append(desc)
                (captured if col in ir_cols else missed).append(desc)
            # entries with neither kind=geo nor column are ignored (shouldn't happen)

    total = len(expected)
    recall = (len(captured) / total) if total else None

    return {
        "phrases": list(phrases),
        "expected": total,
        "captured": len(captured),
        "missed": missed,
        "recall": recall,
        "no_typology_violations": no_typology_violations,
    }


def aggregate_scores(per_query: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine many per-query score dicts into overall recall + violation totals."""
    total_expected = sum(r["expected"] for r in per_query)
    total_captured = sum(r["captured"] for r in per_query)
    violations = sum(len(r["no_typology_violations"]) for r in per_query)
    scored = [r for r in per_query if r["recall"] is not None]
    macro = (sum(r["recall"] for r in scored) / len(scored)) if scored else None
    return {
        "queries": len(per_query),
        "micro_recall": (total_captured / total_expected) if total_expected else None,
        "macro_recall": macro,
        "total_expected": total_expected,
        "total_captured": total_captured,
        "no_typology_violations": violations,
    }
