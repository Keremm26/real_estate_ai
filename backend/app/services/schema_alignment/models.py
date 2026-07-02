"""Pydantic models for deterministic dataset profiling.

These are the *only* artifacts the mapping/review agents see. They must be
rich enough to ground semantic decisions (names, units, distributions, sample
values) yet contain no raw personal data (addresses, holder names, cadastral
refs are redacted to structural masks).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

PROFILING_VERSION = "0.1"


class DetectedClues(BaseModel):
    """Heuristic signals extracted deterministically from a column."""

    unit_from_name: List[str] = Field(
        default_factory=list,
        description="Unit tokens found in the column name, e.g. 'm2', 'kwh', 'co2', '%'",
    )
    unit_from_values: List[str] = Field(
        default_factory=list,
        description="Unit tokens found inline in sample values, e.g. '120 m²' -> 'm²'",
    )
    patterns: List[str] = Field(
        default_factory=list,
        description="Named patterns detected, e.g. 'energy_class_like', 'postal_code', "
        "'year', 'coordinate'",
    )
    language_hint: Optional[str] = Field(
        None, description="Dataset-level language ISO code (e.g. 'nl', 'ca', 'fr')"
    )


class ColumnProfile(BaseModel):
    """Deterministic profile of a single source column."""

    name: str = Field(..., description="Column name as used (deduplicated if needed)")
    original_name: str = Field(..., description="Raw header string as it appears in the CSV")
    position: int = Field(..., description="0-based column index in the source file")
    is_duplicate_name: bool = Field(
        default=False, description="True if this header string appears more than once"
    )

    inferred_kind: str = Field(
        ...,
        description="numeric_continuous | numeric_discrete | categorical | boolean | "
        "datetime | id_like | text | constant | empty",
    )
    raw_dtype: str = Field(default="string", description="Storage dtype at read time")

    n_total: int = Field(..., description="Total rows")
    n_missing: int = Field(..., description="Rows counted as missing/empty")
    missing_ratio: float = Field(..., description="n_missing / n_total")
    n_unique: int = Field(..., description="Distinct non-missing values")
    unique_ratio: float = Field(..., description="n_unique / n_nonmissing")

    numeric_stats: Optional[Dict[str, Any]] = Field(
        None,
        description="min/max/mean/std/quantiles/n_zero/n_negative when numeric",
    )
    decimal_style: Optional[str] = Field(
        None, description="'comma' or 'dot' decimal convention when numeric"
    )

    top_values: Optional[List[Dict[str, Any]]] = Field(
        None, description="Top-k value frequencies (raw strings) for low-cardinality columns"
    )
    sample_values: Optional[List[str]] = Field(
        None, description="A few raw values in original formatting (safe kinds only)"
    )
    masked_samples: Optional[List[str]] = Field(
        None,
        description="Structural masks (9=digit, X=letter) for redacted text/id columns, "
        "revealing format without content",
    )
    value_length: Optional[Dict[str, float]] = Field(
        None, description="min/max/mean character length for text columns"
    )

    detected: DetectedClues = Field(default_factory=DetectedClues)
    flags: List[str] = Field(
        default_factory=list,
        description="e.g. 'constant', 'high_missing', 'probable_id', 'all_missing'",
    )


class DatasetProfile(BaseModel):
    """Deterministic profile of one city's dataset."""

    city: str
    country: str
    language: str = Field(..., description="Primary language ISO code of headers/values")
    source_file: str
    delimiter: str
    encoding: str

    n_rows: int
    n_cols: int
    sampled: bool = Field(default=False, description="True if only a row sample was profiled")
    sample_rows: Optional[int] = None

    profiling_version: str = PROFILING_VERSION
    generated_at: str

    notes: List[str] = Field(default_factory=list)
    columns: List[ColumnProfile] = Field(default_factory=list)
