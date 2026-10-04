"""
Safe column-alias repair for LLM-generated SQL (v2).

The problem
-----------
The agents speak English column names (``surface_area``, ``mobility``); the
dataset uses some Italian names (``superficie_di_riferimento_mq``, ``mobilita``).
The SQLAgent is supposed to translate, but sometimes it leaves an English name in
the WHERE clause. The semantic validator treats that as "exists" (the alias group
hits the schema), so it does not complain — but at execution DuckDB sees the
literal ``surface_area``, which is not a real column, and the query fails. This is
the exact ``surface_area not found`` error observed in v1.

What this does
--------------
Rename only SQL columns that (a) are not literally in the schema but (b) resolve,
via the shared alias map, to a column that IS in the schema. Everything else is
left untouched:
  - a column already in the schema -> no change,
  - a column that does not resolve to any real column -> left alone (it is a
    genuine invalid column; the caller routes that case to deterministic
    fallback, not to repair).

Why sqlglot, not string replace
--------------------------------
We rewrite the parsed tree's Column identifiers, so a name that also appears
inside a string literal (e.g. ``WHERE note = 'surface_area note'``) is never
touched. Repair changes column references only, never logic or values.

This module is pure: SQL string in, repaired SQL string + a record of what
changed out. No pipeline imports, no execution.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

import sqlglot
from sqlglot import exp

from app.services.llm.constraint_validation import (
    COLUMN_ALIASES,
    _build_reverse,
    is_query_local,
    query_local_names,
)
from app.services.llm.ir_sql_renderer import is_text_type


class RepairResult(BaseModel):
    """Outcome of an alias-repair pass."""

    sql: str
    changed: bool = False
    # Each repair: {"from": "surface_area", "to": "superficie_di_riferimento_mq"}
    repairs: List[Dict[str, str]] = Field(default_factory=list)
    # Columns we could NOT ground in the schema (genuine invalids -> fallback).
    unresolved_columns: List[str] = Field(default_factory=list)


def _schema_columns(db_schema: Dict[str, Any]) -> Set[str]:
    types = db_schema.get("types") if isinstance(db_schema, dict) else None
    if isinstance(types, dict):
        return set(types.keys())
    if isinstance(db_schema, dict):
        return set(db_schema.keys())
    return set()


def _resolve_to_schema(
    col: str,
    valid_cols: Set[str],
    aliases: Dict[str, Set[str]],
    reverse: Dict[str, str],
) -> Optional[str]:
    """Return the real dataset column for ``col``, or None if not groundable.

    A column already in the schema resolves to itself. Otherwise we look through
    its alias group for a name that is in the schema.
    """
    if col in valid_cols:
        return col
    canon = reverse.get(col, col)
    for candidate in {canon} | aliases.get(canon, set()):
        if candidate in valid_cols:
            return candidate
    return None


def repair_column_aliases(
    sql: str,
    db_schema: Dict[str, Any],
    column_aliases: Optional[Dict[str, Set[str]]] = None,
) -> RepairResult:
    """Rename alias-mismatched columns in ``sql`` to their real dataset names.

    Args:
        sql: the LLM-generated SQL.
        db_schema: schema dict (``{"types": {col: dtype}}`` or ``{col: dtype}``).
        column_aliases: alias map; defaults to the shared ``COLUMN_ALIASES``.

    Returns:
        A RepairResult. ``sql`` is the (possibly unchanged) query; ``changed`` is
        True only if at least one column was renamed; ``unresolved_columns`` lists
        referenced columns that exist neither literally nor via an alias (a signal
        to the caller that deterministic fallback is needed).
    """
    aliases = COLUMN_ALIASES if column_aliases is None else column_aliases
    reverse = _build_reverse(aliases)
    valid_cols = _schema_columns(db_schema)

    result = RepairResult(sql=sql)
    if not sql or not sql.strip():
        return result

    try:
        expr = sqlglot.parse_one(sql, read="duckdb")
    except Exception:
        # Unparseable -> nothing to repair here; let retry/fallback handle it.
        return result
    if expr is None:
        return result

    # No schema to check against -> we cannot know what is valid; do nothing.
    if not valid_cols:
        return result

    seen_repairs: Dict[str, str] = {}
    unresolved: Set[str] = set()

    # Names the query defines itself are not dataset columns and must never be
    # renamed: SELECT-list aliases (``... AS energy_score_total ORDER BY
    # energy_score_total``) and columns of CTEs / derived tables / VALUES lists
    # (``p.latitude`` where p is a CTE).
    select_aliases, derived_tables = query_local_names(expr)

    def _rename(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            name = node.name
            if name and name not in valid_cols and not is_query_local(node, select_aliases, derived_tables):
                target = _resolve_to_schema(name, valid_cols, aliases, reverse)
                if target is not None and target != name:
                    seen_repairs[name] = target
                    # Rebuild the identifier, preserving any table qualifier.
                    return exp.column(target, table=node.table or None)
                elif target is None:
                    unresolved.add(name)
        return node

    new_expr = expr.transform(_rename)

    # Type repair: a text column compared with a number fails in DuckDB
    # ("Cannot compare VARCHAR and INTEGER_LITERAL"), e.g. epoca_costruzione
    # (years stored as VARCHAR) >= 1990. Cast the column, never the literal.
    text_cols = {c for c, t in _schema_types(db_schema).items() if is_text_type(t)}
    casts: Set[str] = set()

    def _try_cast(col: exp.Column) -> exp.Expression:
        casts.add(col.name)
        return exp.TryCast(this=col.copy(), to=exp.DataType.build("DOUBLE"))

    def _cast(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            left, right = node.this, node.expression
            if _is_text_column(left, text_cols) and _is_number_literal(right):
                node.set("this", _try_cast(left))
            elif _is_text_column(right, text_cols) and _is_number_literal(left):
                node.set("expression", _try_cast(right))
        elif isinstance(node, exp.Between):
            if (_is_text_column(node.this, text_cols)
                    and _is_number_literal(node.args.get("low"))
                    and _is_number_literal(node.args.get("high"))):
                node.set("this", _try_cast(node.this))
        return node

    if text_cols:
        new_expr = new_expr.transform(_cast)

    result.unresolved_columns = sorted(unresolved)
    if seen_repairs or casts:
        result.sql = new_expr.sql(dialect="duckdb")
        result.changed = True
        result.repairs = [{"from": k, "to": v} for k, v in sorted(seen_repairs.items())]
        result.repairs += [{"from": c, "to": f"TRY_CAST({c} AS DOUBLE)"} for c in sorted(casts)]

    return result


def _schema_types(db_schema: Dict[str, Any]) -> Dict[str, Any]:
    types = db_schema.get("types") if isinstance(db_schema, dict) else None
    return types if isinstance(types, dict) else {}


def _is_text_column(node: Optional[exp.Expression], text_cols: Set[str]) -> bool:
    return isinstance(node, exp.Column) and node.name in text_cols


def _is_number_literal(node: Optional[exp.Expression]) -> bool:
    if isinstance(node, exp.Neg):
        node = node.this
    return isinstance(node, exp.Literal) and node.is_number
