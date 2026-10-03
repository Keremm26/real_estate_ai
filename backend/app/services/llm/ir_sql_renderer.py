"""
Deterministic SQL renderer — turns a ConstraintIR into a valid SQL string.

Why this exists (v2)
--------------------
In v1 the ConstraintIR was observe-only: built, validated, logged, but never
used to produce SQL. v2 gives it a job. When the LLM-generated SQL cannot be
trusted (invalid columns that are not alias-fixable, a dropped HARD constraint,
or a query that fails after retry + relaxation), we render SQL directly from the
IR instead.

The render is deterministic: same IR in, same SQL out. It is "faithful by
construction" — it can only emit constraints that are in the IR, using real
dataset column names. That is stated, not claimed as a research win: the point
is robustness (we always get a valid query that respects the must-haves), not
cleverness.

Design
------
This module is pure. It takes a ConstraintIR plus a column resolver and returns
a string. It does not execute SQL, read pipeline state, or import the graph. The
caller decides WHICH entries to include (policy); this file decides only HOW to
render them (mechanics).

Two policies are common and provided as helpers:
  - render everything the IR captured (hard + soft + geo), and
  - render hard-only (maximally permissive: keep must-haves, drop preferences).
The caller can try the first and, if it returns too few rows, fall back to the
second using the existing relaxation machinery.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from app.services.llm.constraint_ir import ConstraintEntry, ConstraintIR
from app.services.llm.constraint_validation import COLUMN_ALIASES, _build_reverse


# The table to select from. The executor exposes ESTATES in BOTH code paths
# (the parquet-native view AND the pandas registration); IMMOBILI exists only in
# the parquet path. ESTATES is therefore the safe, always-available name.
DEFAULT_TABLE = "ESTATES"

# Geo defaults: if a location entry carries no radius, use this many km.
DEFAULT_RADIUS_KM = 3.0
LAT_COLUMN = "latitudine"
LON_COLUMN = "longitudine"

# Comparison operators we render verbatim as ``col OP value``.
_COMPARISON_OPS = {">=", "<=", ">", "<", "=", "==", "!=", "<>"}


class RenderResult(BaseModel):
    """The outcome of rendering an IR: the SQL plus a record of what we did.

    ``skipped`` keeps the renderer honest — every constraint we could NOT render
    (missing value, unknown column, unsupported operator) is recorded with a
    reason instead of silently dropped, mirroring the IR's own failure-record
    philosophy.
    """

    sql: Optional[str] = None
    table: str = DEFAULT_TABLE
    where_clauses: List[str] = Field(default_factory=list)
    rendered_ids: List[str] = Field(default_factory=list)
    skipped: List[Dict[str, str]] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True if nothing renderable was found (no WHERE clauses)."""
        return len(self.where_clauses) == 0


# ---------------------------------------------------------------------------
# Column resolution: IR (agent) names -> real dataset column names
# ---------------------------------------------------------------------------


def make_column_resolver(
    db_schema: Dict[str, Any],
    aliases: Optional[Dict[str, Set[str]]] = None,
) -> Callable[[str], Optional[str]]:
    """Build a resolver mapping an IR column to its real dataset column.

    The IR speaks agent space (often English: ``surface_area``, ``mobility``);
    the dataset uses some Italian names. The renderer must emit names that
    actually exist in the table, so we resolve each IR column against the live
    schema, using the shared alias map to bridge the two naming worlds.

    Returns a function ``resolve(col) -> dataset_col | None``. ``None`` means the
    column cannot be grounded in the schema and the predicate must be skipped
    (we never invent a column name).
    """
    aliases = COLUMN_ALIASES if aliases is None else aliases
    reverse = _build_reverse(aliases)

    types = db_schema.get("types") if isinstance(db_schema, dict) else None
    if isinstance(types, dict):
        valid_cols: Set[str] = set(types.keys())
    elif isinstance(db_schema, dict):
        valid_cols = set(db_schema.keys())
    else:
        valid_cols = set()

    def resolve(col: str) -> Optional[str]:
        if not col:
            return None
        # 0. No schema to check against -> trust the name as given.
        if not valid_cols:
            return col
        # 1. Already a real dataset column.
        if col in valid_cols:
            return col
        # 2. Canonical name whose dataset variant is in the schema.
        canon = reverse.get(col, col)
        for candidate in {canon} | aliases.get(canon, set()):
            if candidate in valid_cols:
                return candidate
        # 3. Unknown -> cannot ground it.
        return None

    return resolve


# ---------------------------------------------------------------------------
# Value / clause rendering (mechanics)
# ---------------------------------------------------------------------------


def _quote(value: Any) -> str:
    """Render a single scalar as a SQL literal (strings quoted + escaped)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # Everything else is treated as text; escape single quotes by doubling.
    return "'" + str(value).replace("'", "''") + "'"


def render_predicate(
    entry: ConstraintEntry,
    resolve_column: Callable[[str], Optional[str]],
) -> tuple[Optional[str], Optional[str]]:
    """Render one predicate entry to a SQL condition.

    Returns ``(clause, skip_reason)``: exactly one is non-None. A skip_reason is
    returned (clause None) when the entry cannot be rendered safely.
    """
    if entry.kind != "predicate":
        return None, "not a predicate"
    if not entry.target_column:
        return None, "missing target_column"

    col = resolve_column(entry.target_column)
    if col is None:
        return None, f"column '{entry.target_column}' not in schema"

    op = (entry.operator or "").strip().upper()
    value = entry.value

    # IN: needs a non-empty list of values.
    if op == "IN":
        values = value if isinstance(value, list) else [value]
        values = [v for v in values if v is not None]
        if not values:
            return None, "IN with no values"
        rendered = ", ".join(_quote(v) for v in values)
        return f"{col} IN ({rendered})", None

    # BETWEEN: needs a two-element [low, high].
    if op == "BETWEEN":
        if isinstance(value, (list, tuple)) and len(value) == 2 and all(
            v is not None for v in value
        ):
            lo, hi = value
            return f"{col} BETWEEN {_quote(lo)} AND {_quote(hi)}", None
        return None, "BETWEEN without a [low, high] value"

    # Plain comparison.
    if op in _COMPARISON_OPS:
        if value is None:
            # e.g. a regulatory min-surface whose threshold is pending docs.
            return None, "comparison with no value"
        sql_op = "=" if op == "==" else op
        return f"{col} {sql_op} {_quote(value)}", None

    return None, f"unsupported operator '{entry.operator}'"


def render_geo(
    entry: ConstraintEntry,
    default_radius_km: float = DEFAULT_RADIUS_KM,
) -> tuple[Optional[str], Optional[str]]:
    """Render one geo entry to a haversine distance condition."""
    if entry.kind != "geo":
        return None, "not a geo entry"
    if entry.lat is None or entry.lon is None:
        return None, "geo entry missing lat/lon"

    lat_col = entry.lat_column or LAT_COLUMN
    lon_col = entry.lon_column or LON_COLUMN
    radius = entry.radius_km if entry.radius_km is not None else default_radius_km
    clause = (
        f"haversine_km({lat_col}, {lon_col}, {_quote(entry.lat)}, {_quote(entry.lon)}) "
        f"<= {_quote(radius)}"
    )
    return clause, None


# ---------------------------------------------------------------------------
# Entry selection (policy helpers)
# ---------------------------------------------------------------------------


def select_entries(ir: ConstraintIR, *, hard_only: bool = False) -> List[ConstraintEntry]:
    """Pick the entries to render.

    Only successfully-parsed entries are eligible (failures never become SQL).
    With ``hard_only`` we keep just the hard predicates plus geo anchors — the
    maximally permissive query that still respects the must-haves.
    """
    chosen: List[ConstraintEntry] = []
    for e in ir.entries:
        if e.parse_status != "success":
            continue
        if e.kind == "geo":
            chosen.append(e)
            continue
        if hard_only and e.severity != "hard":
            continue
        chosen.append(e)
    return chosen


# ---------------------------------------------------------------------------
# Top-level renderer
# ---------------------------------------------------------------------------


def render_sql(
    ir: ConstraintIR,
    *,
    resolve_column: Callable[[str], Optional[str]],
    table: str = DEFAULT_TABLE,
    entries: Optional[List[ConstraintEntry]] = None,
    hard_only: bool = False,
    default_radius_km: float = DEFAULT_RADIUS_KM,
    limit: Optional[int] = None,
) -> RenderResult:
    """Render a ConstraintIR into a deterministic ``SELECT * ... WHERE ...`` query.

    Args:
        ir: the constraints to render.
        resolve_column: maps an IR column to a real dataset column (or None).
            Build one with ``make_column_resolver(db_schema)``.
        table: table/view name to select from.
        entries: explicit entries to render. If None, uses ``select_entries``
            (honoring ``hard_only``).
        hard_only: when ``entries`` is None, render only must-haves + geo.
        default_radius_km: radius used for geo entries that carry none.
        limit: optional row cap appended as ``LIMIT n``.

    Returns:
        A RenderResult. ``sql`` is None only when no entry could be rendered (an
        unconstrained ``SELECT *`` is intentionally NOT emitted — that is the job
        of the existing generic fallback, not of a constraint renderer).
    """
    result = RenderResult(table=table)
    to_render = entries if entries is not None else select_entries(ir, hard_only=hard_only)

    for entry in to_render:
        if entry.kind == "geo":
            clause, reason = render_geo(entry, default_radius_km=default_radius_km)
        else:
            clause, reason = render_predicate(entry, resolve_column)

        if clause is not None:
            result.where_clauses.append(clause)
            result.rendered_ids.append(entry.constraint_id)
        else:
            result.skipped.append(
                {"constraint_id": entry.constraint_id, "reason": reason or "unknown"}
            )

    if result.where_clauses:
        where = " AND ".join(result.where_clauses)
        sql = f"SELECT * FROM {table} WHERE {where}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        result.sql = sql

    return result
