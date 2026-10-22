"""
Run the whole RAG study for one agent model, end to end, for every city.

  0. pre-flight: query-file validator, then the smoke test (stops here on failure)
  1. downstream (regulatory agent alone, arms none + rag) per city
       -> eval_results/downstream/downstream_<city>_<model>.json (+ _summary.md)
     re-run once if any agent / LLM call failed
  2. full pipeline per city, arm none then arm rag (one process at a time)
       -> tests/results/curated_<arm>[_<city>]/outputs/.../<model>/
     after each arm: tests/audit_runs.py deletes result files hit by a failed
     call and the suite is re-run to refill them (it skips cached files)
  3. tests/compare_arms.py metrics per city (compliance with / without fallback,
     stability, regulatory output, strata)
  4. tests/compare_arms.py judge per city (blind Claude pairwise judge, Batch API)

Every step is resumable: finished downstream files and cached suite results are
kept, so after an interruption just run the same command again. The Mac is kept
awake while it runs (caffeinate). Progress goes to the console and to
<results>/full_study_<model>.log.

    python tests/run_full_study.py --model gemma-4
    python tests/run_full_study.py --model gemma-4 --cities london --skip-judge
Options for a rehearsal: --ids-per-city N (first N pipeline queries / downstream
items per city), with EXPERIMENT_RESULTS_DIR and --downstream-dir pointing at a
scratch folder.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PY = sys.executable
RESULTS = Path(os.environ.get("EXPERIMENT_RESULTS_DIR", str(BACKEND / "tests" / "results")))
EVAL = BACKEND / "app" / "services" / "llm" / "rag" / "eval"
LANGS = {"turin": "it,en", "london": "en", "madrid": "es,en"}
LOG = None
MAX_WAIT_H = 8.0
STATUS = {}               # step -> "ok" / "FAILED: ..." for the final table


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def run(cmd, env_extra=None, check=True) -> int:
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "PYTHONUNBUFFERED": "1", **(env_extra or {})}
    log("$ " + " ".join(str(c) for c in cmd))
    t0 = time.time()
    rc = subprocess.run([str(c) for c in cmd], cwd=BACKEND, env=env).returncode
    log(f"  exit {rc} after {(time.time() - t0) / 60:.1f} min")
    if check and rc not in (0,):
        raise SystemExit(f"step failed (exit {rc}): {' '.join(str(c) for c in cmd)}")
    return rc


def _reachable(url: str, headers=None) -> bool:
    import requests
    try:
        requests.get(url, headers=headers or {}, timeout=15)
        return True
    except requests.RequestException:
        return False


def model_reachable(model: str) -> bool:
    sys.path.insert(0, str(BACKEND))
    from app.core.config import settings
    settings.set_llm_model(model)
    base, key = settings.endpoint_for(settings.AGENT_LLM_MODEL)
    return _reachable((base or "https://api.openai.com/v1").rstrip("/") + "/models",
                      {"Authorization": f"Bearer {key}"})


def wait_for(name: str, check) -> bool:
    """Block until check() is true (every minute, up to MAX_WAIT_H): a network
    or server outage pauses the study instead of producing failed runs."""
    t0 = time.time()
    while not check():
        waited = (time.time() - t0) / 60
        if waited >= MAX_WAIT_H * 60:
            log(f"{name}: still unreachable after {MAX_WAIT_H} h")
            return False
        if int(waited) % 10 == 0:
            log(f"{name}: unreachable, waiting (checking every minute; {waited:.0f} min so far)")
        time.sleep(60)
    return True


def lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def ids_for(path: Path, n, key="queries"):
    if not n:
        return None
    return [q["id"] for q in json.loads(path.read_text(encoding="utf-8"))[key]][:n]


def downstream(city, model, out_dir: Path, n, force, repeats: int = 1) -> None:
    out = out_dir / f"downstream_{city}_{model}.json"
    if out.exists() and not force:
        log(f"downstream {city}: {out.name} exists, skipped")
        STATUS[f"downstream {city}"] = "ok (existing file)"
        return
    qfile = EVAL / f"downstream_queries_{city}.json"
    for attempt in (1, 2, 3):
        if not wait_for(f"model {model}", lambda: model_reachable(model)):
            break
        errlog = out_dir / f"downstream_{city}_{model}_errors.jsonl"
        errlog.unlink(missing_ok=True)
        cmd = [PY, "-m", "app.services.llm.rag.eval.downstream", "--model", model, "--queries", qfile,
               "--langs", LANGS[city], "--json", out, "--repeats", str(repeats)]
        ids = ids_for(qfile, n)
        if ids:
            cmd += ["--ids", ",".join(ids)]
        run(cmd, {"AGENT_ERROR_LOG": str(errlog)})
        failed = len(errlog.read_text(encoding="utf-8").splitlines()) if errlog.exists() else 0
        if not failed:
            log(f"downstream {city}: done, no failed calls")
            STATUS[f"downstream {city}"] = "ok"
            return
        log(f"downstream {city}: {failed} failed calls (attempt {attempt}); re-running")
    log(f"WARNING downstream {city}: failed calls remain, see {errlog}")
    STATUS[f"downstream {city}"] = "FAILED: failed calls remain"


def pipeline(city, model, arm, n, max_concurrent) -> None:
    cmd = [PY, "tests/test_suite.py", "--model", model, "--queries", "curated", "--arm", arm, "--city", city]
    if max_concurrent:
        cmd += ["--max-concurrent", max_concurrent]
    ids = ids_for(BACKEND / "tests" / f"benchmark_queries_{city}.json", n)
    if ids:
        cmd += ["--ids", ",".join(ids)]
    errlog = RESULTS / (f"curated_{arm}" + ("" if city == "turin" else f"_{city}")) / "agent_errors.jsonl"
    for attempt in range(1, 7):
        if not wait_for(f"model {model}", lambda: model_reachable(model)):
            break
        before = lines(errlog)
        run(cmd, {"DEBUG_STDOUT": "1"})
        new_failures = lines(errlog) - before
        rc = run([PY, "tests/audit_runs.py", "--arm", arm, "--city", city, "--model", model, "--delete"],
                 check=False)
        # complete = this run recorded no failed call (the suite skips cached
        # results, so a re-run only fills what is missing or was deleted)
        if rc == 0 and new_failures == 0:
            log(f"pipeline {city} {arm}: complete, no failed call in the last run")
            STATUS[f"pipeline {city} {arm}"] = "ok"
            return
        log(f"pipeline {city} {arm}: {new_failures} failed calls in this run; re-running to refill (attempt {attempt + 1})")
    log(f"WARNING pipeline {city} {arm}: still failing after 6 runs; see {errlog}")
    STATUS[f"pipeline {city} {arm}"] = "FAILED: failed calls persist"


def main(argv=None) -> int:
    global LOG, MAX_WAIT_H
    p = argparse.ArgumentParser(description="Run the whole RAG study for one model.")
    p.add_argument("--model", required=True)
    p.add_argument("--cities", default="turin,london,madrid")
    p.add_argument("--skip-downstream", action="store_true")
    p.add_argument("--skip-pipeline", action="store_true")
    p.add_argument("--skip-judge", action="store_true")
    p.add_argument("--skip-smoke", action="store_true")
    p.add_argument("--force-downstream", action="store_true", help="re-run downstream even if its file exists")
    p.add_argument("--downstream-repeats", type=int, default=1,
                   help="repeat each downstream run N times (use 3 with LLM_TEMPERATURE > 0, like the suite's 3 trials)")
    p.add_argument("--max-concurrent", default=None, help="suite concurrency (default: the model's limit)")
    p.add_argument("--ids-per-city", type=int, default=None, help="rehearsal: first N items per city only")
    p.add_argument("--downstream-dir", default=str(BACKEND / "eval_results" / "downstream"))
    p.add_argument("--max-wait-hours", type=float, default=8.0,
                   help="how long to wait for an unreachable server / network before giving up")
    args = p.parse_args(argv)
    MAX_WAIT_H = args.max_wait_hours
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    RESULTS.mkdir(parents=True, exist_ok=True)
    LOG = RESULTS / f"full_study_{args.model}.log"
    out_dir = Path(args.downstream_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":                   # keep the Mac awake while this process runs
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    t0 = time.time()
    log(f"=== full study | model {args.model} | cities {cities} | results {RESULTS} ===")

    run([PY, "tests/validate_query_files.py"])
    if not wait_for(f"model {args.model}", lambda: model_reachable(args.model)):
        raise SystemExit(f"model {args.model} unreachable for {MAX_WAIT_H} h: nothing was run")
    if not args.skip_smoke:
        run([PY, "tests/smoke_run.py", "--model", args.model, "--cities", ",".join(cities)])
    if not args.skip_downstream:
        for city in cities:
            downstream(city, args.model, out_dir, args.ids_per_city, args.force_downstream, args.downstream_repeats)
    if not args.skip_pipeline:
        for city in cities:
            for arm in ("none", "rag"):
                pipeline(city, args.model, arm, args.ids_per_city, args.max_concurrent)
    for city in cities:
        rc = run([PY, "tests/compare_arms.py", "metrics", "--model", args.model, "--city", city], check=False)
        STATUS[f"metrics {city}"] = "ok" if rc == 0 else f"FAILED (exit {rc})"
    if not args.skip_judge:
        for city in cities:
            ids = ids_for(BACKEND / "tests" / f"benchmark_queries_{city}.json", args.ids_per_city)
            cmd = [PY, "tests/compare_arms.py", "judge", "--model", args.model, "--city", city]
            rc = 1
            for attempt in (1, 2, 3):          # resumable: the batch id is kept in judge_state.json
                if not wait_for("api.anthropic.com", lambda: _reachable("https://api.anthropic.com")):
                    break
                rc = run(cmd + (["--ids", ",".join(ids)] if ids else []), check=False)
                if rc == 0:
                    break
            STATUS[f"judge {city}"] = "ok" if rc == 0 else f"FAILED (exit {rc})"

    log(f"=== done in {(time.time() - t0) / 3600:.1f} h ===")
    for step, st in STATUS.items():
        log(f"  {step:28s} {st}")
    (RESULTS / f"full_study_{args.model}_status.json").write_text(json.dumps(STATUS, indent=2))
    if any(not s.startswith("ok") for s in STATUS.values()):
        log("SOME STEPS FAILED: re-run the same command (finished steps are kept)")
    for city in cities:
        cmp_dir = RESULTS / ("curated_compare" + ("" if city == "turin" else f"_{city}")) / args.model
        log(f"{city}: downstream {out_dir / f'downstream_{city}_{args.model}_summary.md'} | "
            f"metrics {cmp_dir / 'metrics.md'} | judge {cmp_dir / 'judge_summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
