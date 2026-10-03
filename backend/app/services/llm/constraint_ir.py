"""
ConstraintIR — a structured, typed representation of the constraints that the
filtering agents extract from a user query.

Why this exists
---------------
Today the pipeline turns each agent's output into a flat text line and loses two
things along the way:
  1. which agent produced which constraint (source is dropped), and
  2. how important the constraint is (no hard / soft distinction).

ConstraintIR keeps every constraint as a typed object that carries its source
agent, its priority (severity), and—important—an explicit record when an agent's
output could NOT be parsed, instead of silently dropping it.

v1 scope (observe-only)
-----------------------
This module is pure data + helpers. It does NOT change the SQL that the pipeline
generates. It is built, logged, and validated against the SQL; nothing here feeds
back into SQL generation in v1.

It has no imports from the graph pipeline, so adding this file cannot affect any
existing behavior.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from typing_extensions import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# Priority of a constraint. Drives behavior in v2 (validation / relaxation),
# but in v1 it is only recorded.
#   hard          -> must be respected; never relaxed or dropped
#   soft          -> a preference; may be relaxed if results are too few
#   ranking_only  -> not a real filter; should only influence ranking (v2)
#   human_review  -> unclear / could not be mapped; surface it, do not guess
Severity = Literal["hard", "soft", "ranking_only", "human_review"]

# Whether the agent output was parsed successfully or not.
ParseStatus = Literal["success", "failed"]

# Two shapes of constraint:
#   predicate -> a normal WHERE condition (column / operator / value)
#   geo       -> a location filter (lat / lon / radius -> haversine)
EntryKind = Literal["predicate", "geo"]


# ---------------------------------------------------------------------------
# Severity fallback rules (used when an agent does not emit its own severity)
# ---------------------------------------------------------------------------

# Building columns that identify or physically define the property. These are
# treated as hard: relaxing them would change *what kind of thing* we search for.
HARD_BUILDING_COLUMNS = {
    "tipologia_bene_immobile",
    "id",
    "codice_comune",
    "foglio",
    "particella",
    "subalterno",
    "number_immobili_per_catasto",
}


def assign_severity(
    source_agent: str,
    target_column: Optional[str],
    requirement: Optional[Dict[str, Any]] = None,
) -> tuple[Severity, bool]:
    """Decide a constraint's severity and whether it can be relaxed.

    Hybrid rule:
      1. If the agent emitted its own ``severity`` in the requirement dict, trust it.
      2. Otherwise fall back to a deterministic rule based on the source agent
         (and, for building, the column).

    Returns:
        (severity, relaxable)

    Note (v1): nothing acts on this yet. It is recorded and logged only.
    """
    requirement = requirement or {}

    # 1. Agent-emitted severity wins (this path is dormant until v1b adds the
    #    field to agent prompts; today agents never emit it, so the fallback runs).
    emitted = requirement.get("severity")
    if emitted in ("hard", "soft", "ranking_only", "human_review"):
        relaxable = requirement.get("relaxable", emitted != "hard")
        return emitted, bool(relaxable)

    # 2. Deterministic fallback by source agent.
    agent = (source_agent or "").lower()

    if agent == "regulatory":
        return "hard", False

    if agent == "building":
        if target_column in HARD_BUILDING_COLUMNS:
            return "hard", False
        # e.g. surface_area thresholds -> a preference
        return "soft", True

    if agent == "energy":
        return "soft", True

    if agent == "location":
        # geo filter: relaxed by widening the radius, not by dropping it
        return "soft", True

    if agent == "proximity":
        # NOTE: proximity currently emits real WHERE filters (e.g. mobility >= 75).
        # Whether to reclassify these as ranking_only is a v2 behavior decision;
        # in v1 we keep them as soft so nothing implies a behavior change.
        return "soft", True

    # Unknown source -> let a human look at it rather than guessing.
    return "human_review", True


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ConstraintEntry(BaseModel):
    """One constraint, or one record of a failed parse.

    A successful entry is either a ``predicate`` (column/operator/value) or a
    ``geo`` filter (lat/lon/radius). A failed entry carries the raw text and the
    reason, so a lost constraint is visible instead of silently gone.
    """

    constraint_id: str = Field(..., description="Stable id, e.g. 'energy_001'")
    source_agent: str = Field(..., description="building|energy|location|proximity|regulatory")
    kind: EntryKind = Field(..., description="predicate | geo")
    parse_status: ParseStatus = Field("success")

    # --- predicate fields ---
    target_column: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    # --- geo fields ---
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_km: Optional[float] = None
    lat_column: Optional[str] = None  # e.g. "latitudine"
    lon_column: Optional[str] = None  # e.g. "longitudine"

    # --- metadata ---
    severity: Severity = "soft"
    relaxable: bool = True
    explicit: bool = Field(True, description="True if the user stated it directly")
    ranking_weight: float = Field(0.0, description="Inherited from the source dimension")
    original_requirement: Optional[Dict[str, Any]] = Field(
        None, description="The raw agent dict this entry came from"
    )

    # --- failure record (only set when parse_status == 'failed') ---
    raw_text_preview: Optional[str] = None
    failure_reason: Optional[str] = None


class ConstraintIR(BaseModel):
    """The full set of constraints for one query.

    ``entries`` holds the constraints we understood. ``failures`` holds the
    agent outputs we could NOT parse — kept on purpose so they are never lost.
    """

    query: str
    entries: List[ConstraintEntry] = Field(default_factory=list)
    failures: List[ConstraintEntry] = Field(default_factory=list)

    # -- small convenience helpers (read-only; no side effects) --

    def predicates(self) -> List[ConstraintEntry]:
        """Entries that are normal WHERE conditions."""
        return [e for e in self.entries if e.kind == "predicate"]

    def geo(self) -> List[ConstraintEntry]:
        """Entries that are location filters."""
        return [e for e in self.entries if e.kind == "geo"]

    def hard(self) -> List[ConstraintEntry]:
        """Entries that must never be relaxed or dropped."""
        return [e for e in self.entries if e.severity == "hard"]

    def has_failures(self) -> bool:
        return len(self.failures) > 0


# ---------------------------------------------------------------------------
# Factory helpers (keep id formatting and defaults in one place)
# ---------------------------------------------------------------------------


def make_predicate_entry(
    *,
    index: int,
    source_agent: str,
    requirement: Dict[str, Any],
    ranking_weight: float = 0.0,
) -> ConstraintEntry:
    """Build a predicate entry from an agent's requirement dict."""
    target_column = requirement.get("target_column")
    severity, relaxable = assign_severity(source_agent, target_column, requirement)
    return ConstraintEntry(
        constraint_id=f"{source_agent}_{index:03d}",
        source_agent=source_agent,
        kind="predicate",
        parse_status="success",
        target_column=target_column,
        operator=requirement.get("operator"),
        value=requirement.get("value"),
        severity=severity,
        relaxable=relaxable,
        ranking_weight=ranking_weight,
        original_requirement=requirement,
    )


def make_geo_entry(
    *,
    index: int,
    lat: float,
    lon: float,
    radius_km: Optional[float] = None,
    lat_column: str = "latitudine",
    lon_column: str = "longitudine",
    ranking_weight: float = 0.0,
) -> ConstraintEntry:
    """Build a geo (location) entry."""
    severity, relaxable = assign_severity("location", None, None)
    return ConstraintEntry(
        constraint_id=f"location_{index:03d}",
        source_agent="location",
        kind="geo",
        parse_status="success",
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        lat_column=lat_column,
        lon_column=lon_column,
        severity=severity,
        relaxable=relaxable,
        ranking_weight=ranking_weight,
    )


def make_failure_entry(
    *,
    index: int,
    source_agent: str,
    raw_text: Optional[str],
    reason: str,
) -> ConstraintEntry:
    """Build a record of an agent output we could not parse.

    This is the Gap 1 fix: instead of dropping a constraint silently, we keep a
    visible record of the loss.
    """
    preview = (raw_text or "")[:300]
    return ConstraintEntry(
        constraint_id=f"{source_agent}_fail_{index:03d}",
        source_agent=source_agent,
        kind="predicate",
        parse_status="failed",
        severity="human_review",
        relaxable=False,
        raw_text_preview=preview,
        failure_reason=reason,
    )


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------

# Source agents that contribute SQL constraints. RankingAgent is intentionally
# absent: it produces weights, not WHERE conditions.
SQL_CONSTRAINT_AGENTS = ("building", "energy", "regulatory", "proximity")


def build_constraint_ir(
    *,
    query: str,
    agent_payloads: List[Dict[str, Any]],
    locations: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> ConstraintIR:
    """Assemble a ConstraintIR from already-parsed agent outputs.

    This function is pure: it takes plain data and returns a ConstraintIR. It does
    not parse JSON, read state, or call the pipeline — the caller does that and
    passes the results in. That keeps this testable and side-effect free.

    Args:
        query: the original user query.
        agent_payloads: one dict per predicate-producing agent, shaped as::

            {
                "source_agent": "building",
                "parsed": {<agent json>} or None,   # None => parse failed
                "raw_text": "<original text>",       # used for failure preview
            }

        locations: list of geo filters, each ``{"lat", "lon", "radius_km"}``.
        weights: per-dimension ranking weights, e.g.
            ``{"building": 0.3, "energy": 0.2, ...}``. A constraint inherits the
            weight of its source agent.

    Returns:
        A populated ConstraintIR. Parse failures go into ``ir.failures`` (never
        dropped); successful constraints go into ``ir.entries``.
    """
    weights = weights or {}
    locations = locations or []

    entries: List[ConstraintEntry] = []
    failures: List[ConstraintEntry] = []

    for payload in agent_payloads:
        source_agent = (payload.get("source_agent") or "unknown").lower()
        parsed = payload.get("parsed")
        raw_text = payload.get("raw_text")
        weight = float(weights.get(source_agent, 0.0))

        # Parse failed upstream -> keep a visible record instead of dropping it.
        if parsed is None or not isinstance(parsed, dict):
            failures.append(
                make_failure_entry(
                    index=len(failures) + 1,
                    source_agent=source_agent,
                    raw_text=raw_text,
                    reason="agent output could not be parsed (None or not a dict)",
                )
            )
            continue

        idx = 0

        # Building typologies become a single predicate on the typology column.
        typologies = parsed.get("typologies") or []
        if source_agent == "building" and typologies:
            idx += 1
            entries.append(
                make_predicate_entry(
                    index=idx,
                    source_agent=source_agent,
                    requirement={
                        "target_column": "tipologia_bene_immobile",
                        "operator": "IN",
                        "value": list(typologies),
                        "description": "Property typologies identified by BuildingAgent",
                    },
                    ranking_weight=weight,
                )
            )

        # Normal requirements -> one predicate each.
        for req in parsed.get("requirements", parsed.get("requisiti", [])) or []:
            if not isinstance(req, dict):
                continue
            idx += 1
            entries.append(
                make_predicate_entry(
                    index=idx,
                    source_agent=source_agent,
                    requirement=req,
                    ranking_weight=weight,
                )
            )

    # Geo filters from the location agent.
    geo_weight = float(weights.get("location", 0.0))
    for i, loc in enumerate(locations, start=1):
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is None or lon is None:
            continue
        entries.append(
            make_geo_entry(
                index=i,
                lat=lat,
                lon=lon,
                radius_km=loc.get("radius_km"),
                ranking_weight=geo_weight,
            )
        )

    return ConstraintIR(query=query, entries=entries, failures=failures)
