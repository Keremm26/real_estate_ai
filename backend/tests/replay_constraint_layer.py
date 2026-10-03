"""
Replay harness — the "before picture" for the constraint layer (v1, observe-only).

What it does
------------
Loads every saved agent trace (data/agent_logs/trace_*.json), rebuilds the
ConstraintIR from each run's agent outputs, validates the run's generated SQL
against it (using the REAL dataset schema), and prints an aggregate report.

This needs NO API keys and makes NO model calls — it replays saved runs. It is
the measurement that quantifies how today's pipeline behaves, before we change
anything in v2.

Run:
    python tests/replay_constraint_layer.py
"""

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.constraint_ir import build_constraint_ir
from app.services.llm.constraint_validation import validate_sql_semantics

TRACE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "agent_logs")

# Agent trace names -> IR source-agent names.
NAME_MAP = {
    "building-agent": "building",
    "energy-agent": "energy",
    "regulatory-agent": "regulatory",
    "proximity-agent": "proximity",
}


def load_real_schema():
    """Build a schema dict {'types': {col: '?'}} from the real dataset columns.

    Falls back to None if the dataset is not available (then we cannot do the
    invalid-column check and say so).
    """
    try:
        from app.core.config import settings
        import pyarrow.parquet as pq

        path = settings.DATASET_FULL
        if path and path.endswith(".parquet") and os.path.exists(path):
            cols = pq.ParquetFile(path).schema.names
            return {"types": {c: "?" for c in cols}}
    except Exception as e:
        print(f"(could not load real schema: {e})")
    return None


def extract_query(trace_data):
    """Pull the user query out of the sql-agent input prompt, if present."""
    for ex in trace_data.get("agent_executions", []):
        if ex.get("agent_name") == "sql-agent":
            inp = ex.get("input", "")
            if isinstance(inp, str):
                m = re.search(r"USER QUERY:\s*(.+)", inp)
                if m:
                    return m.group(1).strip()
    return "(query not found in trace)"


def extract_payloads_and_sql(trace_data):
    """Return (agent_payloads, generated_sql) reconstructed from a trace."""
    payloads_by_agent = {}
    sql = None
    for ex in trace_data.get("agent_executions", []):
        a = ex.get("agent_name")
        out = ex.get("output", {})
        # Extraction outputs are dicts with 'requirements'; scorer outputs are lists.
        if a in NAME_MAP and NAME_MAP[a] not in payloads_by_agent and isinstance(out, dict):
            payloads_by_agent[NAME_MAP[a]] = {
                "source_agent": NAME_MAP[a],
                "parsed": out,
                "raw_text": json.dumps(out, ensure_ascii=False),
            }
        if a == "sql-agent" and sql is None and isinstance(out, dict):
            sql = out.get("sql")
    return list(payloads_by_agent.values()), sql


def main():
    files = sorted(glob.glob(os.path.join(TRACE_DIR, "trace_*.json")))
    if not files:
        print("No trace files found in", TRACE_DIR)
        return

    schema = load_real_schema()
    schema_note = "real dataset schema" if schema else "NO schema (column checks skipped)"
    schema = schema or {"types": {}}
    metadata = {"fields": {}}  # value-domain checks limited; see notes

    print("=" * 78)
    print("CONSTRAINT LAYER — BEFORE PICTURE (observe-only replay)")
    print(f"Traces: {len(files)}   |   Schema: {schema_note}")
    print("=" * 78)

    rows = []
    for f in files:
        try:
            data = json.load(open(f))
        except Exception:
            continue
        payloads, sql = extract_payloads_and_sql(data)
        if not sql:
            continue  # trace without a generated SQL; skip

        ir = build_constraint_ir(query=extract_query(data), agent_payloads=payloads)
        rep = validate_sql_semantics(sql, ir, schema, metadata)

        rows.append({
            "file": os.path.basename(f),
            "query": extract_query(data),
            "entries": len(ir.entries),
            "failures": len(ir.failures),
            "hard": len(ir.hard()),
            "hard_ok": rep.hard_constraints_preserved,
            "invalid_cols": len(rep.invalid_columns),
            "zero_add": len(rep.zero_addition_violations),
            "missing": len(rep.missing_constraints),
            "is_valid": rep.is_valid,
            "invalid_col_names": rep.invalid_columns,
            "zero_add_names": rep.zero_addition_violations,
        })

    if not rows:
        print("No traces with a generated SQL to analyze.")
        return

    # Per-trace lines.
    for r in rows:
        print()
        print(f"• {r['file']}")
        print(f"    query           : {r['query'][:70]}")
        print(f"    IR entries      : {r['entries']}  (hard={r['hard']}, parse-failures={r['failures']})")
        print(f"    hard preserved  : {r['hard_ok']}")
        print(f"    invalid columns : {r['invalid_cols']}  {r['invalid_col_names'] or ''}")
        print(f"    zero-addition   : {r['zero_add']}  {r['zero_add_names'] or ''}")
        print(f"    overall valid   : {r['is_valid']}")

    # Aggregate.
    n = len(rows)
    def pct(c):
        return f"{c}/{n} ({100*c//n}%)"

    print()
    print("=" * 78)
    print("AGGREGATE (the numbers for the thesis 'before' table)")
    print("=" * 78)
    print(f"  traces analyzed                 : {n}")
    print(f"  avg IR entries per query        : {sum(r['entries'] for r in rows)/n:.1f}")
    print(f"  runs with parse failures        : {pct(sum(1 for r in rows if r['failures'] > 0))}")
    print(f"  runs dropping a hard constraint : {pct(sum(1 for r in rows if not r['hard_ok']))}")
    print(f"  runs with invalid columns       : {pct(sum(1 for r in rows if r['invalid_cols'] > 0))}")
    print(f"  runs with zero-addition         : {pct(sum(1 for r in rows if r['zero_add'] > 0))}")
    print(f"  runs fully valid                : {pct(sum(1 for r in rows if r['is_valid']))}")
    print()
    print("Note: categorical value-domain checks are limited here because the")
    print("dataset's metadata field names differ from the column names (a separate")
    print("finding). Column existence and faithfulness checks use the real schema.")


if __name__ == "__main__":
    main()
