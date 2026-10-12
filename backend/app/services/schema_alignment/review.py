"""Models for the Review + Transformation agent (agent 2).

Agent 2 REVIEWS the existing matrix — it never re-maps or re-discovers. Per
concept (seen across all its cities at once) it separates two axes the mapping
agent kept coarse:
  - semantic review: keep / downgrade / flag each cell's correspondence
  - comparability verdict: are the values comparable ACROSS cities (leaning on
    the vocabulary's comparability_risk / note)?
and it authors a machine-readable `TransformRule` per cell that deterministic
code will later execute (the agent writes the rule, never runs it). It also
triages the new-concept candidates for a possible vocab v1.2.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

CELL_VERDICTS = ["keep", "downgrade", "flag"]
COMPARABILITY_VERDICTS = ["comparable", "conditional", "not_comparable"]
TRANSFORM_OPS = [
    "none",              # value usable as-is
    "preserve",          # keep original value verbatim, do NOT normalize (incomparable)
    "unit_convert",      # params: {from, to[, factor]}
    "scale_fix",         # params: {factor} e.g. mm->m is 0.001, or de-scaled ints
    "decimal_normalize", # params: {} — comma/dot cleanup at execution time
    "recategorize",      # params: {mapping: {src_level: canonical_level}}
    "derive",            # params: {formula, inputs:[...]} e.g. total = intensity * floor_area
    "rename",            # pure passthrough to canonical field
]


class TransformRule(BaseModel):
    op: str = Field("none", description=f"One of {TRANSFORM_OPS}")
    params: Dict[str, Any] = Field(default_factory=dict)
    guard: Optional[str] = Field(None, description="Precondition, e.g. 'requires floor_area present'")
    note: Optional[str] = None


class CellReview(BaseModel):
    city: str
    source_columns: List[str] = Field(default_factory=list)
    verdict: str = Field(..., description="keep | downgrade | flag")
    reviewed_semantic_match: str = Field(..., description="direct | partial | uncertain (post-review)")
    reviewed_confidence: str = Field(..., description="high | medium | low (post-review)")
    qualifier: Optional[str] = None
    transform: TransformRule = Field(default_factory=TransformRule)
    reason: str = Field(..., description="Why this verdict / transform")


class ConceptReview(BaseModel):
    concept_id: str
    comparability_verdict: str = Field(..., description=f"One of {COMPARABILITY_VERDICTS}")
    comparability_reason: str
    cells: List[CellReview] = Field(default_factory=list)
    notes: Optional[str] = None


class GroupReview(BaseModel):
    """LLM output for one concept group."""

    reviews: List[ConceptReview] = Field(default_factory=list)


class CandidateDecision(BaseModel):
    city: str
    source_column: str
    suggested_label: str
    decision: str = Field(..., description="promote_new | map_to_existing | city_specific | reject")
    target_concept_id: Optional[str] = Field(None, description="if map_to_existing")
    proposed_concept_id: Optional[str] = Field(None, description="if promote_new")
    reason: str


class TriageResult(BaseModel):
    decisions: List[CandidateDecision] = Field(default_factory=list)
    summary: Optional[str] = None


class ReviewedMatrix(BaseModel):
    """Assembled agent-2 output over all groups."""

    vocab_version: str
    generated_at: str
    model: str
    reviews: List[ConceptReview] = Field(default_factory=list)
    stats: Dict[str, object] = Field(default_factory=dict)
