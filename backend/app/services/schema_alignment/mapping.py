"""Models for the per-city mapping pass and the assembled concept x city matrix.

The Mapping agent (agent 1) maps ONE city's columns onto the frozen canonical
concepts, recording four independent axes: presence (implicit), semantic `match`,
coarse `transform_needed`, and `confidence`. It does NOT author transform rules
or re-judge comparability — that is the Review+Transformation agent (agent 2).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

SEMANTIC_MATCH = ["direct", "partial", "uncertain"]  # correspondence only; NOT how value is produced
# Coarse category only; the exact rule is authored later by the Review agent.
TRANSFORM_NEEDED = [
    "none",              # value usable as-is
    "unit_conversion",   # e.g. mm->m, GBP->EUR, kWh<->MJ
    "value_normalization",  # decimal style, scale fix, string cleanup
    "derivation",        # compute from other column(s), e.g. intensity = total / floor_area
    "recategorization",  # remap categorical levels, e.g. national class bands
]
UNMAPPED_CATEGORIES = [
    "identifier", "address", "administrative", "methodology_provenance",
    "technical_detail", "duplicate", "other",
]


class MappingEntry(BaseModel):
    """One (concept <- source column[s]) mapping for a single city."""

    concept_id: str = Field(..., description="Canonical concept id from the frozen vocabulary")
    source_columns: List[str] = Field(
        ..., description="Original source column name(s); >1 only when the concept is derived"
    )
    semantic_match: str = Field(
        ..., description="direct | partial | uncertain — does the source correspond to the concept? "
        "(NOT how the value is produced; derivation lives in transform_needed)"
    )
    qualifier: Optional[str] = Field(
        None, description="Variant of this source vs the concept (e.g. area basis, energy scope) "
        "— MUST be recorded for lumped concepts like floor_area"
    )
    transform_needed: str = Field(
        "none", description=f"Coarse category, one of {TRANSFORM_NEEDED}; rule authored later"
    )
    confidence: str = Field(..., description="high | medium | low")
    evidence: str = Field(..., description="Why this column maps here (name/units/distribution)")
    notes: Optional[str] = None


class UnmappedColumn(BaseModel):
    column: str
    kind: str = Field(..., description="Inferred kind from the profile")
    category: str = Field(..., description=f"One of {UNMAPPED_CATEGORIES}")
    reason: Optional[str] = None


class NewConceptCandidate(BaseModel):
    """A column that looks like a real EPC concept absent from the vocabulary."""

    column: str
    suggested_label: str
    reason: str


class CityMapping(BaseModel):
    # Stamped deterministically after the call, so not required of the model.
    city: str = ""
    country: str = ""
    language: str = ""
    vocab_version: str = ""
    model: str = ""
    generated_at: str = ""

    n_source_columns: int = 0
    mappings: List[MappingEntry] = Field(default_factory=list)
    unmapped: List[UnmappedColumn] = Field(default_factory=list)
    new_concept_candidates: List[NewConceptCandidate] = Field(default_factory=list)
    summary: Optional[str] = None


# --------------------------------------------------------------------------- #
# Assembled matrix (deterministic, from the per-city CityMapping files)
# --------------------------------------------------------------------------- #
class MatrixCell(BaseModel):
    source_columns: List[str]
    semantic_match: str
    qualifier: Optional[str] = None
    transform_needed: str = "none"
    confidence: str = "medium"
    agrees_with_discovery: Optional[bool] = Field(
        None, description="Did this city's fresh mapping agree with the discovery-stage sources?"
    )


class ConceptRow(BaseModel):
    concept_id: str
    group: str
    comparability_risk: str
    canonical_unit: Optional[str] = None
    cells: Dict[str, MatrixCell] = Field(default_factory=dict, description="city -> cell")
    n_cells_any: int = Field(0, description="cities with any mapping to this concept")
    n_direct_cells: int = Field(0, description="cities whose mapping is a direct match")
    coverage_level: Optional[str] = Field(
        None, description="RECOMPUTED from n_cells_any (1 / 2-3 / >=4), not inherited from discovery"
    )


class MatrixCandidate(BaseModel):
    city: str
    source_column: str
    suggested_label: str
    reason: str


class MappingMatrix(BaseModel):
    vocab_version: str
    generated_at: str
    cities: List[str] = Field(default_factory=list)
    rows: List[ConceptRow] = Field(default_factory=list)
    new_concept_candidates: List[MatrixCandidate] = Field(
        default_factory=list, description="Consolidated across cities, for Agent-2 triage"
    )
    stats: Dict[str, object] = Field(default_factory=dict)
