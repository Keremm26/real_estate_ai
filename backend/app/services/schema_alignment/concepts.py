"""Models for the canonical EPC concept vocabulary (Option C).

The concept-discovery agent reads the cross-city column digest and *proposes*
this vocabulary bottom-up. A human reviews/freezes it; thereafter the per-city
mapping agent maps into the frozen concept ids. Concepts come from the data,
naming is stable, and domain metadata (units, ranges, methodology sensitivity)
lives here rather than in any single city's profile.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

VOCAB_VERSION = "0.1-draft"

CONCEPT_GROUPS = [
    "building_identity",  # type, use, tenure
    "geometry",           # areas, volumes, storeys, dimensions
    "temporal",           # construction year/period, certificate dates
    "energy",             # demand / delivered / primary energy, consumption
    "emissions",          # CO2 and other emissions
    "rating",             # energy class / rating / score
    "systems",            # heating / cooling / DHW / ventilation
    "envelope",           # walls, roof, floor, windows, U-values, insulation
    "location",           # admin geography, postal code, coordinates, climate zone
    "other",
]

MATCH_TYPES = ["direct", "partial", "derived", "uncertain"]  # source->concept correspondence
COMPARABILITY_RISK = ["low", "medium", "high"]  # cross-city value comparability risk
COVERAGE_LEVELS = ["single_city", "limited_cross_city", "cross_city"]


def coverage_level(accepted_city_count: int) -> str:
    """Deterministic coverage tier: 1 / 2-3 / >=4 cities."""
    if accepted_city_count <= 1:
        return "single_city"
    if accepted_city_count <= 3:
        return "limited_cross_city"
    return "cross_city"


class ConceptSource(BaseModel):
    """One city's column offered as evidence for a concept."""

    city: str
    column: str = Field(..., description="Original source column name")
    match: str = Field(..., description="direct | partial | derived | uncertain")
    qualifier: Optional[str] = Field(
        None,
        description="Definition/scope variant of THIS source's values when the concept "
        "lumps variants, e.g. area basis (habitable|heated|useful|cadastral|thermal_zone) "
        "or energy scope (final|primary|delivered|non_renewable|fossil).",
    )
    note: Optional[str] = Field(None, description="Why this column maps here / caveats")


class ConceptCandidate(BaseModel):
    """A proposed canonical cross-city concept with per-city evidence."""

    id: str = Field(..., description="Stable snake_case canonical id, e.g. 'net_floor_area'")
    label: str
    definition: str = Field(..., description="One-sentence semantic definition")
    group: str = Field(..., description=f"One of {CONCEPT_GROUPS}")
    value_kind: str = Field(..., description="numeric | categorical | boolean | datetime | text")
    canonical_unit: Optional[str] = Field(
        None, description="Target unit for numeric concepts, e.g. 'm2', 'kWh/m2/yr'"
    )
    expected_range: Optional[List[float]] = Field(
        None, description="[min, max] soft sanity bound; numeric concepts only (null otherwise)"
    )
    expected_min_year: Optional[int] = Field(
        None, description="Earliest plausible year for date concepts (replaces numeric range)"
    )
    comparability_risk: str = Field(
        default="medium",
        description="low | medium | high — risk that values are NOT comparable across cities "
        "due to differing EPC methodologies/definitions. low = directly comparable "
        "(e.g. postal_code); high = methodology-bound (e.g. energy_class, primary energy).",
    )
    comparability_note: Optional[str] = Field(
        None, description="How/whether values are comparable across cities"
    )
    sources: List[ConceptSource] = Field(
        default_factory=list, description="Accepted supporting columns (provenance, not final mapping)"
    )
    rejected_sources: List[ConceptSource] = Field(
        default_factory=list, description="Columns considered but rejected, with reasons"
    )
    accepted_city_count: Optional[int] = Field(
        None, description="Distinct cities among accepted sources (raw, for stricter views)"
    )
    coverage_level: Optional[str] = Field(
        None, description=f"One of {COVERAGE_LEVELS}, derived from accepted_city_count"
    )
    confidence: str = Field(..., description="high | medium | low")
    notes: Optional[str] = None

    @field_validator("expected_range", mode="before")
    @classmethod
    def _coerce_range(cls, v):
        """Tolerate '0 to 100', '0-100', {min,max}, etc. from the LLM."""
        if v is None or isinstance(v, list):
            return v
        if isinstance(v, dict):
            lo, hi = v.get("min"), v.get("max")
            return [float(lo), float(hi)] if lo is not None and hi is not None else None
        if isinstance(v, str):
            nums = re.findall(r"-?\d+(?:\.\d+)?", v)
            return [float(nums[0]), float(nums[1])] if len(nums) >= 2 else None
        return None

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, v):
        return v or []

    @property
    def n_cities(self) -> int:
        return len({s.city for s in self.sources})


class UnmatchedNote(BaseModel):
    """A recurring column group the agent could not confidently unify."""

    theme: str
    cities: List[str] = Field(default_factory=list)
    reason: str


class DiscoveryResult(BaseModel):
    """Full output of one concept-discovery pass (the draft vocabulary)."""

    concepts: List[ConceptCandidate] = Field(default_factory=list)
    unmatched: List[UnmatchedNote] = Field(
        default_factory=list, description="Recurring columns left unmapped, for human review"
    )
    summary: Optional[str] = None


class ConceptVocabulary(BaseModel):
    """The persisted, human-reviewable vocabulary artifact."""

    vocab_version: str = VOCAB_VERSION
    generated_at: str
    model: str
    n_cities: int
    concepts: List[ConceptCandidate] = Field(default_factory=list)
    unmatched: List[UnmatchedNote] = Field(default_factory=list)
    summary: Optional[str] = None
