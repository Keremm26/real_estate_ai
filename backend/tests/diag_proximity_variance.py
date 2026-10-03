"""
Diagnostic (Level 0, MCF feasibility): is the ProximityAgent's extraction
VARIANCE-dominated or BIAS-dominated?  [v2 — faithful to pipeline conditions]

Why this exists
---------------
Before building any MCF-style multi-sample filtering, we must know whether the
proximity misses are random (different each sample -> a selector like MCF can
recover the right answer) or deterministic (missed every sample -> MCF cannot
help; only a prompt/gazetteer/critic fix can).

v2 fix: the real pipeline calls the proximity agent WITH computed column
statistics (graph_agent.run_proximity -> statistics=proximity_stats); the prompt
leans on that DATA DISTRIBUTION for threshold calibration. v1 of this diagnostic
passed statistics=None, which ran the agent half-blind and likely inflated the
instability. v2 computes the same stats once and feeds them to every run, and
draws its queries from the real query_bank (incl. compound 4-5 category queries).

Method
------
For each query we run the ProximityAgent:
  - K times at high temperature (TEMP, default 0.8)  -> diversity probe
  - 1 time at temperature 0.0                        -> deployed baseline
We compare WHICH proximity categories each run extracts to the gold categories,
and classify each (query, gold-category) pair via "oracle best-of-K":

  appeared_in_k = # of the K hi-temp runs that produced the gold category
    == 0          -> BIAS      (sampling never reaches it; MCF useless)
    == K          -> STABLE    (always correct; nothing to fix)
    0 < .. < K    -> VARIANCE  (MCF sweet spot: right answer in *some* samples)

We also track EXTRA categories (false positives), including for named-place-only
queries whose correct proximity output is EMPTY (precision test).

Run
---
    python tests/diag_proximity_variance.py            # K=5, TEMP=0.8, bank queries
    PROX_DIAG_K=8 python tests/diag_proximity_variance.py
    python tests/diag_proximity_variance.py --dry      # preview queries, no calls

Cost: (K+1) calls per query on ONE agent. Keep K and the query list small.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

VALID_CATS = {"healthcare", "mobility", "green", "sport", "commercial", "education"}

K = int(os.environ.get("PROX_DIAG_K", "5"))
TEMP = float(os.environ.get("PROX_DIAG_TEMP", "0.8"))
LIMIT = int(os.environ.get("PROX_DIAG_LIMIT", "0")) or None
DRY = "--dry" in sys.argv

HERE = os.path.dirname(__file__)


# ---------------------------------------------------------------------------
# Build the query set from the real query_bank: every proximity-relevant query
# (has a servizi_accessori category and/or a named place). Expected categories
# come from the gold (servizi_accessori phrases only; named places must NOT
# produce a proximity category -> they are precision tests with empty gold).
# ---------------------------------------------------------------------------
def build_queries():
    from tests.query_bank import QUERY_BANK, build_query, phrases_of
    g = json.load(open(os.path.join(HERE, "constraint_ground_truth.json")))["phrases"]
    phrase2cat = {k: v["expected"][0]["column"]
                  for k, v in g.items()
                  if v.get("agent") in ("poi", "proximity") and v.get("expected")}

    out = []
    for i, e in enumerate(QUERY_BANK):
        combo = e["combo"]
        if not (combo.get("servizi_accessori") or combo.get("punto_di_interesse")):
            continue  # not proximity-relevant
        exp = {phrase2cat[ph] for ph in phrases_of(combo) if ph in phrase2cat}
        named_place = bool(combo.get("punto_di_interesse"))
        out.append({
            "id": f"T{e['tier']}_{i}",
            "tier": e["tier"],
            "query": build_query(combo),
            "gold": exp,                 # may be empty (precision-only query)
            "named_place": named_place,
        })

    # The bank carries at most ONE proximity category per query. Append genuine
    # MULTI-category queries — the exact case that wobbled in the v1 diagnostic.
    multi = [
        ("MC_edu_mob",   "Cerco un'abitazione con scuole e mezzi di trasporto nelle vicinanze.",            {"education", "mobility"}, False),
        ("MC_three",     "Cerco un immobile con parchi, palestre e mezzi di trasporto nelle vicinanze.",     {"green", "sport", "mobility"}, False),
        ("MC_heal_comm", "Cerco un'abitazione con servizi sanitari e negozi e supermercati nelle vicinanze.",{"healthcare", "commercial"}, False),
        ("MC_edu_grn_np","Cerco un'abitazione vicino a Porta Susa con scuole e parchi nelle vicinanze.",     {"education", "green"}, True),
    ]
    for mid, q, gold, np_ in multi:
        out.append({"id": mid, "tier": 99, "query": q, "gold": gold, "named_place": np_})
    return out


def extract_cats(result) -> set:
    """Pull the set of valid proximity categories from an agent result."""
    reqs = getattr(result, "requirements", None) or []
    if not reqs:
        try:
            raw = json.loads(getattr(result, "raw_text", "") or "{}")
            reqs = raw.get("requirements", []) or []
        except Exception:
            reqs = []
    cats = set()
    for r in reqs:
        if not isinstance(r, dict):
            continue
        col = str(r.get("target_column") or r.get("column") or "").strip().lower()
        if col in VALID_CATS:
            cats.add(col)
    return cats


def build_agent(temperature: float):
    os.environ["LLM_TEMPERATURE"] = str(temperature)
    from app.services.llm.agents.proximity_agent import ProximityAgent
    return ProximityAgent()


def compute_proximity_stats():
    """Same stats the pipeline feeds the proximity agent (graph_agent.run_proximity).

    The pipeline passes an English-keyed base_dataset; the parquet stores these
    categories in Italian, so we read the Italian columns and relabel to English
    before computing, matching the distributions the agent sees in production.
    """
    import pandas as pd
    from app.core.config import settings
    from app.core.constants import PROXIMITY_AGENT_COLUMNS
    from app.services.llm.agents.graph_agent import GraphOrchestratorAgent
    EN2IT = {"healthcare": "sanita", "mobility": "mobilita", "green": "verde",
             "sport": "sport", "commercial": "commerciale", "education": "educazione"}
    ds = settings.dataset_options.get("full")
    ds = ds if os.path.isabs(ds) else os.path.abspath(os.path.join(HERE, "..", ds))
    df = pd.read_parquet(ds, columns=list(EN2IT.values()))
    df = df.rename(columns={v: k for k, v in EN2IT.items()})
    agent = GraphOrchestratorAgent.__new__(GraphOrchestratorAgent)
    return agent._get_column_statistics(columns=PROXIMITY_AGENT_COLUMNS, dataset_df=df)


def main():
    queries = build_queries()
    if LIMIT:
        queries = queries[:LIMIT]
    print("=== ProximityAgent variance/bias diagnostic (v2, with real stats) ===")
    print(f"K = {K}   TEMP = {TEMP}   queries = {len(queries)}   total calls = {len(queries)*(K+1)}\n")

    if DRY:
        for q in queries:
            tag = "PRECISION(empty gold)" if not q["gold"] else f"expect={sorted(q['gold'])}"
            print(f"[{q['id']}] {tag}  named_place={q['named_place']}\n    {q['query']}")
        print("\n(dry run — no calls made)")
        return

    stats = compute_proximity_stats()
    print(f"proximity_stats keys: {sorted(stats.keys()) if isinstance(stats, dict) else type(stats)}\n")

    agent_hi = build_agent(TEMP)
    agent_lo = build_agent(0.0)

    records = []
    summary = {"STABLE": 0, "VARIANCE": 0, "BIAS": 0}
    fp_runs = 0          # runs that produced an extra (false-positive) category
    fp_run_total = 0

    for q in queries:
        qid, query, gold = q["id"], q["query"], q["gold"]
        hi_runs = []
        for i in range(K):
            try:
                res = agent_hi.run(query=query, mode="filtering", statistics=stats)
                hi_runs.append(extract_cats(res))
            except Exception as e:
                hi_runs.append(set())
                print(f"  [{qid}] hi-run {i} error: {e}")
        try:
            lo_cats = extract_cats(agent_lo.run(query=query, mode="filtering", statistics=stats))
        except Exception as e:
            lo_cats = set()
            print(f"  [{qid}] lo-run error: {e}")

        oracle = set().union(*hi_runs) if hi_runs else set()
        extras_union = oracle - gold

        # false-positive accounting (per run)
        for run in hi_runs:
            fp_run_total += 1
            if run - gold:
                fp_runs += 1

        per_cat = {}
        for cat in sorted(gold):
            appeared = sum(1 for run in hi_runs if cat in run)
            label = "BIAS" if appeared == 0 else ("STABLE" if appeared == K else "VARIANCE")
            summary[label] += 1
            per_cat[cat] = {"appeared_in_k": appeared, "label": label}

        oracle_recall = (len(oracle & gold) / len(gold)) if gold else None
        lo_recall = (len(lo_cats & gold) / len(gold)) if gold else None

        records.append({
            "id": qid, "tier": q["tier"], "query": query, "gold": sorted(gold),
            "named_place": q["named_place"],
            "hi_runs": [sorted(r) for r in hi_runs], "lo_baseline": sorted(lo_cats),
            "oracle_cats": sorted(oracle), "oracle_recall": oracle_recall,
            "lo_recall": lo_recall, "extras_union": sorted(extras_union), "per_cat": per_cat,
        })

        if gold:
            cat_str = "  ".join(f"{c}:{v['appeared_in_k']}/{K}[{v['label'][:3]}]" for c, v in per_cat.items())
            extra = f"  +FP={sorted(extras_union)}" if extras_union else ""
            print(f"[{qid}] gold={sorted(gold)} lo={sorted(lo_cats)}({lo_recall:.0%}) oracle={oracle_recall:.0%} | {cat_str}{extra}")
        else:
            # precision-only (named place, no category expected)
            fp_any = sorted(set().union(*hi_runs)) if hi_runs else []
            verdict = "CLEAN" if not fp_any else f"FALSE-POSITIVE {fp_any}"
            print(f"[{qid}] PRECISION (named place) lo={sorted(lo_cats)} hi_any={fp_any} -> {verdict}")

    total = sum(summary.values())
    print("\n=== per (query, gold-category) classification ===")
    for k in ("STABLE", "VARIANCE", "BIAS"):
        pct = (summary[k] / total * 100) if total else 0
        print(f"  {k:9s}: {summary[k]:3d}  ({pct:.0f}%)")
    print(f"  total gold-cats checked: {total}")
    print(f"\n  false-positive runs (extra category): {fp_runs}/{fp_run_total}")

    print("\n=== interpretation ===")
    print("  VARIANCE-heavy -> MCF can help (right answer in some samples).")
    print("  BIAS-heavy     -> MCF will NOT help; fix prompt/gazetteer/critic.")
    print("  STABLE-heavy   -> no recall problem; check false-positive rate for precision issues.")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(HERE, f"diag_proximity_variance_{ts}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"K": K, "temp": TEMP, "summary": summary,
                   "fp_runs": fp_runs, "fp_run_total": fp_run_total,
                   "records": records}, f, ensure_ascii=False, indent=2)
    print(f"\nRaw results -> {out}")


if __name__ == "__main__":
    main()
