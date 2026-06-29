"""
Report-only validation of generated SQL against the ConstraintIR and the live
schema / metadata.

What this does
--------------
It answers questions like:
  - Does the SQL use columns that actually exist in the dataset?
  - Are the categorical values it filters on real values?
  - Did every HARD constraint survive into the SQL?
  - Did the SQL add conditions that no agent asked for (zero-addition)?
  - Was the location filter / typology kept?

v1 scope (observe-only)
-----------------------
These functions ONLY produce a report. They never change or block the SQL. The
report is meant to be logged so we can measure how often today's pipeline gets
these things wrong (the "before" picture).

Pure functions: they take data in and return a report. No pipeline imports, no
side effects.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

import sqlglot
from sqlglot import exp

from app.services.llm.constraint_ir import ConstraintIR


TYPOLOGY_COLUMN = "tipologia_bene_immobile"
GEO_COLUMNS = {"latitudine", "longitudine"}
KNOWN_FUNCTIONS = {"haversine_km"}

# Agents speak canonical (mostly English) column names; the dataset uses some
# Italian names. The SQLAgent translates between them, so the IR (agent space)
# and the SQL (dataset space) can name the SAME column differently. We compare in
# a shared "canonical" space so that mismatch is not flagged as an error.
#
# Sourced from the repo's own alias lists (real_estate_service.py safe_get
# alternatives). Each key is the canonical name; the set holds dataset variants.
COLUMN_ALIASES: Dict[str, Set[str]] = {
    "surface_area": {"superficie_di_riferimento_mq"},
    "energy_class": {"classe_energetica_ape"},
    "mobility": {"mobilita", "mobilità"},
    "commercial": {"commerciale"},
    "healthcare": {"sanita", "sanità"},
    "green": {"greenery", "verde"},
    "education": {"educazione"},
    "property_type": {"tipologia_bene_immobile"},
    "cadastral_units_count": {"numero_immobili_per_catasto"},
}


def _build_reverse(aliases: Dict[str, Set[str]]) -> Dict[str, str]:
    """Map every name (canonical or variant) to its canonical name."""
    reverse: Dict[str, str] = {}
    for canon, variants in aliases.items():
        reverse[canon] = canon
        for v in variants:
            reverse[v] = canon
    return reverse


def _canonical(col: str, reverse: Dict[str, str]) -> str:
    """Return the canonical name for a column (unchanged if unknown)."""
    return reverse.get(col, col)


def _alias_group(col: str, aliases: Dict[str, Set[str]], reverse: Dict[str, str]) -> Set[str]:
    """All names that refer to the same column as ``col`` (incl. itself)."""
    canon = reverse.get(col, col)
    return {canon} | aliases.get(canon, set())


def _canon_set(cols: Set[str], reverse: Dict[str, str]) -> Set[str]:
    """Canonicalize a set of column names."""
    return {_canonical(c, reverse) for c in cols}


class ValidationReport(BaseModel):
    """The result of checking one SQL query against its ConstraintIR + schema."""

    is_valid: bool = True
    sql_parse_ok: bool = True

    invalid_columns: List[str] = Field(default_factory=list)
    invalid_categorical_values: List[Dict[str, Any]] = Field(default_factory=list)

    missing_constraints: List[str] = Field(default_factory=list)
    extra_conditions: List[str] = Field(default_factory=list)
    zero_addition_violations: List[str] = Field(default_factory=list)

    hard_constraints_preserved: bool = True
    typology_preserved: bool = True
    location_filter_preserved: bool = True

    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Small SQL helpers (sqlglot, DuckDB dialect)
# ---------------------------------------------------------------------------


def _parse_sql(sql: str) -> Optional[exp.Expression]:
    """Parse SQL with the DuckDB dialect.

    Returns None if it cannot be parsed OR if the result is not a SELECT query.
    sqlglot is lenient and will turn random text into some expression tree, so we
    require an actual SELECT to count the parse as successful (this pipeline only
    ever produces SELECTs).
    """
    if not sql or not sql.strip():
        return None
    try:
        expr = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        return None
    if expr is None:
        return None
    if isinstance(expr, exp.Select) or expr.find(exp.Select) is not None:
        return expr
    return None


def _referenced_columns(expr: exp.Expression) -> Set[str]:
    """Every column name referenced anywhere in the statement."""
    return {c.name for c in expr.find_all(exp.Column)}


def _where_columns(expr: exp.Expression) -> Set[str]:
    """Column names that appear inside the WHERE clause."""
    where = expr.find(exp.Where)
    if not where:
        return set()
    return {c.name for c in where.find_all(exp.Column)}


def _schema_columns(db_schema: Dict[str, Any]) -> Set[str]:
    """Pull the set of valid column names from the schema dict.

    Accepts either ``{"types": {col: dtype}}`` (the shape the pipeline uses) or a
    plain ``{col: dtype}`` mapping.
    """
    types = db_schema.get("types") if isinstance(db_schema, dict) else None
    if isinstance(types, dict):
        return set(types.keys())
    if isinstance(db_schema, dict):
        return set(db_schema.keys())
    return set()


def _allowed_values(db_metadata: Dict[str, Any], column: str) -> Optional[List[str]]:
    """Return the allowed categorical values for a column, if metadata has them."""
    fields = (db_metadata or {}).get("fields", {})
    meta = fields.get(column)
    if isinstance(meta, dict) and isinstance(meta.get("values"), list):
        return [str(v) for v in meta["values"]]
    return None


# ---------------------------------------------------------------------------
# Schema grounding: do columns and values actually exist?
# ---------------------------------------------------------------------------


def validate_schema_grounding(
    ir: ConstraintIR,
    sql: str,
    db_schema: Dict[str, Any],
    db_metadata: Dict[str, Any],
    column_aliases: Optional[Dict[str, Set[str]]] = None,
) -> ValidationReport:
    """Check that the SQL (and the IR) reference real columns and valid values."""
    report = ValidationReport()
    valid_cols = _schema_columns(db_schema)
    aliases = COLUMN_ALIASES if column_aliases is None else column_aliases
    reverse = _build_reverse(aliases)

    def _exists_in_schema(col: str) -> bool:
        # A column exists if it, or any of its alias siblings, is in the schema.
        return bool(_alias_group(col, aliases, reverse) & valid_cols)

    expr = _parse_sql(sql)
    if expr is None:
        report.sql_parse_ok = False
        report.is_valid = False
        report.warnings.append("SQL could not be parsed; column checks skipped.")
    else:
        # Columns used in the SQL that do not exist in the schema (alias-aware).
        referenced = _referenced_columns(expr)
        if valid_cols:
            report.invalid_columns = sorted(c for c in referenced if not _exists_in_schema(c))

    # IR predicate columns that do not exist in the schema (warning, not fatal).
    if valid_cols:
        for e in ir.predicates():
            if e.target_column and not _exists_in_schema(e.target_column):
                report.warnings.append(
                    f"IR column '{e.target_column}' (from {e.source_agent}) not in schema."
                )

    # Categorical value checks: IR values that are not allowed values.
    for e in ir.predicates():
        if not e.target_column:
            continue
        allowed = _allowed_values(db_metadata, e.target_column)
        if allowed is None:
            continue
        values = e.value if isinstance(e.value, list) else [e.value]
        for v in values:
            if v is None:
                continue
            if str(v) not in allowed:
                report.invalid_categorical_values.append(
                    {"column": e.target_column, "value": v, "source_agent": e.source_agent}
                )

    if report.invalid_columns or report.invalid_categorical_values:
        report.is_valid = False
    return report


# ---------------------------------------------------------------------------
# Semantic faithfulness: does the SQL match what the agents asked for?
# ---------------------------------------------------------------------------


def validate_sql_semantics(
    sql: str,
    ir: ConstraintIR,
    db_schema: Dict[str, Any],
    db_metadata: Dict[str, Any],
    column_aliases: Optional[Dict[str, Set[str]]] = None,
) -> ValidationReport:
    """Check that the SQL faithfully reflects the ConstraintIR.

    Works at the column level (robust): a hard constraint is "preserved" if its
    column appears in the WHERE clause. Comparison happens in a shared canonical
    space so agent vs dataset column names (e.g. surface_area vs
    superficie_di_riferimento_mq) are treated as the same column.
    """
    aliases = COLUMN_ALIASES if column_aliases is None else column_aliases
    reverse = _build_reverse(aliases)

    # Start from the schema-grounding report so one report carries everything.
    report = validate_schema_grounding(ir, sql, db_schema, db_metadata, column_aliases=aliases)

    typology_canon = _canonical(TYPOLOGY_COLUMN, reverse)

    expr = _parse_sql(sql)
    if expr is None:
        # Already flagged in schema grounding; nothing more we can check.
        report.hard_constraints_preserved = False
        report.location_filter_preserved = len(ir.geo()) == 0
        report.typology_preserved = not any(
            _canonical(e.target_column or "", reverse) == typology_canon
            for e in ir.predicates()
        )
        report.is_valid = False
        return report

    # Everything compared in canonical space.
    where_cols = _canon_set(_where_columns(expr), reverse)
    geo_canon = _canon_set(GEO_COLUMNS, reverse)
    sql_lower = sql.lower()

    # Columns the IR accounts for (so anything else in WHERE is "extra").
    ir_predicate_cols = {
        _canonical(e.target_column, reverse) for e in ir.predicates() if e.target_column
    }
    covered_cols = set(ir_predicate_cols) | geo_canon
    for e in ir.geo():
        if e.lat_column:
            covered_cols.add(_canonical(e.lat_column, reverse))
        if e.lon_column:
            covered_cols.add(_canonical(e.lon_column, reverse))

    # 1. Hard constraints: each hard predicate's column must be in the WHERE.
    missing = []
    for e in ir.hard():
        if e.kind != "predicate" or not e.target_column:
            continue
        if _canonical(e.target_column, reverse) not in where_cols:
            missing.append(f"{e.source_agent}:{e.target_column} {e.operator} {e.value}")
    report.missing_constraints = missing
    report.hard_constraints_preserved = len(missing) == 0

    # 2. Typology preserved (if the IR has a typology constraint).
    has_typology = any(
        _canonical(e.target_column or "", reverse) == typology_canon for e in ir.predicates()
    )
    if has_typology:
        report.typology_preserved = typology_canon in where_cols

    # 3. Location filter preserved (if the IR has a geo constraint).
    if ir.geo():
        has_haversine = "haversine_km" in sql_lower
        has_geo_cols = bool(geo_canon & where_cols)
        report.location_filter_preserved = has_haversine or has_geo_cols

    # 4. Zero-addition: WHERE columns that no IR entry asked for.
    extra = sorted(c for c in where_cols if c not in covered_cols)
    report.extra_conditions = extra
    report.zero_addition_violations = extra

    # Overall verdict.
    report.is_valid = (
        report.sql_parse_ok
        and not report.invalid_columns
        and report.hard_constraints_preserved
        and report.typology_preserved
        and report.location_filter_preserved
    )
    return report
