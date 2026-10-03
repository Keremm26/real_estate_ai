"""
Small live batch — generate fresh runs and capture the constraint-layer snapshot.

Runs a handful of diverse queries end-to-end (real model calls), captures the
observe-only constraint_layer snapshot from each, saves everything to a JSON, and
prints per-query + aggregate summaries.

Cost note: this makes real LLM calls. llm_limit=1 keeps the expensive evaluation
step minimal. Keep the query list short.

Run:
    python tests/run_batch_constraint_layer.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.analysis_service import analysis_service

# Diverse queries chosen to stress different behaviors.
QUERIES = [
    # 1. Easy, single concept
    "Apartments with energy class A or better in Turin",
    # 2. Multi-constraint
    "Large offices over 2000 sqm near Porta Nuova with good public transport links",
    # 3. Conflicting adjectives (small vs spacious)
    "A small but spacious commercial space close to the city center",
    # 4. Regulatory use-case + proximity
    "A property suitable for a nursing home for elderly people, near a hospital",
    # 5. Typology + regulatory intent (probes the intent-gap we saw)
    "A residential building for a co-housing project, well connected and energy efficient",
]


async def run_one(query: str, idx: int):
    t0 = time.time()
    res = await analysis_service.run_analysis(
        run_id=f"batch_{idx}",
        query=query,
        dataset_key="full",
        map_limit=100,
        llm_limit=1,            # minimize evaluation cost
        analysis_mode="agent",
        use_relaxation=True,
    )
    dt = time.time() - t0
    gr = res.get("gemini_responses", {}) or {}
    snap = gr.get("constraint_layer")
    return {
        "query": query,
        "seconds": round(dt, 1),
        "sql": res.get("sql_query"),
        "snapshot": snap,
        "num_results": len(res.get("buildings", []) or []),
    }


async def main():
    results = []
    for i, q in enumerate(QUERIES, start=1):
        print(f"[{i}/{len(QUERIES)}] running: {q[:60]} ...")
        try:
            r = await run_one(q, i)
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {str(e)[:200]}")
            results.append({"query": q, "error": str(e)[:300]})
            continue
        results.append(r)
        c = (r.get("snapshot") or {}).get("counters", {})
        print(f"    done in {r['seconds']}s | results={r['num_results']} | "
              f"IR entries={c.get('num_entries')} hard={c.get('num_hard')} "
              f"valid={c.get('is_valid')} zero_add={c.get('zero_addition_count')} "
              f"invalid_cols={c.get('invalid_columns_count')}")

    # Save raw results.
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "agent_logs")
    out_path = os.path.join(out_dir, f"constraint_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved raw batch results to: {out_path}")

    # Per-query detail.
    print("\n" + "=" * 78)
    print("PER-QUERY DETAIL")
    print("=" * 78)
    for r in results:
        if "error" in r:
            print(f"\n• {r['query'][:70]}\n    ERROR: {r['error'][:120]}")
            continue
        snap = r.get("snapshot") or {}
        print(f"\n• {r['query'][:70]}")
        print(f"    SQL: {(r.get('sql') or '')[:140]}")
        ir = snap.get("constraint_ir", {})
        ents = ir.get("entries", [])
        labels = []
        for e in ents:
            if e["kind"] == "predicate":
                labels.append(f"{e['source_agent']}:{e['target_column']}")
            else:
                labels.append(f"{e['source_agent']}:GEO")
        print(f"    IR captured: {labels or '(none)'}")
        if ir.get("failures"):
            print(f"    PARSE FAILURES: {len(ir['failures'])}")

    # Aggregate (only runs that produced a snapshot).
    good = [r for r in results if r.get("snapshot")]
    if good:
        n = len(good)
        def cnt(key):
            return sum(1 for r in good if r["snapshot"]["counters"].get(key))
        print("\n" + "=" * 78)
        print(f"AGGREGATE over {n} runs with snapshots")
        print("=" * 78)
        print(f"  avg IR entries           : {sum(r['snapshot']['counters']['num_entries'] for r in good)/n:.1f}")
        print(f"  runs with parse failures : {sum(1 for r in good if r['snapshot']['counters']['num_failures'])}/{n}")
        print(f"  runs dropping hard       : {n - cnt('hard_constraints_preserved')}/{n}")
        print(f"  runs with invalid cols   : {cnt('invalid_columns_count')}/{n}")
        print(f"  runs with zero-addition  : {cnt('zero_addition_count')}/{n}")
        print(f"  runs fully valid         : {cnt('is_valid')}/{n}")


if __name__ == "__main__":
    asyncio.run(main())
