import asyncio
import json
import itertools
import threading
import json
import copy
import shutil
import asyncio
import argparse
import logging
import os
import sys
import time
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple, Optional

import numpy as np
import pandas as pd
from scipy import stats

# Import custom utilities (Ensure current dir is in sys.path)
sys.path.append(str(Path(__file__).resolve().parent))
from utils import (
    setup_logging, query_ctx, experiment_ctx, log_dir_ctx,
    safe_read_csv, safe_save_csv, safe_update_csv_column, file_lock,
    calculate_iou, calculate_f1, get_activated_agents, 
    calculate_architecture_agreement, extract_sql_columns,
    generate_report
)

try:
    import sqlglot
    from sqlglot import exp
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False

# --- 1. SETTINGS & ENVIRONMENT SETUP ---

current_script_path = Path(__file__).resolve()
suite_path = current_script_path.parent
backend_dir = suite_path.parent
sys.path.append(str(backend_dir))
from app.utils.json_sanitizer import make_json_safe
base_dir = backend_dir
from app.core.config import settings
from app.services.analysis_service import analysis_service
from tests.model_config import apply_model_config

# Added subfolder for results
results_path = Path(os.environ.get("EXPERIMENT_RESULTS_DIR", str(suite_path / "results")))
results_path.mkdir(parents=True, exist_ok=True)

# Global execution log
execution_csv_path = results_path / "execution_log.csv"
RUN_ID = os.environ.get("SYNTHETIC_RUN_ID", f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
os.environ["SYNTHETIC_RUN_ID"] = RUN_ID

class Vlog:
    def log(self, tag, msg):
        log_output(f"[{tag}] {msg}")

vlog = Vlog()

# Initialize logging
setup_logging(RUN_ID, execution_csv_path)
# --- GLOBAL STATE FOR MONITORING ---
pending_jobs = set()
pending_lock = threading.Lock()

def update_pending_job(job_id: str, action: str):
    """Adds or removes a job ID from the global pending set (thread-safe)."""
    with pending_lock:
        if action == "add":
            pending_jobs.add(job_id)
        else:
            pending_jobs.discard(job_id)

def log_output(msg):
    """Centralized log message helper."""
    logging.info(msg)
    # Also print to stdout for real-time monitoring if not in child process
    if "SYNTHETIC_RUN_ID" not in os.environ or os.environ.get("DEBUG_STDOUT") == "1":
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def with_query_context(func):
    """Decorator to set query context for async functions."""
    import functools
    @functools.wraps(func)
    async def wrapper(query, *args, **kwargs):
        token = query_ctx.set(query)
        try:
            return await func(query, *args, **kwargs)
        finally:
            query_ctx.reset(token)
    return wrapper

BASELINE_MODEL = "gpt-5.4"
EVALUATION_MODEL = "gpt-5.4"

# Model-specific concurrency limits to prevent quota issues (429)
# Models not listed here will use the global --max-concurrent value.
MODEL_CONCURRENCY_LIMITS = {
    "gpt-5.4": 2,
    "gpt-5.4-mini": 4,
    "gpt-oss-120b": 48,
    "gemma3-27b": 48,
    "qwen3-8b": 48,
}

def get_model_concurrency(model_name: str, default_val: int) -> int:
    """Returns the specific limit for a model if defined, else the global default."""
    return MODEL_CONCURRENCY_LIMITS.get(model_name, default_val)

from app.core.config import settings
from app.services.analysis_service import analysis_service
from app.services.real_estate_service import RealEstateService
from app.data.loaders import load_and_merge_data
from app.utils.json_parser import safe_extract_json


# Patch settings to use absolute paths relative to backend_dir
for attr in ["DATASET_FULL", "APE_DETAILED_DATA_PATH", "STATIC_DIR", "DATA_DIR", "APE_DIR", "META_DIR", "AGENT_LOGS_DIR"]:
    val = getattr(settings, attr, None)
    if val and isinstance(val, str) and not os.path.isabs(val):
        setattr(settings, attr, str(backend_dir / val))

def get_data_stats():
    """Return basic statistical metrics of the dataset for OOD detection."""
    df = RealEstateService._dataset_cache.get("full")
    if df is None: return {}
    stats = {}
    cols = [
        "superficie_di_riferimento_mq", "num_locali_omi", "latitudine", "longitudine",
        "epglnren_ape", "piani_fuori_terra", "anno_costruzione"
    ]
    for col in cols:
        if col in df.columns:
            try:
                c_upper = col.upper()
                stats[c_upper] = {
                    "min": float(df[col].min()),
                    "max": float(df[col].max()),
                    "mean": float(df[col].mean()),
                    "std": float(df[col].std())
                }
            except: pass
    return stats

async def preload_data():
    """Initializes the RealEstateService by pre-loading and merging essential datasets."""
    if not RealEstateService._dataset_cache.get("full"):
        try:
            df = load_and_merge_data(settings.DATASET_FULL)
            if df is not None:
                RealEstateService._dataset_cache["full"] = df
                # Build indexed cache
                df_indexed = df.copy()
                df_indexed["id_str"] = df_indexed["id"].astype(str)
                df_indexed = df_indexed.drop_duplicates(subset=["id_str"])
                df_indexed.set_index("id_str", inplace=True)
                RealEstateService._dataset_indexed_cache["full"] = df_indexed
        except Exception as e:
            print(f"Error preloading data: {e}")

# Sensitivity / ablation configurations: (id, disabled_agents, use_knowledge, architecture).
# Disabled-agent names must match the graph's task keys
# (graph_agent: ranking / building / location / energy / proximity / regulatory).
SENSITIVITY_CONFIGS = [
    ("no_ranking", ["ranking"], True, "multiagent"),
    ("no_knowledge", None, False, "multiagent"),
    ("no_proximity", ["proximity"], True, "multiagent"),
    ("no_regulatory", ["regulatory"], True, "multiagent"),
    ("no_location", ["location"], True, "multiagent"),
    ("no_energy", ["energy"], True, "multiagent"),
    ("no_building", ["building"], True, "multiagent"),
    ("baseline_columns", None, False, "baseline"),
    ("baseline_stats", None, True, "baseline"),
]

def extract_ranking_logic(res: dict) -> dict:
    """Ranking weights for one run, as recorded by the graph (gemini_responses):
    original_weights  = the ranking agent's weights
    effective_weights = weights actually used (agents without scorable
                        requirements excluded and their weight redistributed)
    discovered_agents = agents with at least one scorable requirement
    contributing_agents = agents with an effective weight > 0"""
    data = (res.get("gemini_responses") or {}).get("ranking_weights") or {}
    effective = {a: w for a, w in (data.get("effective_weights") or {}).items() if w and w > 0}
    return {
        "original_weights": data.get("weights") or {},
        "effective_weights": effective,
        "contributing_agents": list(effective.keys()),
        "discovered_agents": list(data.get("found_agents") or []),
        "excluded_agents": list(data.get("excluded_agents") or []),
    }


def _fmt_metric(value, digits: int = 3) -> str:
    """Format a metric for the report; missing data shows as n/a instead of a
    default that looks like a real result (1.0 IoU read as 'no impact')."""
    return "n/a" if value is None else f"{value:.{digits}f}"

def calculate_sql_iou(sql_a, sql_b, file_a="N/A", file_b="N/A"):
    # This uses extract_sql_columns from analysis_utils
    cols_a = extract_sql_columns(sql_a)
    cols_b = extract_sql_columns(sql_b)
    iou = calculate_iou(list(cols_a), list(cols_b))
    vlog.log("SQL_SIM", f"  Compare: {file_a} vs {file_b} | ColsA: {len(cols_a)} | ColsB: {len(cols_b)} | IoU: {iou:.3f}")
    return iou

def check_sql_ood(sql, data_stats):
    """Detects values outside the real data distribution and calculates normalization distance."""
    if not sql or not HAS_SQLGLOT: return []
    results = []
    try:
        parsed = sqlglot.parse_one(sql)
        # Handle Binary (>, <, =) and Between/In
        for condition in parsed.find_all((exp.Binary, exp.Between, exp.In)):
            col_node = condition.left if hasattr(condition, 'left') else (condition.this if hasattr(condition, 'this') else None)
            if not isinstance(col_node, exp.Column): continue
            col = col_node.name.upper()
            if col not in data_stats: continue
            
            stats_data = data_stats[col]
            rng = max(stats_data["max"] - stats_data["min"], 1.0)
            
            # Extract values to check
            vals = []
            if isinstance(condition, exp.Binary):
                if isinstance(condition.right, exp.Literal) and condition.right.is_number:
                    vals.append(float(condition.right.this))
            elif isinstance(condition, exp.Between):
                if condition.args.get('low') and condition.args['low'].is_number:
                    vals.append(float(condition.args['low'].this))
                if condition.args.get('high') and condition.args['high'].is_number:
                    vals.append(float(condition.args['high'].this))
            elif isinstance(condition, exp.In):
                for v in condition.args.get('expressions', []):
                    if v.is_number: vals.append(float(v.this))

            for v in vals:
                dist = 0.0
                if v < stats_data["min"]:
                    dist = (stats_data["min"] - v) / rng
                elif v > stats_data["max"]:
                    dist = (v - stats_data["max"]) / rng
                
                # Tightness: where is the value placed in the 0-1 range of real data
                # Can be < 0 or > 1 if OOD
                tightness = (v - stats_data["min"]) / rng
                
                results.append({
                    "col": col, "val": v, "dist": dist, "tightness": tightness, "is_ood": dist > 0
                })
    except: pass
    return results

# --- 3. EXECUTION DISPATCHERS ---

# TriSQL constraint layer mode for benchmark runs: "repair" applies only the
# column-alias rename (surface_area -> superficie_di_riferimento_mq), which every
# arm needs to avoid SQL binder errors, without TriSQL's IR fallback / relaxation
# safety confounding other experiments. Use "enforce" for TriSQL evaluations.
CONSTRAINT_MODE = os.environ.get("CONSTRAINT_MODE", "repair")

# Query set and retrieval arm, set once per process by configure_run() (CLI
# --queries / --arm; the conductor forwards both to its model subprocesses).
#   legacy  = Marco's combinatorial generator (query_parameters.json)
#   curated = benchmark_queries_turin.json (RAG study: arms none vs rag)
# suite_root holds the suite CSVs and outputs/. Legacy without an arm keeps the
# original layout (results/); every other run gets its own folder so the arms
# never overwrite each other.
from app.services.llm.rag import config as rag_config

CURATED_QUERIES_PATH = suite_path / "benchmark_queries_turin.json"
QUERY_SET = "legacy"
suite_root = results_path


def configure_run(query_set: str = "legacy", arm: Optional[str] = None) -> Path:
    """Select the query set and the regulatory retrieval arm for this process."""
    global QUERY_SET, suite_root
    QUERY_SET = query_set
    if arm:
        rag_config.RETRIEVAL_MODE = arm   # read at call time by load_regulatory_context
    if query_set == "legacy" and not arm:
        suite_root = results_path
    else:
        suite_root = results_path / f"{query_set}_{rag_config.RETRIEVAL_MODE}"
    suite_root.mkdir(parents=True, exist_ok=True)
    return suite_root


def load_curated_queries() -> List[Dict[str, Any]]:
    with open(CURATED_QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)["queries"]


def _summarize_building(b: dict) -> dict:
    """Compact view of a ranked building: what the compliance metric and the
    pairwise judge need, without the heavy nested fields."""
    keys = ["id", "score", "address", "omi_zone", "property_type", "surface_area", "energy_class",
            "construction_year", "legal_nature", "cultural_constraint", "purpose", "rooms",
            "distance_km", "proximity_reference", "greenery", "mobility", "education",
            "location_score", "regulatory_score", "energy_score", "building_score", "proximity_score"]
    out = {k: b.get(k) for k in keys}
    out["id"] = str(out["id"])
    return out

@with_query_context
async def run_query(query, architecture="multiagent", disabled=None, use_knowledge=True, use_relaxation=False):
    log_output(f"[QUERY] Searching: {query}")
    try:
        agent = analysis_service._init_graph_agent()
        start_t = time.time()
        res = await analysis_service.run_analysis(
            run_id=f"test_{datetime.now().strftime('%H%M%S')}",
            query=query, dataset_key="full", map_limit=15000, llm_limit=25,
            analysis_mode="agent", disabled_agents=disabled, use_data_knowledge=use_knowledge,
            use_relaxation=use_relaxation,
            architecture=architecture,
            constraint_mode=CONSTRAINT_MODE,
        )
        duration = round((time.time() - start_t) * 1000, 2)
        buildings = res.get("buildings", [])
        buildings_dicts = [b.model_dump() if hasattr(b, "model_dump") else b for b in buildings]
        ranking = [{"id": str(b.get("id")), "score": float(round(b.get("score", 0.0), 1))} for b in buildings_dicts[:10]]
        trace = res.get("agent_trace", [])
        
        # 1. Ranking context (for 2a, 2c, 3a), as recorded by the graph itself:
        #    "weights"           = the ranking agent's original weights
        #    "effective_weights" = the weights actually used, after agents that
        #                          found no scorable requirement were excluded
        #    "found_agents"      = agents with at least one scorable requirement
        ranking_logic = extract_ranking_logic(res)

        # 2. Clean up trace: cap long outputs (e.g. ranking data) to 5 items
        cleaned_trace = []
        for t in trace:
            t_entry = t.copy() if isinstance(t, dict) else {}
            if not t_entry:
                continue
            out = t_entry.get("output")
            if isinstance(out, list) and len(out) > 5:
                t_entry["output"] = out[:5] + [f"... truncated (+{len(out)-5} items)"]
            cleaned_trace.append(t_entry)

        # 5. Extract evaluations (for 4a)
        eval_resp = res.get("gemini_responses", {}).get("evaluation", {}) or []
        evaluations = eval_resp.get("results", []) if isinstance(eval_resp, dict) else []

        # 6. Regulatory output + retrieval trace (RAG study: scored per query
        #    against the curated file's `regulatory` expectation in compare_arms.py)
        gr = res.get("gemini_responses") or {}
        reg = gr.get("regulatory_analysis") or {}
        retrieval = reg.get("retrieval") or gr.get("regulatory_retrieval") or {}

        results_pack = {
            "query": query,
            "arm": rag_config.RETRIEVAL_MODE,
            "results_count": res.get("results_count", 0),
            "execution_time_ms": duration,
            "final_sql": res.get("filters_applied", {}).get("final_sql", ""),
            "ranking": ranking,
            "top10": [_summarize_building(b) for b in buildings_dicts[:10]],
            "ranking_logic": ranking_logic,
            "regulatory": {"response": reg.get("response"), "found": reg.get("found"),
                           "sources": reg.get("sources") or []},
            "retrieval": {k: retrieval.get(k) for k in
                          ("mode", "use_case", "use_case_routing", "n_chunks", "hits", "error")},
            "agent_trace": cleaned_trace,
            "evaluations": evaluations
        }
        
        msg = f"  -> Found {res.get('results_count', 0)} buildings in {duration}ms"
        if res.get("filters_applied", {}).get("final_sql"):
            msg += f" | SQL: {res['filters_applied']['final_sql'][:50]}..."
        log_output(msg)
        
        return results_pack
    except Exception as e: 
        log_output(f"  [!] Error: {str(e)}")
        return {"error": str(e)}

class BatchTracker:
    """Tracks progress for a specific batch of jobs."""
    def __init__(self, name: str, total: int):
        self.name = name
        self.total = total
        self.completed = 0
        self._lock = threading.Lock()

    def update(self, status: str):
        with self._lock:
            self.completed += 1
            log_output(f"[{self.name}] Progress: {self.completed}/{self.total} ({status})")

async def run_individual_job(
    idx: int, 
    df: pd.DataFrame, 
    out_dir: Path, 
    sem: asyncio.Semaphore, 
    arch: str = "multiagent", 
    disabled: Optional[List[str]] = None, 
    use_knowledge: bool = True, 
    use_relaxation: bool = False, 
    csv_path: Optional[Path] = None, 
    col: Optional[str] = None, 
    trial: Optional[int] = None, 
    batch_name: str = "",
    tracker: Optional[BatchTracker] = None
):
    """Processes a single query variation with full context management."""
    query = df.at[idx, "query"]
    query_id = df.at[idx, "query_id"] if "query_id" in df.columns else f"{idx+1:03d}"
    suffix = f"_tr{trial}" if trial else ""
    filename = f"query_{query_id}{suffix}.json"
    target_path = out_dir / filename

    # Set context variables for logging
    q_token = query_ctx.set(query)
    e_token = experiment_ctx.set(batch_name)
    l_token = log_dir_ctx.set(out_dir)
    job_id = f"[{batch_name}] query_{query_id}{suffix}"

    try:
        # Cache check
        if target_path.exists():
            if csv_path and col and df.at[idx, col] == 0:
                safe_update_csv_column(csv_path, idx, col, 1)
            if tracker: tracker.update("OK (cached)")
            return

        update_pending_job(job_id, "add")
        try:
            async with sem:
                res = await run_query(query, architecture=arch, disabled=disabled, use_knowledge=use_knowledge, use_relaxation=use_relaxation)
                status = "OK" if "error" not in res else "ERR"

                if "error" not in res:
                    res["query_id"] = str(query_id)
                    with open(target_path, "w") as f: json.dump(res, f, indent=4, ensure_ascii=False)
                    if csv_path and col:
                        safe_update_csv_column(csv_path, idx, col, 1)
                elif csv_path and col:
                    safe_update_csv_column(csv_path, idx, col, 2)

                if tracker: tracker.update(status)
        finally:
            update_pending_job(job_id, "remove")
    finally:
        query_ctx.reset(q_token)
        experiment_ctx.reset(e_token)
        log_dir_ctx.reset(l_token)

async def sync_shared_baselines(model_key: str, df_sens: pd.DataFrame, sens_csv_path: Path):
    """Reuse reference baseline results from gpt-5.4 for other models to ensure consistency and save resources."""
    if model_key == BASELINE_MODEL:
        return

    source_root = suite_root / "outputs" / "sensitivity" / BASELINE_MODEL
    target_root = suite_root / "outputs" / "sensitivity" / model_key
    baseline_configs = ["baseline_columns", "baseline_stats"]
    
    copy_count = 0
    for cid in baseline_configs:
        source_dir = source_root / cid
        if not source_dir.exists(): continue
        
        target_dir = target_root / cid
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy execution log if it exists
        if (source_dir / "execution.csv").exists() and not (target_dir / "execution.csv").exists():
            shutil.copy(source_dir / "execution.csv", target_dir / "execution.csv")

        for source_file in source_dir.glob("query_*.json"):
            target_file = target_dir / source_file.name
            if not target_file.exists():
                try:
                    shutil.copy(source_file, target_file)
                    copy_count += 1
                except Exception: pass
    
    if copy_count > 0:
        log_output(f"[*] Synced {copy_count} baseline reference results from {BASELINE_MODEL} to {model_key}")
        
        # Also sync to benchmark directory as it's used for main architecture comparison analysis
        target_bench_root = suite_root / "outputs" / "benchmarks" / model_key
        for cid in baseline_configs:
            source_dir = source_root / cid
            if not source_dir.exists(): continue
            target_bench_dir = target_bench_root / cid
            target_bench_dir.mkdir(parents=True, exist_ok=True)
            for source_file in source_dir.glob("query_*.json"):
                shutil.copy(source_file, target_bench_dir / source_file.name)

        # Update CSV status for synced items in df_sens
        updated = False
        for i, row in df_sens.iterrows():
            query_id = row["query_id"] if "query_id" in row else f"{i+1:03d}"
            for cid in baseline_configs:
                col = f"status_{model_key.replace('-', '_')}_{cid}"
                if col in df_sens.columns and df_sens.at[i, col] == 0:
                    target_file = suite_root / "outputs" / "sensitivity" / model_key / cid / f"query_{query_id}.json"
                    if target_file.exists():
                        df_sens.at[i, col] = 1
                        updated = True
        if updated:
            safe_save_csv(df_sens, sens_csv_path)

# --- 4. ANALYTICS ENGINES ---

def analyze_activation(results_dir, mapping, model_name):
    vlog.log("ACTIVATION", f"Starting activation analysis for {model_name}...")
    
    # Load agent mapping from the ground truth file
    mapping_path = suite_path / "agent_ground_truth.json"
    if not mapping_path.exists():
        vlog.log("ACTIVATION", f"  Error: Ground truth mapping not found at {mapping_path}")
        return {}
        
    with open(mapping_path) as mf:
        agent_mapping = json.load(mf)
        
    agents = sorted(list(set(agent_mapping.values())))
    metrics = {a: {"tp": 0, "fp": 0, "fn": 0} for a in agents}
    total_j, perfect = 0.0, 0
    files = list(results_dir.glob("*.json"))
    
    if not files:
        return {}

    for f in files:
        with open(f) as jf:
            data = json.load(jf)
            query = data.get("query", "").lower()
            sql = data.get("final_sql", "")
            
            # Predict agents from SQL columns (Strict activation)
            pred_agents = get_activated_agents(sql)
            
            # Expected agents from query mapping
            expected = {a for k, a in agent_mapping.items() if k.lower() in query}
            
            if not expected:
                continue

            vlog.log("ACTIVATION", f"  File: {f.name} | Exp: {expected} | Pred: {pred_agents}")
            
            for a in agents:
                is_ex, is_ac = a in expected, a in pred_agents
                if is_ex and is_ac: metrics[a]["tp"] += 1
                elif not is_ex and is_ac: metrics[a]["fp"] += 1
                elif is_ex and not is_ac: metrics[a]["fn"] += 1
            
            if pred_agents == expected: perfect += 1
            u = expected.union(pred_agents)
            j = (len(expected.intersection(pred_agents))/len(u) if u else 1.0)
            total_j += j
            
    summary = {
        "model": model_name, 
        "perfect_rate": round(perfect/len(files), 3) if files else 0, 
        "mean_jaccard": round(total_j/len(files), 3) if files else 0, 
        "mismatches": len(files)-perfect
    }
    
    agent_metrics = {}
    total_precision, total_recall, total_f1 = 0.0, 0.0, 0.0
    active_agents_count = 0

    for a, m in metrics.items():
        precision = round(m["tp"]/(m["tp"]+m["fp"]), 3) if (m["tp"]+m["fp"])>0 else 0.0
        recall = round(m["tp"]/(m["tp"]+m["fn"]), 3) if (m["tp"]+m["fn"])>0 else 0.0
        f1 = round(2*precision*recall/(precision+recall) if (precision+recall)>0 else 0.0, 3)
        agent_metrics[a] = {"precision": precision, "recall": recall, "f1": f1}
        
        # Only count agents that were expected at least once
        if (m["tp"] + m["fn"]) > 0:
            total_precision += precision
            total_recall += recall
            total_f1 += f1
            active_agents_count += 1

    summary["mean_precision"] = round(total_precision / active_agents_count, 3) if active_agents_count else 0.0
    summary["mean_recall"] = round(total_recall / active_agents_count, 3) if active_agents_count else 0.0
    summary["mean_f1"] = round(total_f1 / active_agents_count, 3) if active_agents_count else 0.0

    vlog.log("ACTIVATION", f"Summary: PerfectRate={summary['perfect_rate']} | MeanF1={summary['mean_f1']}")
    return {"summary": summary, "agent_metrics": agent_metrics}

def analyze_curated_activation(results_dir, model_name):
    """Activation on the curated set: per query, the agents that found a scorable
    requirement (graph `found_agents`) vs the item's expected_agents. Optional
    agents are neither required nor penalised. Same output shape as
    analyze_activation so the report is unchanged."""
    items = {q["id"]: q for q in load_curated_queries()}
    agents = sorted({a for q in items.values() for a in q["expected_agents"] + q.get("optional_agents", [])})
    metrics = {a: {"tp": 0, "fp": 0, "fn": 0} for a in agents}
    total_j, perfect, n = 0.0, 0, 0
    per_query = {}
    for f in sorted(results_dir.glob("query_*.json")):
        with open(f) as jf:
            data = json.load(jf)
        item = items.get(str(data.get("query_id") or f.stem[len("query_"):]))
        if not item:
            continue
        n += 1
        expected = set(item["expected_agents"])
        optional = set(item.get("optional_agents", []))
        pred = set((data.get("ranking_logic") or {}).get("discovered_agents") or [])
        for a in agents:
            if a in optional:
                continue
            is_ex, is_ac = a in expected, a in pred
            if is_ex and is_ac: metrics[a]["tp"] += 1
            elif not is_ex and is_ac: metrics[a]["fp"] += 1
            elif is_ex and not is_ac: metrics[a]["fn"] += 1
        scored_pred = pred - optional
        ok = expected <= pred and scored_pred <= expected
        perfect += ok
        u = expected | scored_pred
        total_j += len(expected & scored_pred) / len(u) if u else 1.0
        per_query[item["id"]] = {"expected": sorted(expected), "optional": sorted(optional),
                                 "found": sorted(pred), "ok": ok}
        vlog.log("ACTIVATION", f"  {item['id']} | Exp: {sorted(expected)} | Found: {sorted(pred)} | ok={ok}")
    if not n:
        return {}

    agent_metrics = {}
    for a, m in metrics.items():
        precision = round(m["tp"]/(m["tp"]+m["fp"]), 3) if (m["tp"]+m["fp"]) > 0 else 0.0
        recall = round(m["tp"]/(m["tp"]+m["fn"]), 3) if (m["tp"]+m["fn"]) > 0 else 0.0
        f1 = round(2*precision*recall/(precision+recall) if (precision+recall) > 0 else 0.0, 3)
        agent_metrics[a] = {"precision": precision, "recall": recall, "f1": f1}
    expected_once = [a for a, m in metrics.items() if m["tp"] + m["fn"] > 0]
    summary = {
        "model": model_name,
        "perfect_rate": round(perfect / n, 3),
        "mean_jaccard": round(total_j / n, 3),
        "mismatches": n - perfect,
        "mean_precision": round(np.mean([agent_metrics[a]["precision"] for a in expected_once]), 3) if expected_once else 0.0,
        "mean_recall": round(np.mean([agent_metrics[a]["recall"] for a in expected_once]), 3) if expected_once else 0.0,
        "mean_f1": round(np.mean([agent_metrics[a]["f1"] for a in expected_once]), 3) if expected_once else 0.0,
    }
    vlog.log("ACTIVATION", f"Summary: PerfectRate={summary['perfect_rate']} | MeanF1={summary['mean_f1']}")
    return {"summary": summary, "agent_metrics": agent_metrics, "per_query": per_query}

def analyze_performance(results_dir):
    """Calculates execution time statistics for queries in a directory."""
    durations = []
    for f in results_dir.glob("*.json"):
        with open(f) as jf:
            data = json.load(jf)
            if "execution_time_ms" in data:
                durations.append(data["execution_time_ms"])
    
    if not durations:
        return {"mean_ms": 0, "median_ms": 0, "sample_size": 0}
        
    return {
        "mean_ms": round(np.mean(durations), 2),
        "median_ms": round(np.median(durations), 2),
        "sample_size": len(durations)
    }

def analyze_iou_stats(results_dir):
    ids_list = []
    total, analyzed = 0, 0
    for f in sorted(results_dir.glob("*.json")):
        total += 1
        with open(f) as jf:
            data = json.load(jf)
            ids_list.append([str(i["id"]) for i in data.get("ranking", [])])
            analyzed += 1
    if not ids_list: return {"zero_iou": "0.0%", "analyzed_rate": "0.0%"}
    ious = [calculate_iou(ids_list[i], ids_list[j]) for i in range(len(ids_list)) for j in range(i+1, len(ids_list))]
    return {"zero_iou": f"{np.mean(np.array(ious) == 0)*100:.2f}%" if ious else "100.0%", "analyzed_rate": f"{analyzed/total*100:.2f}%"}

def analyze_architecture_comparison(agent_dir, baseline_dir):
    vlog.log("ARCH_COMP", f"Comparing architecture: {agent_dir.absolute()} vs {baseline_dir.absolute()}")
    sql_ious, ranking_ious = [], []
    agent_files = {f.name: f for f in agent_dir.glob("query_*.json") if "_tr" not in f.name}
    baseline_files = {f.name: f for f in baseline_dir.glob("query_*.json")}
    for name, f_a in agent_files.items():
        if name in baseline_files:
            f_b = baseline_files[name]
            with open(f_a) as fa, open(f_b) as fb:
                da, db = json.load(fa), json.load(fb)
                iou_sql = calculate_sql_iou(da.get("final_sql", ""), db.get("final_sql", ""), file_a=f_a.absolute(), file_b=f_b.absolute())
                sql_ious.append(iou_sql)
                iou_rank = calculate_iou([str(r['id']) for r in da.get('ranking', [])], [str(r['id']) for r in db.get('ranking', [])])
                ranking_ious.append(iou_rank)
                vlog.log("ARCH_COMP", f"  File: {name} | SQL IoU: {iou_sql:.3f} | Rank IoU: {iou_rank:.3f}")
    return {"mean_sql_iou": round(np.mean(sql_ious), 3) if sql_ious else None, "mean_ranking_iou": round(np.mean(ranking_ious), 3) if ranking_ious else None, "sample_size": len(sql_ious)}

def analyze_ranking_impact(multiagent_dir, no_ranking_dir):
    vlog.log("RANK_IMPACT", f"Analyzing ranking impact: {multiagent_dir.absolute()} vs {no_ranking_dir.absolute()}")
    ious = []
    ma_files = {f.name: f for f in multiagent_dir.glob("query_*.json") if "_tr" not in f.name}
    nr_files = {f.name: f for f in no_ranking_dir.glob("query_*.json")}
    for name, f_ma in ma_files.items():
        if name in nr_files:
            f_nr = nr_files[name]
            with open(f_ma) as fa, open(f_nr) as fb:
                da, db = json.load(fa), json.load(fb)
                iou = calculate_iou([str(r['id']) for r in da.get('ranking', [])], [str(r['id']) for r in db.get('ranking', [])])
                ious.append(iou)
                vlog.log("RANK_IMPACT", f"  File: {name} | Rank IoU: {iou:.3f} | Files: {f_ma.absolute()} vs {f_nr.absolute()}")
    if not ious:  # no paired results: unknown, not "no impact"
        return {"mean_iou": None, "impact": None, "sample_size": 0}
    mean_iou = np.mean(ious)
    return {"mean_iou": round(mean_iou, 3), "impact": round(1.0 - mean_iou, 3), "sample_size": len(ious)}

def analyze_knowledge_impact(multiagent_dir, no_knowledge_dir, data_stats):
    vlog.log("KNOW_IMPACT", f"Analyzing knowledge impact: {multiagent_dir.absolute()} vs {no_knowledge_dir.absolute()}")
    ious, total = [], 0
    ood_stats = {"with": [], "without": []}
    
    ma_files = {f.name: f for f in multiagent_dir.glob("query_*.json") if "_tr" not in f.name}
    nk_files = {f.name: f for f in no_knowledge_dir.glob("query_*.json")}
    
    all_names = set(ma_files.keys()).union(nk_files.keys())
    for name in all_names:
        total += 1
        d_ma, d_nk = {}, {}
        if name in ma_files:
            with open(ma_files[name]) as f: d_ma = json.load(f)
        if name in nk_files:
            with open(nk_files[name]) as f: d_nk = json.load(f)
            
        if d_ma and d_nk:
            ious.append(calculate_iou([str(r['id']) for r in d_ma.get('ranking', [])], [str(r['id']) for r in d_nk.get('ranking', [])]))
        
        if d_ma: ood_stats["with"].extend(check_sql_ood(d_ma.get("final_sql", ""), data_stats))
        if d_nk: ood_stats["without"].extend(check_sql_ood(d_nk.get("final_sql", ""), data_stats))

    def get_metrics(entries):
        if not entries: return {"rate": 0, "severity": 0, "tightness": 0}
        oods = [e for e in entries if e["is_ood"]]
        # Unique columns that had at least one OOD value across all queries
        ood_cols = set([e['col'] for e in oods])
        return {
            "rate": round(len(oods) / total if total else 0, 3), # simplified: avg OOD points per query
            "severity": round(np.mean([e["dist"] for e in oods]) if oods else 0, 3),
            "tightness": round(np.mean([e["tightness"] for e in entries]), 3)
        }

    return {
        "mean_ranking_iou": round(np.mean(ious), 3) if ious else None,
        "metrics_with": get_metrics(ood_stats["with"]),
        "metrics_without": get_metrics(ood_stats["without"])
    }

def analyze_consistency(consistency_dir):
    vlog.log("CONSISTENCY", f"Analyzing consistency in {consistency_dir.absolute()}")
    trial_ious = {}
    for f in consistency_dir.glob("query_*_tr*.json"):
        q_idx = f.name.split("_")[1]
        if q_idx not in trial_ious: trial_ious[q_idx] = []
        with open(f) as jf: 
            rank = [str(r['id']) for r in json.load(jf).get('ranking', [])]
            trial_ious[q_idx].append((f.absolute(), rank))
    
    avg_ious = []
    for q_idx, entries in trial_ious.items():
        if len(entries) < 2: continue
        pair_ious = []
        for i in range(len(entries)):
            for j in range(i+1, len(entries)):
                iou = calculate_iou(entries[i][1], entries[j][1])
                pair_ious.append(iou)
                vlog.log("CONSISTENCY", f"  Q:{q_idx} | Trial {i} vs {j} | IoU: {iou:.3f} | Files: {entries[i][0]} vs {entries[j][0]}")
        avg_ious.append(np.mean(pair_ious))
    
    res = round(np.mean(avg_ious), 3) if avg_ious else None
    vlog.log("CONSISTENCY", f"Mean Self-IoU: {res}")
    return {"mean_self_iou": res}

async def evaluate_with_judge(model_key, results_dir):
    """LLM-as-a-judge to evaluate all pros/cons quality with granular binary metrics."""
    from app.services.llm.langchain_client import get_llm
    
    # Create a dedicated directory for judge evaluations
    judge_dir = results_dir.parent / "judge_eval"
    judge_dir.mkdir(parents=True, exist_ok=True)
    
    apply_model_config(settings, EVALUATION_MODEL)
    judge_llm = get_llm()
    
    df = analysis_service._get_or_load_dataset("full")
    all_files = list(results_dir.glob("*.json"))
    
    if not all_files: return {}

    # Use a semaphore to avoid hitting judge model rate limits
    judge_sem = asyncio.Semaphore(10) 
    
    async def evaluate_single(f_path):
        out_f = judge_dir / f_path.name
        # Reuse existing evaluation if available
        if out_f.exists():
            with open(out_f) as jf: return json.load(jf)

        with open(f_path) as jf:
            data = json.load(jf)
            if not data.get("evaluations"): return None
            ev = data["evaluations"][0]
            b_id = str(ev.get("id"))
            matches = df[df['id'].astype(str) == b_id]
            if matches.empty: return None
            b = matches.iloc[0].to_dict()

        prompt = f"""
        Sei un esperto di analisi immobiliare. Valuta la qualità dei PRO e dei CONTRO generati dall'AI.
        
        QUERY UTENTE: {data['query']}
        DATI ORIGINALI IMMOBILE (Verità): {json.dumps(make_json_safe(b), ensure_ascii=False)}
        
        PRO GENERATI: {ev.get('pros')}
        CONTRO GENERATI: {ev.get('cons')}
        
        Per OGNI punto dei PRO e OGNI punto dei CONTRO, determina:
        1. ACCURACY: Il punto è supportato dai dati reali? (Sì/No)
        2. RELEVANCE: Il punto è utile rispetto a ciò che l'utente ha chiesto? (Sì/No)
        
        Rispondi ESCLUSIVAMENTE con un JSON nel seguente formato:
        {{
          "pros": [
            {{"point": "testo", "accuracy": true/false, "relevance": true/false}},
            ...
          ],
          "cons": [
            {{"point": "testo", "accuracy": true/false, "relevance": true/false}},
            ...
          ],
          "reasoning": "breve spiegazione"
        }}
        """
        try:
            async with judge_sem:
                res = await judge_llm.ainvoke(prompt)
                score_data = safe_extract_json(res.content if hasattr(res, 'content') else str(res))
                if score_data:
                    with open(out_f, "w") as out_jf: json.dump(score_data, out_jf, indent=2)
                    return score_data
        except: return None
        return None

    tasks = [evaluate_single(f) for f in all_files]
    results = await asyncio.gather(*tasks)
    results = [r for r in results if r]

    # Aggregate metrics
    all_metrics = {
        "acc_pros": [], "acc_cons": [], 
        "rel_pros": [], "rel_cons": []
    }
    
    for r in results:
        if not isinstance(r, dict): continue
        for p in r.get("pros", []):
            if not isinstance(p, dict): continue
            all_metrics["acc_pros"].append(1 if p.get("accuracy") else 0)
            all_metrics["rel_pros"].append(1 if p.get("relevance") else 0)
        for c in r.get("cons", []):
            if not isinstance(c, dict): continue
            all_metrics["acc_cons"].append(1 if c.get("accuracy") else 0)
            all_metrics["rel_cons"].append(1 if c.get("relevance") else 0)

    # Restore original model settings
    apply_model_config(settings, model_key)
    
    final = {
        "samples": len(results),
        "accuracy_pros": round(np.mean(all_metrics["acc_pros"]), 3) if all_metrics["acc_pros"] else 0,
        "accuracy_cons": round(np.mean(all_metrics["acc_cons"]), 3) if all_metrics["acc_cons"] else 0,
        "relevance_pros": round(np.mean(all_metrics["rel_pros"]), 3) if all_metrics["rel_pros"] else 0,
        "relevance_cons": round(np.mean(all_metrics["rel_cons"]), 3) if all_metrics["rel_cons"] else 0,
    }
    final["accuracy_avg"] = round((final["accuracy_pros"] + final["accuracy_cons"]) / 2, 3)
    final["relevance_avg"] = round((final["relevance_pros"] + final["relevance_cons"]) / 2, 3)
    
    vlog.log("EVAL_JUDGE", f"Judge result for {model_key}: Acc={final['accuracy_avg']}, Rel={final['relevance_avg']} over {final['samples']} samples")
    return final

def analyze_ranking_differentiation(results_dir):
    """2a: Pairwise IoU of rankings across different queries. Lower is generally better (differentiation)."""
    vlog.log("RANK_DIFF", f"Analyzing differentiation in {results_dir.absolute()}...")
    data_list = [] # List of (absolute_path, ids)
    files = sorted(results_dir.glob("query_*.json"))
    for f in files:
        if "_tr" in f.name: continue
        with open(f) as jf:
            ids = [str(i["id"]) for i in json.load(jf).get("ranking", [])]
            data_list.append((f.absolute(), ids))
    
    if len(data_list) < 2: return 1.0
    ious = []
    for i in range(len(data_list)):
        for j in range(i+1, len(data_list)):
            iou = calculate_iou(data_list[i][1], data_list[j][1])
            vlog.log("RANK_DIFF", f"    {data_list[i][0]} vs {data_list[j][0]} | IoU: {iou:.3f}")
            ious.append(iou)
    
    res = round(np.mean(ious), 3)
    vlog.log("RANK_DIFF", f"  Compared {len(data_list)} queries | Result IoU: {res}")
    return res

def analyze_rank_contribution_correlation(results_dir):
    """3a: Correlation between agent rank (from weights) and whether they contributed requirements."""
    vlog.log("RANK_CONTRIB", f"Analyzing rank-contribution correlation in {results_dir.absolute()}")
    data = []
    for f in results_dir.glob("*.json"):
        with open(f) as jf:
            res = json.load(jf)
            logic = res.get('ranking_logic', {})
            orig = logic.get('original_weights', {})
            # Use discovered_agents if available (unfiltered by weight), fallback to contributing_agents
            contribs = set(logic.get('discovered_agents', logic.get('contributing_agents', [])))
            vlog.log("RANK_CONTRIB", f"  File: {f.absolute()} | OrigWeights: {orig} | Contribs: {contribs}")
            
            # Rank agents across all 5 standard keys to evaluate zero-weight logic
            # (RankingWeights field names, as written by the graph)
            ranking_keys = ["location", "regulatory", "energy", "building", "proximity"]
            all_weights = {k: orig.get(k, 0.0) for k in ranking_keys}
            
            # Sort agents based on weights (Descending)
            sorted_agents = sorted(all_weights.items(), key=lambda x: x[1], reverse=True)
            for i, (agent, weight) in enumerate(sorted_agents):
                data.append({
                    'rank': i + 1,
                    'contributed': 1 if agent in contribs else 0,
                    'agent': agent
                })
    
    if not data: return {}
    df = pd.DataFrame(data)
    
    # Spearman correlation: Rank vs Contribution
    if df['contributed'].nunique() > 1 and df['rank'].nunique() > 1:
        rho, pval = stats.spearmanr(df['rank'], df['contributed'])
    else:
        rho, pval = 0.0, 1.0
    
    # Stats per rank and per agent
    stats_per_rank = df.groupby('rank')['contributed'].mean().to_dict()
    stats_per_agent = df.groupby('agent')['contributed'].agg(['count', 'mean']).rename(columns={'count':'total', 'mean':'rate'}).to_dict(orient='index')
    
    return {
        "spearman": {"rho": round(rho, 4), "p_value": float(pval)},
        "by_rank": stats_per_rank,
        "by_agent": stats_per_agent
    }

def compare_dirs_sql_similarity(dir_a, dir_b):
    """Compares SQL similarity between matching files in two directories."""
    vlog.log("SQL_DIR_COMP", f"Comparing SQL similarity: {dir_a.absolute()} vs {dir_b.absolute()}")
    ious = []
    files_a = {f.name: f for f in dir_a.glob("query_*.json") if "_tr" not in f.name}
    files_b = {f.name: f for f in dir_b.glob("query_*.json") if "_tr" not in f.name}
    
    for name, path_a in files_a.items():
        if name in files_b:
            path_b = files_b[name]
            with open(path_a) as fa, open(path_b) as fb:
                da, db = json.load(fa), json.load(fb)
                iou = calculate_sql_iou(da.get("final_sql", ""), db.get("final_sql", ""), file_a=path_a.absolute(), file_b=path_b.absolute())
                ious.append(iou)
    
    return round(np.mean(ious), 3) if ious else 0.0

def generate_report(bench, sens, models):
    """Generate the definitive multi-section technical report."""
    report_md = f"# Multi-Agent Real Estate Analysis: Technical Evaluation Report\n"
    report_md += f"*Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  \n"
    report_md += f"*Query set: {QUERY_SET} · regulatory retrieval arm: {rag_config.RETRIEVAL_MODE}*\n\n"

    completed = [m for m in models if m in bench and "activation" in bench[m]]
    if not completed: return report_md + "\n*Waiting for results...*\n"

    for mod in completed:
        report_md += f"## Analysis for Model: `{mod}`\n\n"
        
        # --- SECTION 1: ROUTING & ACTIVATION ---
        report_md += "### 1. Ground Truth & Agent Activation\n"
        report_md += "Verifica se gli agenti specialisti si attivano coerentemente con il contenuto della query (Expected Activations).\n\n"
        
        s = bench[mod]["activation"]["summary"]
        report_md += f"**Global Performance:**\n"
        report_md += f"- Perfect Mapping Rate: `{s['perfect_rate']*100:.1f}%` (Exact match between expected/actual agents)\n"
        report_md += f"- Mean Jaccard Score: `{s['mean_jaccard']:.3f}`\n"
        report_md += f"- Classification Mismatches: `{s['mismatches']}`\n\n"
        
        report_md += "| Agent | Precision | Recall | F1-Score |\n| :--- | :---: | :---: | :---: |\n"
        for ag, met in bench[mod]["activation"]["agent_metrics"].items():
            report_md += f"| {ag.capitalize()} | {met['precision']:.3f} | {met['recall']:.3f} | {met['f1']:.3f} |\n"
        
        # --- SECTION 2: RANKING DYNAMICS ---
        report_md += "\n### 2. Ranking Dynamics & Consistency\n"
        report_md += "Analisi della differenziazione, impatto dei pesi e stabilità deterministica.\n\n"
        
        # Differentiation (2a)
        diff_score = analyze_ranking_differentiation(suite_root / "outputs" / "benchmarks" / mod / "full")
        report_md += f"**A. Ranking Differentiation (IoU across queries):** `{diff_score:.3f}`\n"
        report_md += "> Una IoU bassa indica che query diverse producono ranking significativamente diversi (buona specificità).\n\n"

        # Ablation Impact (2b) - from sensitivity results
        report_md += "**B. Agent Sensitivity (Ranking IoU: Full vs Ablation):**\n"
        report_md += "> Misura quanto il ranking resta simile (IoU) rimuovendo un componente.\n\n"
        report_md += "| Ablation Component | Ranking IoU (vs Full) |\n| :--- | :---: |\n"
        
        # Values from sensitivity_results
        sens_data = sens.get(mod, {}).get("sensitivity", {})
        agents_map = {"no_location": "Location", "no_proximity": "Proximity/POI", "no_building": "Building/Technical", "no_energy": "Energy/APE", "no_regulatory": "Regulatory"}
        for k, label in agents_map.items():
            report_md += f"| {label} | {_fmt_metric(sens_data.get(k))} |\n"
        
        # No Ranking Impact (2c)
        ri = bench[mod].get("ranking_impact", {})
        report_md += f"\n**C. Weighting Similarity (`Full` vs `No Ranking`):** `{_fmt_metric(ri.get('mean_iou'))}` (IoU)\n"
        report_md += "> Somiglianza tra ranking pesato dall'agente e ranking a pesi uniformi.\n\n"
        
        # Consistency (2d)
        co = bench[mod].get("consistency", {})
        report_md += f"**D. Model Consistency (Self-IoU across 3 trials):** `{_fmt_metric(co.get('mean_self_iou'))}`\n\n"
        
        # --- SECTION 3: SQL & KNOWLEDGE ---
        report_md += "### 3. SQL Synthesis & Knowledge Integration\n"
        report_md += "Analisi della qualità della generazione SQL e della 'data awareness'.\n\n"
        
        # Rank/Contribution Correlation (3a)
        corr = analyze_rank_contribution_correlation(suite_root / "outputs" / "benchmarks" / mod / "full")
        if corr:
            report_md += f"**A. Rank-Requirement Correlation:** Spearman ρ = `{corr['spearman']['rho']:.3f}` (p={corr['spearman']['p_value']:.4f})\n"
            report_md += "> Correlazione tra il rank assegnato dal modello (basato sui pesi) e l'effettiva presenza di requisiti estratti (Discovery Rate). Un valore negativo indica che gli agenti con rank più alto (1, 2) contribuiscono più spesso di quelli con rank basso (4, 5).\n\n"
            report_md += "**Requirement Discovery Rate by Weight Rank:**\n"
            rank_stats = " | ".join([f"Rank {r}: **{val*100:.1f}%**" for r, val in sorted(corr['by_rank'].items())])
            report_md += f"> {rank_stats}\n\n"
        
        # Architecture SQL Sim (3b) — baselines exist only in the legacy study
        if QUERY_SET == "legacy":
            fs = suite_root / "outputs" / "sensitivity" / mod / "all_enabled"
            bc = suite_root / "outputs" / "sensitivity" / mod / "baseline_columns"
            bs = suite_root / "outputs" / "sensitivity" / mod / "baseline_stats"
            sim_ma_bc = compare_dirs_sql_similarity(fs, bc)
            sim_ma_bs = compare_dirs_sql_similarity(fs, bs)
            sim_bs_bc = compare_dirs_sql_similarity(bs, bc)

            report_md += "**B. SQL Similarity Matrix (IoU):**\n"
            report_md += f"| Architecture Pair | Similarity (IoU) |\n| :--- | :---: |\n"
            report_md += f"| Multi-Agent vs Baseline (Columns Only) | {sim_ma_bc:.3f} |\n"
            report_md += f"| Multi-Agent vs Baseline (with Stats) | {sim_ma_bs:.3f} |\n"
            report_md += f"| Baseline Stats vs Baseline Columns | {sim_bs_bc:.3f} |\n\n"
        else:
            report_md += "**B. SQL Similarity Matrix:** n/a (baselines are not run in the curated RAG study)\n\n"
        
        # Knowledge Impact (3c)
        ki = bench[mod].get("knowledge_impact", {})
        mw, mwo = ki.get("metrics_with", {}), ki.get("metrics_without", {})
        
        report_md += f"**C. Knowledge Impact & Data Awareness:**\n"
        report_md += f"- SQL Alignment (`Full` vs `No Knowledge`): `{_fmt_metric(ki.get('mean_ranking_iou'))}` (Ranking IoU)\n\n"
        
        report_md += "| Metric | Multi-Agent (Full Knowledge) | Multi-Agent (No Knowledge) |\n"
        report_md += "| :--- | :---: | :---: |\n"
        report_md += f"| Out-of-Distribution Rate | {mw.get('rate',0)*100:.1f}% | {mwo.get('rate',0)*100:.1f}% |\n"
        report_md += f"| OOD Mean Severity | {mw.get('severity',0):.3f} | {mwo.get('severity',0):.3f} |\n"
        report_md += f"| Constraint Tightness | {mw.get('tightness',0):.3f} | {mwo.get('tightness',0):.3f} |\n\n"
        
        report_md += "> **Severity**: Distanza media normalizzata dei valori OOD dal range reale (0=nessuno, >1=estremo).  \n"
        report_md += "> **Tightness**: Posizionamento medio dei filtri (0=min, 1=max). Più il valore è simile tra i due scenari, più il modello possiede un 'senso' innato dei dati a prescindere dalle statistiche.\n\n"
        
        # --- SECTION 4: QUALITATIVE EVALUATION ---
        report_md += "### 4. Qualitative Evaluation\n"
        report_md += "Analisi automatizzata della qualità e attendibilità dei Pro/Contro generati.\n\n"
        
        eq = bench[mod].get("eval_quality", {})
        report_md += f"**LLM-as-a-Judge Quality Scores:** (Analyzed all N={eq.get('samples', 0)} queries)\n"
        
        report_md += "| Metric | Pros | Cons | **Average** |\n"
        report_md += "| :--- | :---: | :---: | :---: |\n"
        report_md += f"| Accuracy (Fact-based) | {eq.get('accuracy_pros',0)*100:.1f}% | {eq.get('accuracy_cons',0)*100:.1f}% | **{eq.get('accuracy_avg',0)*100:.1f}%** |\n"
        report_md += f"| Relevance (User Query) | {eq.get('relevance_pros',0)*100:.1f}% | {eq.get('relevance_cons',0)*100:.1f}% | **{eq.get('relevance_avg',0)*100:.1f}%** |\n\n"
        
        report_md += "> Valutazione binaria di aderenza ai dati (Fact-checking) e utilità rispetto alla query specifica dell'utente.\n\n"
        
        # --- SECTION 5: PERFORMANCE ---
        report_md += "### 5. Performance Analysis\n"
        report_md += "Analisi delle latenze medie ed effettiva velocità di risposta.\n\n"
        
        p = bench[mod].get("performance", {})
        report_md += f"| Metric | Value |\n| :--- | :---: |\n"
        report_md += f"| Average Latency | **{p.get('mean_ms', 0)/1000:.2f}s** ({p.get('mean_ms', 0):.0f}ms) |\n"
        report_md += f"| Median Latency | {p.get('median_ms', 0)/1000:.2f}s ({p.get('median_ms', 0):.0f}ms) |\n"
        report_md += f"| Samples Analyzed | {p.get('sample_size', 0)} |\n\n"

        report_md += "---\n"

    return report_md

# --- 6. MAIN WORKFLOW ---

def generate_compositions(json_path, output_path, ablation=False):
    with open(json_path) as f: data = json.load(f)
    keys = ["tipologia_immobile", "punto_di_interesse", "metratura_totale", "classe_energetica", "progetto_destinazione_uso", "servizi_accessori"]
    
    # opts will be a list of lists: [[(choice, index), ...], ...]
    # index is 1-based, 0 is for None
    opts = []
    for k in keys:
        choices = []
        # The ablation suite (sensitivity) must only include complete queries (all variables present)
        if ablation:
            choices = [(val, i+1) for i, val in enumerate(data[k])]
        # The benchmark suite includes partial queries, but 'tipologia_immobile' is always mandatory
        elif k == "tipologia_immobile":
            choices = [(val, i+1) for i, val in enumerate(data[k])]
        else:
            choices = [(None, 0)] + [(val, i+1) for i, val in enumerate(data[k])]
        opts.append(choices)

    rows = []
    for c in itertools.product(*opts):
        # c is a tuple of (value, index) tuples
        indices = "".join(str(item[1]) for item in c)
        t, p, m, cl, pr, s = [item[0] for item in c]
        
        # Skip empty queries if everything is None (should not happen as tipologia_immobile is mandatory)
        if t is None: continue

        q = f"Cerca un {t}" if t else "Cerca un immobile"
        if p: q += f" vicino a {p}"
        if m: q += f" con superficie {m}"
        if cl: q += f" in classe {cl}"
        if pr: q += f", finalizzato a {pr}"
        if s: q += f" e situato vicino a {s}"
        
        row = {"query_id": indices, "query": q}
        rows.append(row)

    rows.sort(key=lambda x: len(x["query"]))
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)


def sync_curated_csv(csv_path: Path, items: List[Dict[str, Any]], outputs_root: Path):
    """(Re)build a curated suite CSV from the query file. Status columns are kept
    for rows whose (query_id, query) is unchanged; result files of an id whose
    text changed are deleted, so a stale answer is never reused from cache.
    Ids no longer in the file are removed by prune_obsolete_results."""
    new = pd.DataFrame([{"query_id": q["id"], "query": q["query"]} for q in items])
    if csv_path.exists():
        old = pd.read_csv(csv_path, dtype={"query_id": str})
        old_text = dict(zip(old["query_id"], old["query"]))
        changed = {qid for qid, text in zip(new["query_id"], new["query"])
                   if qid in old_text and old_text[qid] != text}
        for qid in changed:
            for f in outputs_root.rglob(f"query_{qid}*.json"):
                if re.fullmatch(rf"query_{re.escape(qid)}(?:_tr\d+)?\.json", f.name):
                    f.unlink()
        if changed:
            log_output(f"[*] Query text changed for {sorted(changed)}: cached results removed")
        status_cols = [c for c in old.columns if c.startswith("status_")]
        if status_cols:
            keep = old[~old["query_id"].isin(changed)].set_index("query_id")[status_cols]
            new = new.join(keep, on="query_id")
            new[status_cols] = new[status_cols].fillna(0).astype(int)
    safe_save_csv(new, csv_path)


def prepare_suites() -> Tuple[Path, Path]:
    """Benchmark and sensitivity suite CSVs for the configured query set."""
    if QUERY_SET == "curated":
        bench_csv = suite_root / "benchmark_queries_suite.csv"
        sens_csv = suite_root / "sensitivity_queries_suite.csv"
        items = load_curated_queries()
        sync_curated_csv(bench_csv, [q for q in items if "benchmark" in q["suites"]], suite_root / "outputs" / "benchmarks")
        sync_curated_csv(sens_csv, [q for q in items if "sensitivity" in q["suites"]], suite_root / "outputs" / "sensitivity")
    else:
        pos_json = suite_path / "query_parameters.json"
        bench_csv = suite_root / "combinatorial_queries_suite.csv"
        sens_csv = suite_root / "sensitivity_queries_suite.csv"
        if not bench_csv.exists(): generate_compositions(pos_json, bench_csv)
        if not sens_csv.exists(): generate_compositions(pos_json, sens_csv, ablation=True)
    return bench_csv, sens_csv


async def compute_consensus_results(model: str, df: pd.DataFrame, out_root: Path):
    """Determine the most frequent result among trials and populate the 'full' config directory."""
    cons_dir = out_root / "consistency"
    full_dir = out_root / "full"
    full_dir.mkdir(parents=True, exist_ok=True)
    
    log_output(f"[*] Computing consensus for {model}...")
    
    for i, row in df.iterrows():
        query_id = row["query_id"] if "query_id" in row else f"{i+1:03d}"
        results = []
        for tr in [1, 2, 3]:
            tr_file = cons_dir / f"query_{query_id}_tr{tr}.json"
            if tr_file.exists():
                with open(tr_file) as f: results.append(json.load(f))
        
        if not results: continue

        # Simple consensus: Compare ranking IDs
        def get_rank_sig(res):
            return tuple(r.get("id") for r in res.get("ranking", []))
        
        signatures = [get_rank_sig(r) for r in results]
        # Find most frequent signature
        most_common_sig = max(set(signatures), key=signatures.count)
        
        # Pick the first result that matches the most common signature
        winner = next(r for r in results if get_rank_sig(r) == most_common_sig)
        
        target_file = full_dir / f"query_{query_id}.json"
        with open(target_file, "w") as f:
            json.dump(winner, f, indent=4)

def prune_obsolete_results(df: pd.DataFrame, out_root: Path):
    """Removes JSON results that no longer correspond to a query in the suite (CSV)."""
    if not out_root.exists() or "query_id" not in df.columns:
        return

    valid_ids = set(df["query_id"].astype(str))
    log_output(f"[*] Pruning obsolete results in {out_root}")
    
    removed_count = 0
    # Search in all subdirectories recursively
    for json_file in out_root.rglob("query_*.json"):
        # Handle formats like "query_111000.json", "query_111000_tr1.json", "query_PQ-01_tr1.json"
        match = re.fullmatch(r"query_([A-Za-z0-9-]+?)(?:_tr\d+)?\.json", json_file.name)
        if match:
            qid = match.group(1)
            if qid not in valid_ids:
                try:
                    json_file.unlink()
                    removed_count += 1
                except Exception as e:
                    log_output(f"  [!] Failed to delete {json_file.name}: {e}")
    
    if removed_count > 0:
        log_output(f"  [-] Removed {removed_count} obsolete JSON files.")

async def sync_sensitivity_with_benchmark(df_sens: pd.DataFrame, df_bench: pd.DataFrame, model_key: str):
    """Reuse benchmark results for sensitivity common configurations to avoid re-runs."""
    # Maps query text to benchmark index
    query_to_bench_idx = {row['query']: i for i, row in df_bench.iterrows()}
    
    configurations_to_sync = [
        ("all_enabled", "full")
    ]
    
    bench_root = suite_root / "outputs" / "benchmarks" / model_key
    sens_root = suite_root / "outputs" / "sensitivity" / model_key
    
    for sens_cid, bench_cid in configurations_to_sync:
        sens_col = f"status_{model_key.replace('-', '_')}_{sens_cid}"
        bench_col = f"status_{model_key.replace('-', '_')}_{bench_cid}"
        
        if bench_col not in df_bench.columns: continue
        if sens_col not in df_sens.columns: df_sens[sens_col] = 0
        
        bench_dir = bench_root / bench_cid
        sens_dir = sens_root / sens_cid
        sens_dir.mkdir(parents=True, exist_ok=True)

        # Copy execution log if it exists in benchmark but not in sensitivity
        import shutil
        bench_log = bench_dir / "execution.csv"
        sens_log = sens_dir / "execution.csv"
        if bench_log.exists() and not sens_log.exists():
            shutil.copy(bench_log, sens_log)
        
        for i, row in df_sens.iterrows():
            query = row['query']
            if query in query_to_bench_idx:
                b_idx = query_to_bench_idx[query]
                bench_query_id = df_bench.at[b_idx, "query_id"] if "query_id" in df_bench.columns else f"{b_idx+1:03d}"
                sens_query_id = row["query_id"] if "query_id" in row else f"{i+1:03d}"
                
                # Check if benchmark is done
                if df_bench.at[b_idx, bench_col] == 1:
                    bench_file = bench_dir / f"query_{bench_query_id}.json"
                    sens_file = sens_dir / f"query_{sens_query_id}.json"
                    if bench_file.exists() and not sens_file.exists():
                        # Symbolic link or copy. Copy is safer for portability.
                        shutil.copy(bench_file, sens_file)
                    if sens_file.exists():
                        df_sens.at[i, sens_col] = 1

async def run_single_model_suite(model: str, max_concurrent: int, limit: int = None, ids: List[str] = None):
    """Execution logic for a single model with optimized non-redundant workflow."""
    await preload_data()
    data_stats = get_data_stats()
    sem = asyncio.Semaphore(max_concurrent)
    csv_lock = asyncio.Lock()

    log_output(f"\n" + "="*50)
    log_output(f"=== [START] MODEL: {model} | queries={QUERY_SET} | arm={rag_config.RETRIEVAL_MODE} | {suite_root} ===")
    log_output(f"="*50)

    bench_csv, sens_csv = prepare_suites()

    df_bench = safe_read_csv(bench_csv)
    df_sens = safe_read_csv(sens_csv)

    apply_model_config(settings, model)
    # Force re-initialization of the agents with the new model settings
    analysis_service._init_graph_agent(force=True)
    out_root_bench = suite_root / "outputs" / "benchmarks" / model
    out_root_sens = suite_root / "outputs" / "sensitivity" / model

    # Prune results whose query is no longer in the suite file. Done on the
    # full suite, before --limit / --ids, so a smoke run never deletes results.
    prune_obsolete_results(df_bench, out_root_bench)
    prune_obsolete_results(df_sens, out_root_sens)

    # Row filters keep the original index, so status updates hit the right CSV rows.
    if ids:
        log_output(f"[*] Restricting to query ids: {ids}")
        df_bench = df_bench[df_bench["query_id"].astype(str).isin(ids)].copy()
        df_sens = df_sens[df_sens["query_id"].astype(str).isin(ids)].copy()
    if limit:
        log_output(f"[*] Applying sampling limit: {limit} queries per suite")
        df_bench = df_bench.head(limit).copy()
        df_sens = df_sens.head(limit).copy()

    # Clear old execution logs to start fresh in each run
    for d in [out_root_bench, out_root_sens]:
        if d.exists():
            for csv_file in d.rglob("execution.csv"):
                try:
                    csv_file.unlink(missing_ok=True)
                except Exception:
                    pass

    # Baselines (single-LLM planner) belong to Marco's legacy study only; the
    # curated RAG study skips them, and there gpt-5.4 is an ordinary model.
    baseline_master = (QUERY_SET == "legacy" and model == BASELINE_MODEL)
    if QUERY_SET == "legacy":
        # Sync shared baselines from Master Model before starting execution wave
        await sync_shared_baselines(model, df_sens, sens_csv)
        # Reload df_sens as it might have been updated by sync_shared_baselines
        # (same rows as before: --limit / --ids stay applied)
        df_sens = safe_read_csv(sens_csv).loc[df_sens.index]

    # --- PHASE 1 & 3: GROUPED PER-QUERY EXECUTION ---
    log_output(f"[*] Starting unified grouped execution wave (Per-Query) for {model}...")

    # Define configurations to run
    sens_configs = list(SENSITIVITY_CONFIGS)

    if baseline_master:
        sens_configs = [c for c in sens_configs if c[0].startswith("baseline")]
    else:
        sens_configs = [c for c in sens_configs if not c[0].startswith("baseline")]
    if QUERY_SET == "curated" and rag_config.RETRIEVAL_MODE != "rag":
        sens_configs = []   # ablations run only in the rag arm (RAG study design)

    from collections import defaultdict
    jobs_by_query = defaultdict(list)
    trackers = {}

    # 1. Collect Sensitivity Jobs
    for cid, dis, kn, arch in sens_configs:
        col = f"status_{model.replace('-', '_')}_{cid}"
        if col not in df_sens.columns: df_sens[col] = 0
        out_dir = out_root_sens / cid
        out_dir.mkdir(parents=True, exist_ok=True)
        pending = df_sens[df_sens[col].isin([0, 2])].index.tolist()
        
        batch_name = f"{model}-SENS-{cid.upper()}"
        trackers[batch_name] = BatchTracker(batch_name, len(pending))
        
        for idx in pending:
            query_text = df_sens.at[idx, 'query']
            jobs_by_query[query_text].append({
                'idx': idx, 'df': df_sens, 'out_dir': out_dir, 'arch': arch, 'disabled': dis, 
                'use_knowledge': kn, 'csv_path': sens_csv, 'col': col, 'batch_name': batch_name, 'trial': None
            })

    # 2. Collect Benchmark Consistency Jobs
    if not baseline_master:
        for tr in [1, 2, 3]:
            out_dir = out_root_bench / "consistency"
            out_dir.mkdir(parents=True, exist_ok=True)
            col = f"status_{model.replace('-', '_')}_consistency_tr{tr}"
            if col not in df_bench.columns: df_bench[col] = 0
            pending = df_bench[df_bench[col].isin([0, 2])].index.tolist()
            
            batch_name = f"{model}-CONS-TR{tr}"
            trackers[batch_name] = BatchTracker(batch_name, len(pending))
            
            for idx in pending:
                query_text = df_bench.at[idx, 'query']
                jobs_by_query[query_text].append({
                    'idx': idx, 'df': df_bench, 'out_dir': out_dir, 'arch': 'multiagent', 'disabled': None, 
                    'use_knowledge': True, 'csv_path': bench_csv, 'col': col, 'batch_name': batch_name, 'trial': tr
                })

    # Flatten jobs grouped by query to preserve temporal/data locality
    all_tasks = []
    for query_text in jobs_by_query:
        for job_params in jobs_by_query[query_text]:
            tracker = trackers.get(job_params['batch_name'])
            all_tasks.append(run_individual_job(
                **job_params, sem=sem, tracker=tracker
            ))

    if all_tasks:
        await asyncio.gather(*all_tasks)

    # --- FINALIZATION: CONSENSUS & SYNC ---
    log_output("[*] Finalizing results (Consensus & Sync)...")
    
    # Reload results from disk (updated by concurrent processes)
    df_bench = safe_read_csv(bench_csv)
    df_sens = safe_read_csv(sens_csv)

    # Compute consensus for benchmark
    await compute_consensus_results(model, df_bench, out_root_bench)
    full_col = f"status_{model.replace('-', '_')}_full"
    if full_col not in df_bench.columns: df_bench[full_col] = 0
    full_dir = out_root_bench / "full"
    for i, row in df_bench.iterrows():
        query_id = row["query_id"] if "query_id" in row else f"{i+1:03d}"
        if (full_dir / f"query_{query_id}.json").exists():
            df_bench.at[i, full_col] = 1
    safe_save_csv(df_bench, bench_csv)

    # Sync benchmark 'full' config to sensitivity 'all_enabled'
    await sync_sensitivity_with_benchmark(df_sens, df_bench, model)
    safe_save_csv(df_sens, sens_csv)
    
    log_output(f"=== [COMPLETE] Queries for {model} finished. ===")

async def conductor_main(max_concurrent: int, only_analysis: bool = False, limit: int = None,
                         models: List[str] = None, run_flags: List[str] = None):
    """Main orchestrator that manages model processes and generates final reports.
    ``run_flags`` (--queries / --arm / --ids) are forwarded to every model subprocess."""
    mapping_json = suite_path / "agent_ground_truth.json"
    with open(mapping_json) as f: mapping = json.load(f)
    await preload_data()
    data_stats = get_data_stats()
    run_flags = run_flags or []

    if not models:
        models = ["gpt-oss-120b", "gemma3-27b", "qwen3-8b"]
    
    # 1. PREPARE SUITES (also in analysis-only mode, for reference)
    bench_csv, sens_csv = prepare_suites()
    
    if not only_analysis:
        # 1. Run Master Baseline (gpt-5.4) first to ensure reference results exist
        #    (legacy study only: the curated RAG study skips baselines)
        if QUERY_SET == "legacy":
            log_output(f"[CONDUCTOR] Ensuring Master Baseline ({BASELINE_MODEL}) is complete...")
            m_concurrency = get_model_concurrency(BASELINE_MODEL, max_concurrent)
            base_cmd = [sys.executable, __file__, "--model", BASELINE_MODEL, "--max-concurrent", str(m_concurrency)] + run_flags
            if limit is not None: base_cmd.extend(["--limit", str(limit)])
            p_base = await asyncio.create_subprocess_exec(*base_cmd)
            await p_base.wait()

        # 2. RUN ALL MODELS IN PARALLEL
        log_output(f"[CONDUCTOR] Launching {len(models)} model benchmark processes in parallel...")
        processes = []
        for m in models:
            m_concurrency = get_model_concurrency(m, max_concurrent)
            log_output(f"[CONDUCTOR] -> Starting {m} (max_concurrent={m_concurrency})")
            m_cmd = [sys.executable, __file__, "--model", m, "--max-concurrent", str(m_concurrency)] + run_flags
            if limit is not None: m_cmd.extend(["--limit", str(limit)])
            p = await asyncio.create_subprocess_exec(*m_cmd)
            processes.append(p)
        
        await asyncio.gather(*(p.wait() for p in processes))
    else:
        log_output("[CONDUCTOR] SKIPPING execution phase (using existing results).")

    # 4. AGGREGATE ANALYSIS & GENERATE REPORT
    log_output("[CONDUCTOR] All processes finished. Running final analysis...")
    
    benchmark_results = {}
    sensitivity_results = {}
    report_models = models

    for model in report_models:
        out_root = suite_root / "outputs" / "benchmarks" / model
        out_sens = suite_root / "outputs" / "sensitivity" / model
        
        if not (out_root / "full").exists():
            log_output(f"[!] Warning: No results found for {model}. Skipping analysis.")
            continue

        # Run model-specific analysis
        log_output(f"[*] Analyzing results for {model}...")
        
        # Benchmark Analysis
        benchmark_results[model] = {
            "performance": analyze_performance(out_root / "full"),
            "activation": (analyze_curated_activation(out_root / "full", model) if QUERY_SET == "curated"
                           else analyze_activation(out_root / "full", mapping, model)),
            "iou": analyze_iou_stats(out_root / "full"),
            "arch_comp": analyze_architecture_comparison(out_root / "full", out_root / "baseline_stats"),
            "baseline_impact": analyze_architecture_comparison(out_root / "baseline_stats", out_root / "baseline_columns"),
            # no_ranking / no_knowledge are sensitivity configs: they are written
            # under sensitivity/<model>/, so compare them with that suite's
            # all_enabled run (same queries), not with benchmarks/<model>/full.
            "ranking_impact": analyze_ranking_impact(out_sens / "all_enabled", out_sens / "no_ranking"),
            "knowledge_impact": analyze_knowledge_impact(out_sens / "all_enabled", out_sens / "no_knowledge", data_stats),
            "consistency": analyze_consistency(out_root / "consistency"),
            "eval_quality": await evaluate_with_judge(model, out_root / "full")
        }
        benchmark_results[model]["arch_comp"]["baseline_model"] = "baseline_stats"

        # Sensitivity Analysis
        sens_configs = [cid for cid, _, _, arch in SENSITIVITY_CONFIGS if arch == "multiagent"]
        sens_vals = {}
        all_enabled_dir = out_sens / "all_enabled"
        if all_enabled_dir.exists():
            base_ref = {f.name: [r['id'] for r in json.load(open(f))['ranking']] for f in all_enabled_dir.glob("*.json")}
            for cid in sens_configs:
                cfg_dir = out_sens / cid
                if cfg_dir.exists():
                    cfg_res = {f.name: [r['id'] for r in json.load(open(f))['ranking']] for f in cfg_dir.glob("*.json")}
                    ious = [calculate_iou(base_ref[n], cfg_res[n]) for n in cfg_res if n in base_ref]
                    sens_vals[cid] = np.mean(ious) if ious else None
        
        sensitivity_results[model] = {"sensitivity": sens_vals}

    # Final report
    report = generate_report(benchmark_results, sensitivity_results, report_models)
    with open(suite_root / "report.md", "w") as f:
        f.write(report)
    
    log_output(f"\nWorkflow complete. Final report: {suite_root / 'report.md'}")
    log_output(f"Detailed execution log: {execution_csv_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthetic Test Suite - Parallel Runner")
    parser.add_argument("--model", type=str, help="Run only a specific model")
    parser.add_argument("--only-analysis", action="store_true", help="Only run analysis on existing results")
    parser.add_argument("--max-concurrent", type=int, default=48, help="Max concurrent queries per model")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of queries per model")
    parser.add_argument("--models", type=str, help="Comma-separated list of models to run")
    parser.add_argument("--queries", choices=["curated", "legacy"], default="curated",
                        help="curated = benchmark_queries_turin.json (RAG study); legacy = Marco's generator")
    parser.add_argument("--arm", choices=list(rag_config.RETRIEVAL_MODES), default=None,
                        help="regulatory retrieval arm (default: RAG_RETRIEVAL_MODE, i.e. rag)")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated query ids to run (smoke runs)")
    args = parser.parse_args()

    configure_run(args.queries, args.arm)
    ids = [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None

    if args.model:
        # Use model-specific concurrency if the user didn't explicitly override it from CLI
        # (Assuming 48 is the default to detect 'unspecified' state)
        m_concurrency = args.max_concurrent
        if m_concurrency == 48:
            m_concurrency = get_model_concurrency(args.model, 48)
            
        # Run a single model suite (to be called as a subprocess or manually)
        asyncio.run(run_single_model_suite(args.model, m_concurrency, limit=args.limit, ids=ids))
    else:
        # Launch the conductor
        selected_models = args.models.split(",") if args.models else None
        run_flags = ["--queries", args.queries] + (["--arm", args.arm] if args.arm else []) \
            + (["--ids", args.ids] if args.ids else [])
        asyncio.run(conductor_main(args.max_concurrent, args.only_analysis, limit=args.limit,
                                   models=selected_models, run_flags=run_flags))
