"""Review + Transformation agent (agent 2).

Reviews the frozen matrix per concept group (all cities of a concept seen at
once), separating semantic review from cross-city comparability, and authors a
machine-readable TransformRule per cell. Also triages new-concept candidates.
It REVIEWS ONLY — it never re-maps or re-discovers (mapping stays agent 1's).

Run:
    uv run python -m app.services.schema_alignment.review_agent review           # all groups
    uv run python -m app.services.schema_alignment.review_agent review --group energy
    uv run python -m app.services.schema_alignment.review_agent triage
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.config import AGENT_MODELS
from app.services.llm.langchain_client import get_llm, is_oss_model
from app.utils.json_parser import safe_extract_json

try:
    from .review import (
        CandidateDecision, ConceptReview, GroupReview, ReviewedMatrix, TriageResult,
    )
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from review import (  # type: ignore
        CandidateDecision, ConceptReview, GroupReview, ReviewedMatrix, TriageResult,
    )

VOCAB_PATH = "data/epc_schema/concept_vocabulary.v1.1.json"
MATRIX_PATH = "data/epc_schema/mapping_matrix.json"
OUT_DIR = "data/epc_schema"


def _vocab_meta(vocab: dict) -> dict[str, dict]:
    return {c["id"]: c for c in vocab["concepts"]}


def render_group_input(group: str, rows: list[dict], meta: dict[str, dict]) -> str:
    lines = []
    for r in rows:
        c = meta.get(r["concept_id"], {})
        unit = f" unit={r.get('canonical_unit')}" if r.get("canonical_unit") else ""
        lines.append(
            f"\n## {r['concept_id']} [risk={r['comparability_risk']}{unit} "
            f"coverage={r.get('coverage_level')} n={r['n_cells_any']}]"
        )
        lines.append(f"def: {c.get('definition', '')}")
        if c.get("comparability_note"):
            lines.append(f"note: {c['comparability_note']}")
        for city, cell in r["cells"].items():
            q = f" q='{cell['qualifier']}'" if cell.get("qualifier") else ""
            tf = f" tf={cell['transform_needed']}" if cell.get("transform_needed", "none") != "none" else ""
            lines.append(
                f"  - {city}: {','.join(cell['source_columns'])} "
                f"[{cell['semantic_match']}/{cell['confidence']}]{q}{tf}"
            )
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the Review + Transformation stage for a cross-city EPC schema \
alignment system. You are given, per canonical concept, ALL cities that mapped to it \
(source columns, semantic_match, qualifier, confidence, coarse transform_needed) plus the \
concept's definition, comparability_risk and comparability_note. You REVIEW — you do NOT \
re-map columns or invent concepts.

For EACH concept output a ConceptReview:
1. comparability_verdict (comparable | conditional | not_comparable) for the VALUES across \
cities, with a reason. Separate this from semantic match: a concept can be semantically \
direct in every city yet NOT value-comparable (e.g. energy_class — same idea, different \
national A-G methodologies). Lean on comparability_risk: high-risk concepts are usually \
not_comparable or conditional.
2. For EACH city cell, a CellReview:
   - verdict: keep | downgrade | flag. DOWNGRADE overconfident cells — e.g. a 'direct' whose \
qualifier reveals a different definition/scope (habitable vs heated area; intensity mapped to \
a *_total concept; delivered vs final energy) should become 'partial'. FLAG genuinely doubtful ones.
   - reviewed_semantic_match (direct|partial|uncertain) and reviewed_confidence (high|medium|low).
   - transform: a FULLY-SPECIFIED, machine-runnable rule (author it, do NOT execute). A \
deterministic program must run it with NO further interpretation, so you MUST provide complete \
params. If you cannot fully specify an op, use `preserve` (or `none`) and put the intended logic \
in note — NEVER emit an op you cannot fully specify.
     * unit_convert → params {from, to, factor:<number>}. factor is REQUIRED and numeric. If the \
conversion needs an external rate you don't know (e.g. currency GBP->EUR), use preserve instead.
     * scale_fix → params {factor:<number>} (e.g. mm->m is 0.001).
     * recategorize → params {mapping:{<source_value>:<canonical_value>, ...}} covering ALL \
observed source levels. If you cannot enumerate the full crosswalk, use preserve instead.
     * derive → params {operation, operands}. operation ∈ multiply|divide|sum|subtract. operands \
is an ORDERED list; each operand is either a source column name OR 'concept:<concept_id>' for \
another concept (use 'concept:floor_area' for the floor area). E.g. intensity→total is \
{operation:'multiply', operands:['<intensity_col>','concept:floor_area']}. If the derivation is \
NOT expressible this way (boolean presence like col>0, date parsing, string extraction), use \
preserve and describe the logic in note.
     * preserve when values must NOT be normalized because they are not comparable (record why in note).
   - reason.

Be conservative and explicit. Return STRICT JSON: {"reviews": [ConceptReview, ...]} where \
ConceptReview = {concept_id, comparability_verdict, comparability_reason, notes, cells:[{city, \
source_columns, verdict, reviewed_semantic_match, reviewed_confidence, qualifier, reason, \
transform:{op,params,guard,note}}]}."""


def review_group(group: str, rows: list[dict], meta: dict, model_name: str | None) -> list[ConceptReview]:
    resolved = model_name or AGENT_MODELS.get("default")
    llm = get_llm(model_name=resolved)
    user = (f"Concept group: {group}\nReview every concept below and its city cells:\n"
            f"{render_group_input(group, rows, meta)}")
    messages = [("system", SYSTEM_PROMPT), ("user", user)]

    result = None
    if hasattr(llm, "with_structured_output") and not is_oss_model(resolved):
        try:
            result = llm.with_structured_output(GroupReview, method="json_mode").invoke(messages)
        except Exception as e:  # noqa: BLE001
            print(f"[review] group {group}: structured output failed ({e}); text fallback")
    if result is None:
        raw = llm.invoke(messages)
        text = raw.content if hasattr(raw, "content") else str(raw)
        result = safe_extract_json(text, schema=GroupReview)
    if result is None:
        raise RuntimeError(f"Review agent returned unparseable output for group {group}")
    return result.reviews


# --------------------------------------------------------------------------- #
def run_review(model_name: str | None, only_group: str | None) -> ReviewedMatrix:
    vocab = json.loads(Path(VOCAB_PATH).read_text())
    matrix = json.loads(Path(MATRIX_PATH).read_text())
    meta = _vocab_meta(vocab)

    groups: dict[str, list[dict]] = {}
    for r in matrix["rows"]:
        if r["n_cells_any"] >= 1:
            groups.setdefault(r["group"], []).append(r)
    if only_group:
        groups = {g: rs for g, rs in groups.items() if g == only_group}

    all_reviews: list[ConceptReview] = []
    for g, rows in groups.items():
        print(f"[review] {g} ({len(rows)} concepts) ...")
        all_reviews.extend(review_group(g, rows, meta, model_name))

    verd = {}
    comp = {}
    for cr in all_reviews:
        comp[cr.comparability_verdict] = comp.get(cr.comparability_verdict, 0) + 1
        for cell in cr.cells:
            verd[cell.verdict] = verd.get(cell.verdict, 0) + 1
    return ReviewedMatrix(
        vocab_version=vocab.get("vocab_version", "?"),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=model_name or AGENT_MODELS.get("default"),
        reviews=all_reviews,
        stats={"concepts_reviewed": len(all_reviews), "cell_verdicts": verd,
               "comparability_verdicts": comp},
    )


def build_transforms(rm: ReviewedMatrix) -> list[dict]:
    out = []
    for cr in rm.reviews:
        for cell in cr.cells:
            if cell.transform.op not in ("none", "rename"):
                out.append({"concept_id": cr.concept_id, "city": cell.city,
                            "source_columns": cell.source_columns, "op": cell.transform.op,
                            "params": cell.transform.params, "guard": cell.transform.guard,
                            "note": cell.transform.note})
    return out


def render_review_markdown(rm: ReviewedMatrix) -> str:
    s = rm.stats
    out = [f"# Reviewed mapping matrix (vocab {rm.vocab_version}) — {rm.generated_at}",
           f"_model {rm.model} · {s['concepts_reviewed']} concepts_",
           f"**Comparability:** {s['comparability_verdicts']}  **Cell verdicts:** {s['cell_verdicts']}", ""]
    by_group: dict = {}
    for cr in rm.reviews:
        by_group.setdefault(cr.concept_id.split("_")[0], None)
    for cr in sorted(rm.reviews, key=lambda x: x.comparability_verdict):
        badge = {"comparable": "🟢", "conditional": "🟠", "not_comparable": "🔴"}.get(cr.comparability_verdict, "")
        out.append(f"\n### `{cr.concept_id}` — {badge} {cr.comparability_verdict}")
        out.append(f"_{cr.comparability_reason}_")
        for cell in cr.cells:
            t = cell.transform
            trule = f" → **{t.op}**" + (f" {t.params}" if t.params else "") if t.op not in ("none", "rename") else ""
            out.append(f"  - {cell.city}: `{','.join(cell.source_columns)}` "
                       f"[{cell.verdict}→{cell.reviewed_semantic_match}/{cell.reviewed_confidence}]{trule}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
_TRIAGE_SYSTEM = """You triage NEW-CONCEPT CANDIDATES for a cross-city EPC vocabulary. Each \
candidate is a source column a city could not map to an existing concept. Given the existing \
concept ids and the candidates (with city, column, suggested_label, reason), decide for EACH:
- decision: promote_new (a real recurring EPC concept genuinely missing — give proposed_concept_id \
in snake_case) | map_to_existing (it actually fits an existing concept — give target_concept_id) | \
city_specific (real but only this city / too granular) | reject (noise/duplicate/provenance).
Favor promote_new only when the concept is clearly useful and likely recurs across cities. \
Return STRICT JSON {"decisions":[{city,source_column,suggested_label,decision,target_concept_id,\
proposed_concept_id,reason}], "summary": "..."}."""


def run_triage(model_name: str | None) -> TriageResult:
    vocab = json.loads(Path(VOCAB_PATH).read_text())
    matrix = json.loads(Path(MATRIX_PATH).read_text())
    ids = sorted(c["id"] for c in vocab["concepts"])
    cands = matrix.get("new_concept_candidates", [])
    resolved = model_name or AGENT_MODELS.get("default")
    llm = get_llm(model_name=resolved)
    user = (f"EXISTING concept ids:\n{', '.join(ids)}\n\nCANDIDATES ({len(cands)}):\n"
            + json.dumps(cands, ensure_ascii=False))
    messages = [("system", _TRIAGE_SYSTEM), ("user", user)]

    result = None
    if hasattr(llm, "with_structured_output") and not is_oss_model(resolved):
        try:
            result = llm.with_structured_output(TriageResult, method="json_mode").invoke(messages)
        except Exception as e:  # noqa: BLE001
            print(f"[triage] structured output failed ({e}); text fallback")
    if result is None:
        raw = llm.invoke(messages)
        text = raw.content if hasattr(raw, "content") else str(raw)
        result = safe_extract_json(text, schema=TriageResult)
    if result is None:
        raise RuntimeError("Triage returned unparseable output")
    return result


# --------------------------------------------------------------------------- #
def _cmd_review(args) -> None:
    rm = run_review(args.model, args.group)
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(f"{OUT_DIR}/reviewed_mappings.json").write_text(rm.model_dump_json(indent=2))
    Path(f"{OUT_DIR}/reviewed_mappings.review.md").write_text(render_review_markdown(rm))
    transforms = build_transforms(rm)
    Path(f"{OUT_DIR}/transforms.json").write_text(json.dumps(transforms, indent=2, ensure_ascii=False))
    print("[review]", json.dumps(rm.stats, indent=2))
    print(f"[review] {len(transforms)} transform rules -> transforms.json")
    print("[review] wrote reviewed_mappings.json + .review.md")


def _cmd_triage(args) -> None:
    tr = run_triage(args.model)
    Path(f"{OUT_DIR}/candidate_triage.json").write_text(tr.model_dump_json(indent=2))
    from collections import Counter
    print("[triage] decisions:", dict(Counter(d.decision for d in tr.decisions)))
    print("[triage]", tr.summary)
    print("[triage] wrote candidate_triage.json")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Review + Transformation agent (agent 2).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rv = sub.add_parser("review", help="Review the matrix per concept group.")
    rv.add_argument("--group", default=None)
    rv.add_argument("--model", default=None)
    rv.set_defaults(func=_cmd_review)
    tg = sub.add_parser("triage", help="Triage new-concept candidates.")
    tg.add_argument("--model", default=None)
    tg.set_defaults(func=_cmd_triage)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
