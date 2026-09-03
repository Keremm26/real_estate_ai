"""
Chunk metadata schema for the normative vector store.

Every chunk is one *article* of a regulatory document, tagged with the
metadata that drives the jurisdiction cascade (the router filters on these
fields before semantic search runs). The tier tagging is the thesis-relevant
part: the same rule (e.g. minimum student-room area) lives at a different
jurisdiction level per country, so the level must be a first-class filter.

Chroma constraint: metadata values must be primitive (str/int/float/bool) —
no ``None``, no lists. ``ChunkMetadata.to_chroma()`` flattens accordingly
(lists -> comma-joined strings, ``None`` -> dropped).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JurisdictionLevel(str, Enum):
    NATIONAL = "national"
    REGIONAL = "regional"
    MUNICIPAL = "municipal"


class DocType(str, Enum):
    BUILDING = "building"          # building code / habitability (edilizia)
    PLANNING = "planning"          # land-use / zoning (urbanistica)
    HOUSING = "housing"            # housing / licensing rules
    ACCESSIBILITY = "accessibility"


class UseCase(str, Enum):
    STUDENT_HOUSING = "student_housing"
    GENERAL = "general"            # national baselines that apply across uses


class ChunkMetadata(BaseModel):
    """Metadata attached to a single article-level chunk."""

    # --- cascade keys (filtered on before semantic search) ---
    country: str                                   # ISO-ish: IT / ES / UK
    jurisdiction_level: JurisdictionLevel
    use_case: UseCase = UseCase.GENERAL
    doc_type: DocType = DocType.BUILDING

    # --- provenance ---
    doc_name: str                                  # e.g. "D.M. Sanità 5 luglio 1975"
    article_ref: str                               # e.g. "Art. 24"
    source_url: str
    lang: str = "it"
    city: Optional[str] = None
    effective_date: Optional[str] = None

    # --- signals ---
    has_quantitative: bool = False                 # contains numeric thresholds

    # --- dormant graph edges (populated cheaply; used only if 1-hop
    #     expansion is added later — kept out of the cascade for now) ---
    amends: Optional[List[str]] = None
    superseded_by: Optional[str] = None
    cross_references: Optional[List[str]] = None

    def to_chroma(self) -> Dict[str, Any]:
        """Flatten to Chroma-safe primitives (no None, no lists)."""
        out: Dict[str, Any] = {
            "country": self.country,
            "jurisdiction_level": self.jurisdiction_level.value,
            "use_case": self.use_case.value,
            "doc_type": self.doc_type.value,
            "doc_name": self.doc_name,
            "article_ref": self.article_ref,
            "source_url": self.source_url,
            "lang": self.lang,
            "has_quantitative": self.has_quantitative,
        }
        if self.city:
            out["city"] = self.city
        if self.effective_date:
            out["effective_date"] = self.effective_date
        if self.amends:
            out["amends"] = ",".join(self.amends)
        if self.superseded_by:
            out["superseded_by"] = self.superseded_by
        if self.cross_references:
            out["cross_references"] = ",".join(self.cross_references)
        return out

    def chunk_id(self) -> str:
        """Stable id for upserts: re-ingesting the same article overwrites it."""
        slug = f"{self.country}|{self.doc_name}|{self.article_ref}"
        return slug.lower().replace(" ", "_")
