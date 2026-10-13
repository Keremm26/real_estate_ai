"""
Audit a test_suite run for failed agent / LLM calls.

The agents return a fallback when a call fails, so in the result files a failed
call looks like "found nothing". test_suite.py records every failed call in
results/curated_<arm>[_<city>]/agent_errors.jsonl (AGENT_ERROR_LOG: written by
app.utils.decorators.handle_agent_error and by the LLM client's failed-call
recorder, with the query or the prompt that contains it). This lists the result
files whose run overlapped a failure for the same query; --delete removes them
so that re-running the suite (it skips cached files) refills exactly those.

    python tests/audit_runs.py --arm rag --city london --model gemma-4 [--delete]

Exit code 0 when no result file is affected, 2 when some are (after --delete,
they are gone and the suite must be re-run).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

suite_path = Path(__file__).resolve().parent
RESULTS = Path(os.environ.get("EXPERIMENT_RESULTS_DIR", str(suite_path / "results")))
START_MARGIN_S = 120      # queue / setup time before the measured execution
END_MARGIN_S = 5


def suite_root(arm: str, city: str) -> Path:
    return RESULTS / (f"curated_{arm}" + ("" if city == "turin" else f"_{city}"))


def load_errors(path: Path):
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                r["_t"] = datetime.fromisoformat(r["time"])
                out.append(r)
            except (ValueError, KeyError):
                continue
    return out


def affected_files(root: Path, model: str, errors):
    """(file, reason) for every result file whose run overlapped a failure."""
    hits, matched = [], set()
    files = sorted(root.glob(f"outputs/*/{model}/**/query_*.json"))
    for f in files:
        try:
            pack = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        q = pack.get("query") or ""
        end = datetime.fromtimestamp(f.stat().st_mtime)
        start = end - timedelta(seconds=(pack.get("execution_time_ms") or 0) / 1000 + START_MARGIN_S)
        end += timedelta(seconds=END_MARGIN_S)
        for i, e in enumerate(errors):
            if not (start <= e["_t"] <= end):
                continue
            same_query = q and (e.get("query") == q or q in (e.get("prompt") or ""))
            anonymous = not e.get("query") and not e.get("prompt")
            if same_query or anonymous:
                hits.append((f, f"{e['agent']}: {e['error'][:80]}" + ("" if same_query else " (time window only)")))
                matched.add(i)
                break
    return files, hits, [e for i, e in enumerate(errors) if i not in matched]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Find (and remove) result files affected by failed agent calls.")
    p.add_argument("--arm", required=True)
    p.add_argument("--city", default="turin")
    p.add_argument("--model", required=True)
    p.add_argument("--delete", action="store_true", help="delete the affected result files")
    args = p.parse_args(argv)

    root = suite_root(args.arm, args.city)
    errors = load_errors(root / "agent_errors.jsonl")
    files, hits, unmatched = affected_files(root, args.model, errors)
    fallback = 0
    for f in files:
        try:
            fallback += bool(json.loads(f.read_text(encoding="utf-8")).get("fallback"))
        except (OSError, ValueError):
            pass
    print(f"[audit] {root.name} / {args.model}: {len(files)} result files, {len(errors)} recorded failures, "
          f"{len(hits)} affected files, {fallback} fallback runs (SQL matched nothing: pipeline behaviour, kept)")
    for f, why in hits:
        print(f"   {f.relative_to(root)}  <- {why}")
    if unmatched:
        print(f"   {len(unmatched)} failures matched no result file (older runs, or the run did not save a result)")
    if args.delete:
        for f, _ in hits:
            f.unlink(missing_ok=True)
        if hits:
            print(f"[audit] deleted {len(hits)} files: re-run the suite to refill them")
    return 2 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
