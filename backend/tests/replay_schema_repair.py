"""
Offline replay of schema-grounding repair on the final RAG study (no LLM calls).

For every pipeline trial in tests/results/curated_{none,rag}[_city]/ (consistency trials only,
not Marco's full/ consensus copies) the script re-executes on the same city dataset:
  (a) the SQL agent's first raw output (agent_trace step whose output has a "sql" field), and
  (b) the SQL the pipeline actually ran (final_sql), when it came from the LLM (sql_source llm or
      repaired_llm),
and compares status (ok / empty / error) and row counts. It answers: how many first-attempt
queries would fail without the repair, which columns the repair renamed, and whether the replay
reproduces the pipeline's own result counts (fidelity check).

The pipeline registers haversine_km as a Python UDF returning FLOAT; the replay uses the same
formula as a DuckDB macro cast to FLOAT (vectorised, same results on the checked trials).

Usage (from backend/):
    ../.venv/bin/python tests/replay_schema_repair.py [--out eval_results/schema_repair_replay]
"""
import argparse
import collections
import glob
import json
import os
import re

import duckdb
import sqlglot
from sqlglot import exp

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(BACKEND, "tests", "results")
DATASETS = {
    "turin": os.path.join(BACKEND, "data", "metadata", "estates.parquet"),
    "london": os.path.join(BACKEND, "data", "metadata", "estates_london.parquet"),
    "madrid": os.path.join(BACKEND, "data", "metadata", "estates_madrid.parquet"),
}
ARMS = [
    ("turin", "none", "curated_none"), ("turin", "rag", "curated_rag"),
    ("london", "none", "curated_none_london"), ("london", "rag", "curated_rag_london"),
    ("madrid", "none", "curated_none_madrid"), ("madrid", "rag", "curated_rag_madrid"),
]
HAVERSINE = """
CREATE MACRO haversine_km(lat1, lon1, lat2, lon2) AS CAST(
  6371 * 2 * atan2(
    sqrt(pow(sin(radians(lat2 - lat1) / 2), 2)
         + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)),
    sqrt(1 - (pow(sin(radians(lat2 - lat1) / 2), 2)
         + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)))
  ) AS FLOAT)
"""


def connect(city):
    con = duckdb.connect(":memory:")
    con.execute(HAVERSINE)
    con.execute(f"CREATE VIEW base_properties AS SELECT * FROM '{DATASETS[city]}'")
    for view in ("PROPERTIES", "ESTATES", "IMMOBILI"):
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM base_properties")
    return con


def run(con, sql):
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return {"status": "missing", "rows": None, "error": None}
    try:
        rows = con.execute(f"SELECT COUNT(*) FROM ({sql}) AS q").fetchone()[0]
        return {"status": "ok" if rows else "empty", "rows": rows, "error": None}
    except Exception as e:
        return {"status": "error", "rows": None, "error": str(e).splitlines()[0]}


def sql_agent_outputs(trace):
    return [s["output"]["sql"] for s in trace or []
            if isinstance(s, dict) and isinstance(s.get("output"), dict) and isinstance(s["output"].get("sql"), str)]


def diff(raw, final):
    try:
        a, b = sqlglot.parse_one(raw, read="duckdb"), sqlglot.parse_one(final, read="duckdb")
    except Exception:
        return {"parse_error": True}
    cols = lambda t: {c.name.lower() for c in t.find_all(exp.Column)}
    lits = lambda t: {lit.this for lit in t.find_all(exp.Literal) if lit.is_string}
    casts = lambda t: sum(1 for _ in t.find_all(exp.TryCast))
    return {"renamed_from": sorted(cols(a) - cols(b)), "renamed_to": sorted(cols(b) - cols(a)),
            "try_cast_added": casts(b) - casts(a), "literals_changed": lits(a) != lits(b)}


def replay():
    records = []
    for city, arm, folder in ARMS:
        con = connect(city)
        pattern = os.path.join(RESULTS, folder, "outputs", "benchmarks", "gemma-4", "**", "*.json")
        for f in sorted(p for p in glob.glob(pattern, recursive=True) if f"{os.sep}full{os.sep}" not in p):
            d = json.load(open(f))
            outs = sql_agent_outputs(d.get("agent_trace"))
            raw, final, source = (outs[0] if outs else None), d.get("final_sql"), d.get("sql_source")
            rec = {"city": city, "arm": arm, "file": os.path.relpath(f, RESULTS), "sql_source": source,
                   "results_count": d.get("results_count"), "n_sql_agent_calls": len(outs),
                   "raw": run(con, raw), "final": run(con, final) if source in ("llm", "repaired_llm") else None}
            if raw and final and source == "repaired_llm":
                rec["diff"] = diff(raw, final)
            records.append(rec)
        con.close()
    return records


def summarise(records):
    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.0f}%)" if b else "0/0"
    lines = ["# Schema-grounding repair: offline replay on the final study", "",
             "Each trial's first raw SQL and the SQL the pipeline ran were re-executed on the city dataset "
             "(DuckDB, no LLM calls). Source: `tests/replay_schema_repair.py`.", "",
             "| city | arm | trials | repaired by the pipeline | raw SQL fails without repair | "
             "repaired SQL changed rows of a raw query that ran | trials with SQL retries | replay = pipeline count |",
             "|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for city, arm, _ in ARMS:
        rs = [r for r in records if r["city"] == city and r["arm"] == arm]
        rep = [r for r in rs if r["sql_source"] == "repaired_llm"]
        fail = [r for r in rs if r["raw"]["status"] == "error"]
        changed = [r for r in rep if r["raw"]["status"] != "error" and r["final"] and r["raw"]["rows"] != r["final"]["rows"]]
        retries = [r for r in rs if r["n_sql_agent_calls"] > 1]
        checked = [r for r in rs if r["final"] and r["final"]["rows"] is not None and isinstance(r["results_count"], int)]
        match = [r for r in checked if r["final"]["rows"] == r["results_count"]]
        lines.append(f"| {city} | {arm} | {len(rs)} | {pct(len(rep), len(rs))} | {pct(len(fail), len(rs))} | "
                     f"{pct(len(changed), len(rep))} | {len(retries)} | {pct(len(match), len(checked))} |")
        tot.update(trials=len(rs), repaired=len(rep), raw_fail=len(fail), changed=len(changed),
                   retries=len(retries), checked=len(checked), match=len(match))
    lines.append(f"| **all** | | {tot['trials']} | {pct(tot['repaired'], tot['trials'])} | "
                 f"{pct(tot['raw_fail'], tot['trials'])} | {pct(tot['changed'], tot['repaired'])} | "
                 f"{tot['retries']} | {pct(tot['match'], tot['checked'])} |")
    kinds = collections.Counter(re.match(r"(\w+ Error)", r["raw"]["error"]).group(1) if re.match(r"(\w+ Error)", r["raw"]["error"] or "") else r["raw"]["error"]
                                for r in records if r["raw"]["status"] == "error")
    renames = collections.Counter(c for r in records for c in (r.get("diff") or {}).get("renamed_from", []))
    unrepaired_fail = collections.Counter((r["city"], r["arm"], r["sql_source"]) for r in records
                                          if r["raw"]["status"] == "error" and r["sql_source"] != "repaired_llm")
    lines += ["", f"Raw-SQL error kinds: {dict(kinds)}",
              f"Columns renamed by the repair (agent name -> times): {dict(renames.most_common())}",
              f"Repairs that added TRY_CAST: {sum(1 for r in records if (r.get('diff') or {}).get('try_cast_added', 0) > 0)}; "
              f"repairs that changed string literals: {sum(1 for r in records if (r.get('diff') or {}).get('literals_changed'))}",
              f"Raw failures in trials not marked repaired (repaired SQL ran but matched nothing -> generic fallback): "
              f"{dict(unrepaired_fail)}", "",
              "Without the repair, a failing query enters the pipeline's retry loop (up to three new SQL-agent calls) "
              "and then the generic fallback; the replay cannot say whether those retries would have succeeded."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=os.path.join(BACKEND, "eval_results", "schema_repair_replay"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    recs = replay()
    json.dump(recs, open(os.path.join(args.out, "records.json"), "w"), indent=1, ensure_ascii=False)
    summary = summarise(recs)
    open(os.path.join(args.out, "summary.md"), "w").write(summary)
    print(summary)
