"""EPC concept-vocabulary builder — the full discover -> refine -> freeze lifecycle.

Two steps, two subcommands:
  discover  reads the deterministic cross-city column digest and asks the LLM to
            propose canonical concepts bottom-up, conservatively, with per-city
            evidence -> writes concept_vocabulary.draft.json (Option C).
  refine    enriches a FROZEN draft in place (never re-discovers, so ids stay
            stable): grades comparability_risk, splits rejected sources, derives
            coverage_level, cleans date ranges -> writes concept_vocabulary.<ver>.json.

Run:
    uv run python -m app.services.schema_alignment.concept_agent discover
    uv run python -m app.services.schema_alignment.concept_agent refine --version 1.1
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.config import AGENT_MODELS
from app.services.llm.langchain_client import get_llm, is_oss_model
from app.utils.json_parser import safe_extract_json

try:
    from .concepts import (
        CONCEPT_GROUPS, ConceptCandidate, ConceptSource, ConceptVocabulary,
        DiscoveryResult, UnmatchedNote, coverage_level,
    )
    from .digest import build_digest, digest_stats, render_digest_text
except ImportError:  # pragma: no cover - script fallback
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from concepts import (  # type: ignore
        CONCEPT_GROUPS, ConceptCandidate, ConceptSource, ConceptVocabulary,
        DiscoveryResult, UnmatchedNote, coverage_level,
    )
    from digest import build_digest, digest_stats, render_digest_text  # type: ignore


SYSTEM_PROMPT = f"""You are a senior building-energy data analyst constructing a \
CROSS-CITY canonical concept vocabulary for residential EPC/APE (Energy \
Performance Certificate) datasets from different European countries.

You are given, per city, a deterministic digest of its columns: name, inferred \
type, missing %, cardinality, unit clues, and value distributions / sample \
values. Column names are in the local language (Dutch, Catalan, French, \
Portuguese, Spanish, English).

YOUR TASK: discover the canonical concepts that recur ACROSS cities and map each \
city's source columns to them. The vocabulary must emerge from the actual \
columns — do NOT invent concepts that are not present, and do NOT copy any \
single city's schema.

RULES — be conservative and evidence-driven:
- Propose a concept when the SAME underlying quantity appears in >= 2 cities. \
Prefer concepts spanning many cities. You may include a clearly-core EPC concept \
present in only 1 city, but mark it accordingly.
- Give each concept a stable snake_case English id (e.g. net_floor_area, \
primary_energy_demand, energy_class, co2_emissions, construction_year).
- group must be one of: {CONCEPT_GROUPS}.
- For every source column, assign a match type:
  * direct   — same quantity, same/convertible unit
  * partial  — related but narrower/broader or definitionally different
  * derived  — obtainable only by computing from other columns
  * uncertain— plausible but weak evidence
- Do NOT force a mapping when evidence is weak; leave such columns out and list \
recurring un-unified ones under "unmatched". If you judge a column is not a genuine \
match, do NOT list it as a source at all.
- ONE CONCEPT + QUALIFIER over splitting: when cities express the SAME concept under \
different definitions that SHARE THE SAME UNIT (floor area as habitable/heated/useful/ \
cadastral/thermal_zone, all in m2), keep a single canonical concept and record each \
source's variant in its `qualifier` field — do not create near-duplicate concepts or \
assert false equivalence. Set comparability_risk high and explain in comparability_note.
- UNIT RULE (overrides the above): every concept has EXACTLY ONE canonical_unit. When \
variants differ in UNIT they are SEPARATE concepts — never merge them under one qualifier. \
In particular, absolute totals (kWh/year, kgCO2/year) and per-floor-area intensities \
(kWh/m2.year, kgCO2/m2.year) are ALWAYS distinct concepts (e.g. final_energy_consumption \
vs final_energy_intensity; co2_emissions_total vs co2_emissions_intensity). Per-area \
intensity is the primary cross-city-comparable form — always expose it as its own concept \
whenever any city reports it. (Energy carrier scope final/primary/delivered/non_renewable \
also remain separate concepts, as they already are.)
- A concept may span only 1 city if it is a clearly-core EPC concept; keep it but its \
evidence will show n_cities=1.
- CRITICAL — comparability != name match. Assign comparability_risk (low|medium|high) \
per concept: low = directly comparable across countries (ids, postal codes, coordinates, \
dates, counts, construction year); medium = comparable after a definable transform or with \
a caveat (areas with differing definitions, costs, storeys, generic system categories); \
high = bound to national EPC methodology (energy class/rating scales, primary/final energy \
and intensities, CO2 metrics, demand indicators, climate zones). Energy CLASS / RATING \
differ by country (asset vs operational, A–G vs numeric BER) → always high. Explain limits \
in comparability_note.
- Split sources you accept from ones you considered-but-rejected: put genuine supporting \
columns in `sources` and rejected ones in `rejected_sources` (each with a reason).
- Assign confidence (high/medium/low) per concept based on strength and \
consistency of evidence across cities.
- Fill canonical_unit and a soft expected_range for numeric concepts (null for non-numeric).

Return STRICT JSON matching the requested schema. No prose outside JSON."""

USER_TEMPLATE = """Here are the per-city column digests for {n_cities} cities \
({n_cols} mappable columns total):
{digest}

Produce the draft canonical concept vocabulary as JSON with keys:
- "concepts": list of concepts, each with: id, label, definition, group, \
value_kind, canonical_unit, expected_range, comparability_risk, comparability_note, \
sources (list of {{city, column, match, qualifier, note}}), rejected_sources (same \
shape, for considered-but-rejected columns), confidence, notes.
- "unmatched": list of {{theme, cities, reason}} for recurring columns you could \
not confidently unify.
- "summary": 2-3 sentences on coverage and the main comparability caveats.
"""


def discover_vocabulary(profiles_dir: str = "data/epc_profiles",
                        model_name: str | None = None) -> tuple[ConceptVocabulary, dict]:
    digest = build_digest(profiles_dir)
    stats = digest_stats(digest)
    digest_text = render_digest_text(digest)

    resolved_model = model_name or AGENT_MODELS.get("default")
    llm = get_llm(model_name=resolved_model)

    user = USER_TEMPLATE.format(
        n_cities=stats["cities"], n_cols=stats["mappable_columns"], digest=digest_text
    )
    messages = [("system", SYSTEM_PROMPT), ("user", user)]

    result: DiscoveryResult | None = None
    if hasattr(llm, "with_structured_output") and not is_oss_model(resolved_model):
        try:
            result = llm.with_structured_output(DiscoveryResult, method="json_mode").invoke(messages)
        except Exception as e:  # noqa: BLE001 - fall back to manual parse
            print(f"[concept-agent] structured output failed ({e}); falling back to text parse")

    if result is None:
        raw = llm.invoke(messages)
        text = raw.content if hasattr(raw, "content") else str(raw)
        result = safe_extract_json(text, schema=DiscoveryResult)
    if result is None:
        raise RuntimeError("Concept-discovery agent returned unparseable output")

    vocab = ConceptVocabulary(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=resolved_model,
        n_cities=stats["cities"],
        concepts=result.concepts,
        unmatched=result.unmatched,
        summary=result.summary,
    )
    return vocab, stats


def render_review_markdown(vocab: ConceptVocabulary) -> str:
    lines = [
        f"# Draft EPC concept vocabulary ({vocab.vocab_version})",
        f"_model: {vocab.model} · {vocab.n_cities} cities · "
        f"{len(vocab.concepts)} concepts · generated {vocab.generated_at}_",
        "",
        f"**Summary:** {vocab.summary or '—'}",
        "",
    ]
    by_group: dict[str, list] = {}
    for c in vocab.concepts:
        by_group.setdefault(c.group, []).append(c)

    for group in sorted(by_group, key=lambda g: (-len(by_group[g]), g)):
        lines.append(f"## {group}")
        for c in sorted(by_group[group], key=lambda x: -x.n_cities):
            risk = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(c.comparability_risk, "")
            unit = f" · unit=`{c.canonical_unit}`" if c.canonical_unit else ""
            cov = f" · {c.coverage_level}" if c.coverage_level else ""
            lines.append(
                f"\n### `{c.id}` — {c.label}  ({c.n_cities}/{vocab.n_cities} cities{cov}, "
                f"conf={c.confidence}{unit} · risk={risk}{c.comparability_risk})"
            )
            lines.append(f"{c.definition}")
            if c.comparability_note:
                lines.append(f"- _comparability_: {c.comparability_note}")
            for s in sorted(c.sources, key=lambda x: x.city):
                qual = f" _({s.qualifier})_" if s.qualifier else ""
                note = f" — {s.note}" if s.note else ""
                lines.append(f"  - **{s.city}**: `{s.column}` [{s.match}]{qual}{note}")
            for s in sorted(c.rejected_sources, key=lambda x: x.city):
                note = f" — {s.note}" if s.note else ""
                lines.append(f"  - ~~{s.city}: `{s.column}`~~ [rejected]{note}")
        lines.append("")

    if vocab.unmatched:
        lines.append("## ⚠️ Unmatched / needs human decision")
        for u in vocab.unmatched:
            lines.append(f"- **{u.theme}** ({', '.join(u.cities)}): {u.reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# refine: enrich a frozen draft in place (v1.0 -> v1.1). Operates on the FROZEN
# concept set — never re-discovers, so ids/definitions stay stable.
# --------------------------------------------------------------------------- #
_REJECT_CUES = (
    "not mapped", "retained? no", "not a genuine", "not actually",
    "not a true", "weak directness", "do not map", "not a real match",
)
_NUMERIC_KINDS = {"numeric"}
_DATE_KINDS = {"date", "datetime"}


class _RiskItem(BaseModel):
    id: str
    comparability_risk: str = Field(..., description="low | medium | high")


class _RiskResult(BaseModel):
    assignments: List[_RiskItem] = Field(default_factory=list)


_RISK_SYSTEM = """You grade CROSS-CITY value comparability risk for canonical EPC \
concepts. For each concept decide comparability_risk:
- low: directly comparable across countries with at most trivial normalization \
(identifiers, postal codes, coordinates, dates, counts, simple presence booleans, \
construction year).
- medium: comparable after a definable transform or with a known caveat (areas with \
differing definitions, costs/currency, storeys, generic system-type categories, efficiencies).
- high: bound to national EPC methodology and NOT comparable without alignment (energy \
classes/ratings, primary/final/delivered energy and intensities, CO2 metrics, demand \
indicators, climate zones, methodology-defined indices).
Return JSON {"assignments": [{"id": "...", "comparability_risk": "low|medium|high"}]} \
for EVERY concept id given."""


def _is_rejected(src: dict) -> bool:
    note = (src.get("note") or "").lower()
    return any(cue in note for cue in _REJECT_CUES)


def _split_sources(raw_sources: list[dict]) -> tuple[list[dict], list[dict]]:
    accepted, rejected = [], []
    for s in raw_sources:
        (rejected if _is_rejected(s) else accepted).append(s)
    return accepted, rejected


def _clean_ranges(kind: str, expected_range) -> tuple[Optional[list], Optional[int]]:
    """Keep numeric ranges; convert date ranges to expected_min_year; else null."""
    if kind in _NUMERIC_KINDS and isinstance(expected_range, list) and len(expected_range) == 2:
        lo, hi = expected_range
        if hi is not None and hi < lo:  # sentinel like [2000, -1] -> open-ended
            return [lo, None], None
        return [lo, hi], None
    if kind in _DATE_KINDS and isinstance(expected_range, list) and expected_range:
        yr = expected_range[0]
        return None, int(yr) if isinstance(yr, (int, float)) and yr > 1500 else None
    return None, None


def _grade_risk(concepts: list[dict], model_name: str | None) -> dict[str, str]:
    payload = [
        {"id": c["id"], "group": c["group"], "definition": c["definition"],
         "canonical_unit": c.get("canonical_unit"),
         "was_methodology_sensitive": c.get("methodology_sensitive"),
         "comparability_note": c.get("comparability_note")}
        for c in concepts
    ]
    resolved = model_name or AGENT_MODELS.get("default")
    llm = get_llm(model_name=resolved)
    messages = [("system", _RISK_SYSTEM),
                ("user", "Grade every concept:\n" + json.dumps(payload, ensure_ascii=False))]

    result = None
    if hasattr(llm, "with_structured_output") and not is_oss_model(resolved):
        try:
            result = llm.with_structured_output(_RiskResult, method="json_mode").invoke(messages)
        except Exception as e:  # noqa: BLE001
            print(f"[refine] structured risk pass failed ({e}); using boolean prior")

    risks: dict[str, str] = {}
    if result:
        risks = {a.id: a.comparability_risk for a in result.assignments
                 if a.comparability_risk in {"low", "medium", "high"}}
    for c in concepts:  # fallback for any missing id
        risks.setdefault(c["id"], "high" if c.get("methodology_sensitive") else "medium")
    return risks


def refine_vocabulary(in_path: str, version: str, model_name: str | None = None) -> ConceptVocabulary:
    raw = json.loads(Path(in_path).read_text())
    concepts_raw = raw["concepts"]
    print(f"[refine] grading comparability_risk for {len(concepts_raw)} frozen concepts ...")
    risks = _grade_risk(concepts_raw, model_name)

    out_concepts: list[ConceptCandidate] = []
    for c in concepts_raw:
        accepted, rejected = _split_sources(c.get("sources", []))
        acc_cities = len({s["city"] for s in accepted})
        er, min_year = _clean_ranges(c.get("value_kind", ""), c.get("expected_range"))
        out_concepts.append(ConceptCandidate(
            id=c["id"], label=c["label"], definition=c["definition"], group=c["group"],
            value_kind=c["value_kind"], canonical_unit=c.get("canonical_unit"),
            expected_range=er, expected_min_year=min_year,
            comparability_risk=risks[c["id"]], comparability_note=c.get("comparability_note"),
            sources=[ConceptSource(**s) for s in accepted],
            rejected_sources=[ConceptSource(**s) for s in rejected],
            accepted_city_count=acc_cities, coverage_level=coverage_level(acc_cities),
            confidence=c["confidence"], notes=c.get("notes"),
        ))
    return ConceptVocabulary(
        vocab_version=version,
        generated_at=raw.get("generated_at", datetime.now().isoformat(timespec="seconds")),
        model=raw.get("model", "unknown"), n_cities=raw.get("n_cities", 8),
        concepts=out_concepts,
        unmatched=[UnmatchedNote(**u) for u in raw.get("unmatched", [])],
        summary=raw.get("summary"),
    )


# --------------------------------------------------------------------------- #
def _cmd_discover(args) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print("[discover] building digest and calling model ...")
    vocab, _ = discover_vocabulary(args.profiles, args.model)

    json_path = out_dir / "concept_vocabulary.draft.json"
    json_path.write_text(vocab.model_dump_json(indent=2))
    (out_dir / "concept_vocabulary.draft.review.md").write_text(render_review_markdown(vocab))
    n_cross = sum(1 for c in vocab.concepts if c.n_cities >= 2)
    print(f"[discover] {len(vocab.concepts)} concepts ({n_cross} cross-city); wrote {json_path}")


def _cmd_refine(args) -> None:
    vocab = refine_vocabulary(args.inp, args.version, args.model)
    out = vocab.model_dump()
    out["frozen_at"] = datetime.now().isoformat(timespec="seconds")
    out["coverage_thresholds"] = {"single_city": 1, "limited_cross_city": "2-3", "cross_city": ">=4"}
    out_json = Path(args.out) / f"concept_vocabulary.v{args.version}.json"
    out_md = Path(args.out) / f"concept_vocabulary.v{args.version}.review.md"
    out_json.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    out_md.write_text(render_review_markdown(vocab))

    from collections import Counter
    print(f"[refine] wrote {out_json} (v{args.version})")
    print(f"  comparability_risk: {dict(Counter(c.comparability_risk for c in vocab.concepts))}")
    print(f"  coverage_level:     {dict(Counter(c.coverage_level for c in vocab.concepts))}")
    print(f"  rejected_sources moved out: {sum(len(c.rejected_sources) for c in vocab.concepts)}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Build/refine the EPC concept vocabulary.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="Discover the draft vocabulary from profiles.")
    d.add_argument("--profiles", default="data/epc_profiles")
    d.add_argument("--out", default="data/epc_schema")
    d.add_argument("--model", default=None)
    d.set_defaults(func=_cmd_discover)

    r = sub.add_parser("refine", help="Enrich a frozen draft (adds risk/coverage/rejected).")
    r.add_argument("--in", dest="inp", default="data/epc_schema/concept_vocabulary.draft.json")
    r.add_argument("--out", default="data/epc_schema")
    r.add_argument("--version", default="1.1")
    r.add_argument("--model", default=None)
    r.set_defaults(func=_cmd_refine)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
