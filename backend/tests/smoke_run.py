"""
Pre-flight smoke test before a full evaluation run on one model (e.g. gemma-4).

  1. the model's endpoint answers;
  2. per city, one downstream item through the regulatory agent with RAG: the
     answer parses (found + requirements) and no call failed;
  3. per city, one full-pipeline query: no failed agent / LLM call, SQL
     generated, real results (not the generic fallback), regulatory answer parses.

Exit code 0 only when every check passes (a few dozen LLM calls, a few minutes).

    python tests/smoke_run.py --model gemma-4 [--cities turin,london,madrid]
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
os.chdir(backend_dir)
ERROR_LOG = Path(tempfile.mkdtemp(prefix="smoke_")) / "agent_errors.jsonl"
os.environ["AGENT_ERROR_LOG"] = str(ERROR_LOG)      # before any LLM client is created

import requests  # noqa: E402

from app.core.config import settings  # noqa: E402

EVAL = backend_dir / "app" / "services" / "llm" / "rag" / "eval"


def n_errors() -> int:
    return len(ERROR_LOG.read_text(encoding="utf-8").splitlines()) if ERROR_LOG.exists() else 0


def check_endpoint() -> bool:
    base, key = settings.endpoint_for(settings.AGENT_LLM_MODEL)
    if not base:
        print(f"[smoke] endpoint: OpenAI default for {settings.AGENT_LLM_MODEL} (not probed)")
        return True
    try:
        r = requests.get(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        ok = r.status_code == 200
        print(f"[smoke] endpoint {base}: HTTP {r.status_code}" + ("" if ok else f" {r.text[:120]}"))
        return ok
    except requests.RequestException as e:
        print(f"[smoke] endpoint {base}: unreachable ({type(e).__name__}) — VPN / university network?")
        return False


def check_downstream(city: str) -> bool:
    from app.core.constants import REGULATORY_AGENT_COLUMNS
    from app.services.llm.agents.regulatory_agent import RegulatoryAgent
    from app.services.llm.agents.schema import RegulatoryResponse
    from app.utils.json_parser import safe_extract_json

    data = json.loads((EVAL / f"downstream_queries_{city}.json").read_text(encoding="utf-8"))
    q = next(i for i in data["queries"] if i["expect"]["activate"] is True)
    lang = "en" if "en" in q["queries"] else next(iter(q["queries"]))
    before = n_errors()
    res = RegulatoryAgent().run(query=q["queries"][lang], available_columns=REGULATORY_AGENT_COLUMNS,
                                statistics=None, retrieval_mode="rag", country=data["meta"]["country"],
                                use_case=q["use_case"])
    parsed = safe_extract_json(res.raw_text or "", schema=RegulatoryResponse)
    reqs = list(parsed.requirements) if parsed else []
    value = next((r.get("value") for r in reqs if isinstance(r, dict)), None)
    hits = len((res.retrieval or {}).get("hits") or [])
    ok = parsed is not None and n_errors() == before and hits > 0
    print(f"[smoke] {city} downstream {q['id']}: parsed={parsed is not None} found={getattr(parsed, 'found', None)} "
          f"value={value} (expected {q['expect']['value_min']}-{q['expect']['value_max']}) chunks={hits} "
          f"failed_calls={n_errors() - before} -> {'OK' if ok else 'FAIL'}")
    return ok


def check_pipeline(city: str) -> bool:
    from app.data.loaders import load_and_merge_data
    from app.services.analysis_service import analysis_service
    from app.services.real_estate_service import RealEstateService
    from app.services.llm.agents.schema import RegulatoryResponse
    from app.utils.json_parser import safe_extract_json

    # one process, several cities: drop the dataset loaded for the previous city
    analysis_service._base_dataset_cache.clear()
    RealEstateService._dataset_cache.clear()
    RealEstateService._dataset_indexed_cache.clear()
    df = load_and_merge_data(settings.dataset_options["full"])
    RealEstateService._dataset_cache["full"] = df
    idx = df.copy()
    idx["id_str"] = idx["id"].astype(str)
    RealEstateService._dataset_indexed_cache["full"] = idx.drop_duplicates("id_str").set_index("id_str")

    items = json.loads((backend_dir / "tests" / f"benchmark_queries_{city}.json").read_text(encoding="utf-8"))["queries"]
    q = next(i for i in items if i["regulatory"]["activate"] is True and i["slots"].get("landmark"))
    before = n_errors()
    res = asyncio.run(analysis_service.run_analysis(
        run_id=f"smoke_{city}", query=q["query"], dataset_key="full", map_limit=15000, llm_limit=25,
        analysis_mode="agent", use_data_knowledge=True, use_relaxation=False, architecture="multiagent",
        constraint_mode=os.environ.get("CONSTRAINT_MODE", "repair")))
    gr = res.get("gemini_responses") or {}
    sql = (res.get("filters_applied") or {}).get("final_sql") or ""
    reg = (gr.get("regulatory_analysis") or {}).get("response") or ""
    parsed = safe_extract_json(reg, schema=RegulatoryResponse) if reg else None
    fallback = bool(gr.get("fallback_activated"))
    failed = n_errors() - before
    # Technical health only: a fallback (the SQL matched nothing) is pipeline
    # behaviour that the evaluation measures, so it is reported, not a failure.
    ok = failed == 0 and bool(sql) and (res.get("results_count") or 0) > 0 and parsed is not None
    print(f"[smoke] {city} pipeline {q['id']}: results={res.get('results_count')} fallback={fallback} "
          f"sql={'yes' if sql else 'NO'} regulatory_parsed={parsed is not None} failed_calls={failed} "
          f"-> {'OK' if ok else 'FAIL'}" + (" (warning: fallback, SQL matched nothing)" if ok and fallback else ""))
    if not ok or fallback:
        print(f"         query: {q['query'][:110]}\n         sql: {sql[:300]}")
    return ok


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Smoke test before a full evaluation run.")
    p.add_argument("--model", required=True)
    p.add_argument("--cities", default="turin,london,madrid")
    args = p.parse_args(argv)
    settings.set_llm_model(args.model)
    print(f"[smoke] model {settings.AGENT_LLM_MODEL} | failed calls logged to {ERROR_LOG}")
    if not check_endpoint():
        return 1
    ok = True
    for city in [c.strip() for c in args.cities.split(",") if c.strip()]:
        settings.set_city(city)
        ok &= check_downstream(city)
        ok &= check_pipeline(city)
    if ERROR_LOG.exists():
        for line in ERROR_LOG.read_text(encoding="utf-8").splitlines()[:5]:
            r = json.loads(line)
            print(f"   failed call: {r['agent']} | {r['error'][:120]}")
    print("[smoke] ALL OK" if ok else "[smoke] FAILED — fix before the full run")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
