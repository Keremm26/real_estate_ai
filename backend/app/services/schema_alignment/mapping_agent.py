"""Mapping agent (agent 1): map each city's columns onto the frozen concepts.

One independent LLM call per city (no cross-city contamination). Input is the
concept DEFINITIONS ONLY (sources stripped) plus the city's full column profile.
A deterministic `matrix` step then assembles the per-city results into the
concept x city matrix and cross-checks them against the discovery-stage sources.

Run:
    uv run python -m app.services.schema_alignment.mapping_agent map          # all cities
    uv run python -m app.services.schema_alignment.mapping_agent map --city liege
    uv run python -m app.services.schema_alignment.mapping_agent matrix
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.config import AGENT_MODELS
from app.services.llm.langchain_client import get_llm, is_oss_model
from app.utils.json_parser import safe_extract_json

try:
    from .concepts import coverage_level
    from .mapping import (
        ConceptRow, CityMapping, MappingMatrix, MatrixCandidate, MatrixCell,
    )
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from concepts import coverage_level  # type: ignore
    from mapping import (  # type: ignore
        ConceptRow, CityMapping, MappingMatrix, MatrixCandidate, MatrixCell,
    )

VOCAB_PATH = "data/epc_schema/concept_vocabulary.v1.1.json"
PROFILES_DIR = "data/epc_profiles"
MAP_DIR = "data/epc_schema/mappings"


# --------------------------------------------------------------------------- #
# Rendering the agent inputs
# --------------------------------------------------------------------------- #
def load_vocab(path: str = VOCAB_PATH) -> dict:
    return json.loads(Path(path).read_text())


def render_concept_target(vocab: dict) -> str:
    """Concept definitions only (no sources) — the stable mapping target."""
    lines = []
    for c in sorted(vocab["concepts"], key=lambda x: (x["group"], x["id"])):
        unit = f" unit={c['canonical_unit']}" if c.get("canonical_unit") else ""
        lines.append(
            f"- {c['id']} [{c['group']}/{c['value_kind']}{unit} risk={c['comparability_risk']}]: "
            f"{c['definition']}"
        )
    return "\n".join(lines)


def _col_line(col: dict) -> str:
    name, kind = col["original_name"], col["inferred_kind"]
    bits = [f"{name} | {kind} | miss={col['missing_ratio'] * 100:.0f}% uniq={col['n_unique']}"]
    units = (col["detected"].get("unit_from_name") or []) + (col["detected"].get("unit_from_values") or [])
    if units:
        bits.append("unit:" + ",".join(dict.fromkeys(units)))
    if col["detected"].get("patterns"):
        bits.append("pat:" + ",".join(col["detected"]["patterns"]))
    s = col.get("numeric_stats")
    if s:
        bits.append(f"~[{s['min']:g}..{s['max']:g}] p50={s['p50']:g}")
    elif col.get("top_values"):
        bits.append("vals: " + ", ".join(f"{t['value']}({t['pct'] * 100:.0f}%)" for t in col["top_values"][:4]))
    elif col.get("sample_values"):
        bits.append("e.g. " + ", ".join(col["sample_values"][:3]))
    elif col.get("masked_samples"):
        bits.append("mask: " + ", ".join(col["masked_samples"][:2]))
    return " | ".join(bits)


def render_city_columns(profile: dict) -> str:
    return "\n".join("- " + _col_line(c) for c in profile["columns"])


SYSTEM_PROMPT = """You map ONE city's residential EPC/APE dataset columns onto a FIXED \
canonical concept vocabulary. You are given the canonical concepts (definitions only) \
and this city's full column profile (name, type, missing %, cardinality, units, \
distributions/samples; names are in the local language).

TASK: for each canonical concept this city can supply, output a mapping. Be \
conservative — never invent a mapping.

For every mapping record FOUR INDEPENDENT axes:
- semantic_match: direct | partial | uncertain — does the source CORRESPOND to the concept? \
This is about meaning ONLY, never about how the value is produced. (There is no 'derived' \
here: if the value must be computed, keep semantic_match direct/partial and set \
transform_needed=derivation.)
- transform_needed: none | unit_conversion | value_normalization | derivation | recategorization \
(COARSE category only — do NOT write the transformation formula; that is a later stage)
- confidence: high | medium | low
- qualifier: the variant of THIS column vs the concept (e.g. floor-area basis \
habitable/heated/cadastral/thermal_zone; energy scope final/primary/delivered; total vs \
intensity) — you MUST set this whenever the concept lumps variants; use null (not "") otherwise.
Also give short `evidence` (name/units/distribution) and use EXACT original column names.

RULES:
- A column normally maps to at most one concept (its best fit). Only list >1 source_columns \
when the concept is genuinely DERIVED from several (transform_needed=derivation).
- Do not upgrade comparability; the concept's risk is fixed in the vocabulary.
- ACCOUNT FOR EVERY COLUMN exactly once: each source column is either the source of a \
mapping, OR listed in `unmapped` with a category (identifier/address/administrative/ \
methodology_provenance/technical_detail/duplicate/other), OR — if it clearly encodes a real \
EPC concept MISSING from the vocabulary — listed in `new_concept_candidates`.
- Prefer the per-area intensity concept over the total when a column is an intensity, and vice versa.

Return STRICT JSON with keys: mappings (list of {concept_id, source_columns, semantic_match, \
qualifier, transform_needed, confidence, evidence, notes}), unmapped (list of {column, kind, \
category, reason}), new_concept_candidates (list of {column, suggested_label, reason}), \
summary (2-3 sentences)."""


def map_city(profile_path: str, vocab: dict, model_name: str | None = None) -> CityMapping:
    profile = json.loads(Path(profile_path).read_text())
    resolved = model_name or AGENT_MODELS.get("default")
    llm = get_llm(model_name=resolved)

    user = (
        f"CANONICAL CONCEPTS (map into these ids only):\n{render_concept_target(vocab)}\n\n"
        f"CITY: {profile['city']} ({profile['country']}, lang={profile['language']}), "
        f"{profile['n_cols']} columns.\nCOLUMNS:\n{render_city_columns(profile)}"
    )
    messages = [("system", SYSTEM_PROMPT), ("user", user)]

    result = None
    if hasattr(llm, "with_structured_output") and not is_oss_model(resolved):
        try:
            result = llm.with_structured_output(CityMapping, method="json_mode").invoke(messages)
        except Exception as e:  # noqa: BLE001
            print(f"[map] {profile['city']}: structured output failed ({e}); text fallback")
    if result is None:
        raw = llm.invoke(messages)
        text = raw.content if hasattr(raw, "content") else str(raw)
        result = safe_extract_json(text, schema=CityMapping)
    if result is None:
        raise RuntimeError(f"Mapping agent returned unparseable output for {profile['city']}")

    # stamp deterministic metadata (don't trust the model for these)
    result.city = profile["city"]
    result.country = profile["country"]
    result.language = profile["language"]
    result.vocab_version = vocab.get("vocab_version", "?")
    result.model = resolved
    result.generated_at = datetime.now().isoformat(timespec="seconds")
    result.n_source_columns = profile["n_cols"]
    return result


# --------------------------------------------------------------------------- #
# Matrix assembly (deterministic) + discovery cross-check
# --------------------------------------------------------------------------- #
def _discovery_index(vocab: dict) -> dict[tuple[str, str], set[str]]:
    """(concept_id, city) -> set of source columns claimed at discovery time."""
    idx: dict[tuple[str, str], set[str]] = {}
    for c in vocab["concepts"]:
        for s in c.get("sources", []):
            idx.setdefault((c["id"], s["city"]), set()).add(s["column"])
    return idx


def build_matrix(vocab: dict, map_dir: str = MAP_DIR) -> MappingMatrix:
    disc = _discovery_index(vocab)
    meta = {c["id"]: c for c in vocab["concepts"]}
    city_maps = [CityMapping(**json.loads(p.read_text())) for p in sorted(Path(map_dir).glob("*.json"))]
    cities = [cm.city for cm in city_maps]

    rows: dict[str, ConceptRow] = {}
    for cid, c in meta.items():
        rows[cid] = ConceptRow(
            concept_id=cid, group=c["group"], comparability_risk=c["comparability_risk"],
            canonical_unit=c.get("canonical_unit"), coverage_level=c.get("coverage_level"),
        )

    unknown_ids: set[str] = set()
    candidates: list[MatrixCandidate] = []
    for cm in city_maps:
        for nc in cm.new_concept_candidates:
            candidates.append(MatrixCandidate(
                city=cm.city, source_column=nc.column,
                suggested_label=nc.suggested_label, reason=nc.reason,
            ))
        for m in cm.mappings:
            if m.concept_id not in rows:
                unknown_ids.add(m.concept_id)  # hallucinated id -> excluded from matrix
                continue
            claimed = disc.get((m.concept_id, cm.city), set())
            agrees = bool(claimed & set(m.source_columns)) if claimed else None
            rows[m.concept_id].cells[cm.city] = MatrixCell(
                source_columns=m.source_columns, semantic_match=m.semantic_match,
                qualifier=m.qualifier or None, transform_needed=m.transform_needed,
                confidence=m.confidence, agrees_with_discovery=agrees,
            )
    for r in rows.values():  # coverage recomputed from ACTUAL mapped cells (not discovery)
        r.n_cells_any = len(r.cells)
        r.n_direct_cells = sum(1 for c in r.cells.values() if c.semantic_match == "direct")
        r.coverage_level = coverage_level(r.n_cells_any)

    checked = [c for r in rows.values() for c in r.cells.values() if c.agrees_with_discovery is not None]
    agree = sum(1 for c in checked if c.agrees_with_discovery)
    coverage_core = [r for r in rows.values() if r.n_cells_any >= 4]
    comparable_core = [r for r in coverage_core if r.comparability_risk != "high"]
    stats = {
        "n_concepts": len(rows),
        "concepts_mapped_ge1": sum(1 for r in rows.values() if r.n_cells_any >= 1),
        "coverage_core_ge4": len(coverage_core),
        "comparable_core_ge4_not_high_risk": len(comparable_core),
        "total_cells": sum(r.n_cells_any for r in rows.values()),
        "discovery_agreement": round(agree / len(checked), 3) if checked else None,
        "discovery_cells_checked": len(checked),
        "unknown_concept_ids_from_agent": sorted(unknown_ids),
        "n_new_concept_candidates": len(candidates),
    }
    return MappingMatrix(
        vocab_version=vocab.get("vocab_version", "?"),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        cities=cities, rows=sorted(rows.values(), key=lambda r: (r.group, r.concept_id)),
        new_concept_candidates=candidates, stats=stats,
    )


_SYM = {"direct": "●", "partial": "◐", "uncertain": "?"}


def render_matrix_markdown(m: MappingMatrix) -> str:
    short = [c[:3] for c in m.cities]
    head = "| concept | risk | cov | " + " | ".join(short) + " | n |"
    sep = "|" + "---|" * (len(m.cities) + 4)
    s = m.stats
    out = [
        f"# Mapping matrix (vocab {m.vocab_version}) — {m.generated_at}",
        f"_cells: ●direct ◐partial ?uncertain · cities: {', '.join(f'{sh}={c}' for sh, c in zip(short, m.cities))}_",
        "",
        f"**Coverage core** (mapped in ≥4 cities, before review): {s['coverage_core_ge4']}/{s['n_concepts']}. "
        f"**Comparable core** (≥4 cities AND not high comparability-risk): {s['comparable_core_ge4_not_high_risk']}. "
        f"Concepts mapped ≥1 city: {s['concepts_mapped_ge1']}.",
        f"_Discovery agreement (fresh mapping vs discovery provenance, {s['discovery_cells_checked']} cells): "
        f"{s['discovery_agreement']} — indicates the vocabulary is stable and the mapping reproducible, "
        f"not that two independent methods converge (same profiles + model). "
        f"New-concept candidates: {s['n_new_concept_candidates']}._",
        "",
    ]
    cur = None
    for r in m.rows:
        if r.group != cur:
            cur = r.group
            out += ["", f"### {cur}", head, sep]
        cells = " | ".join(_SYM.get(r.cells[c].semantic_match, "") if c in r.cells else "·" for c in m.cities)
        out.append(f"| `{r.concept_id}` | {r.comparability_risk} | {r.coverage_level or ''} | {cells} | {r.n_cells_any} |")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
def _cmd_map(args) -> None:
    vocab = load_vocab(args.vocab)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    profiles = sorted(Path(args.profiles).glob("*.json"))
    if args.city:
        profiles = [p for p in profiles if args.city.lower() in p.stem.lower()]
        if not profiles:
            raise SystemExit(f"No profile matches --city {args.city!r}")
    for p in profiles:
        print(f"[map] {p.stem} ...")
        cm = map_city(str(p), vocab, args.model)
        dest = Path(args.out) / f"{p.stem}.json"
        dest.write_text(cm.model_dump_json(indent=2))
        print(f"[map] {cm.city}: {len(cm.mappings)} mapped, {len(cm.unmapped)} unmapped, "
              f"{len(cm.new_concept_candidates)} new-concept candidates -> {dest}")


def _cmd_matrix(args) -> None:
    vocab = load_vocab(args.vocab)
    m = build_matrix(vocab, args.maps)
    Path("data/epc_schema/mapping_matrix.json").write_text(m.model_dump_json(indent=2))
    Path("data/epc_schema/mapping_matrix.review.md").write_text(render_matrix_markdown(m))
    print("[matrix]", json.dumps(m.stats, indent=2))
    print("[matrix] wrote data/epc_schema/mapping_matrix.json + .review.md")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Map city columns to concepts; assemble the matrix.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    mp = sub.add_parser("map", help="Run the per-city mapping agent.")
    mp.add_argument("--profiles", default=PROFILES_DIR)
    mp.add_argument("--vocab", default=VOCAB_PATH)
    mp.add_argument("--out", default=MAP_DIR)
    mp.add_argument("--city", default=None, help="Only map cities whose filename matches this")
    mp.add_argument("--model", default=None)
    mp.set_defaults(func=_cmd_map)

    mx = sub.add_parser("matrix", help="Assemble the concept x city matrix (deterministic).")
    mx.add_argument("--vocab", default=VOCAB_PATH)
    mx.add_argument("--maps", default=MAP_DIR)
    mx.set_defaults(func=_cmd_matrix)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
