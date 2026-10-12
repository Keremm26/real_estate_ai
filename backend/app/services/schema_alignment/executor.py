"""Deterministic transform executor — materializes normalized cross-city columns.

Reads the frozen agent-2 output (reviewed_mappings.json) + the vocabulary + the
raw CSVs, and APPLIES each cell's TransformRule with no LLM. Tiered handling:
  - passthrough (none/rename/preserve): numeric concept -> parse to number;
    categorical/text -> keep raw label (preserve = "do not normalize").
  - mechanical (decimal_normalize/scale_fix/unit_convert): parse + factor.
  - derive: only the recognized `total = intensity * floor_area` pattern, with a
    guard that floor_area is present; anything else is reported as UNEXECUTABLE.
  - recategorize: applied only if params.mapping is provided, else UNEXECUTABLE.
Outputs per-city normalized tables + an execution report (guard outcomes and a
value-range sanity check against the vocabulary's expected_range/min_year).

Run:
    uv run python -m app.services.schema_alignment.executor run          # default 5-concept subset
    uv run python -m app.services.schema_alignment.executor run --concepts floor_area,energy_class
    uv run python -m app.services.schema_alignment.executor run --all
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from unidecode import unidecode

try:
    from .profiler import _numeric_cores, _to_num, detect_delimiter, detect_encoding
except ImportError:  # pragma: no cover
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from profiler import _numeric_cores, _to_num, detect_delimiter, detect_encoding  # type: ignore

VOCAB_PATH = "data/epc_schema/concept_vocabulary.v1.1.json"
REVIEW_PATH = "data/epc_schema/reviewed_mappings.json"
PROFILES_DIR = "data/epc_profiles"
RAW_DIR = "data/epc_raw"
OUT_DIR = "data/epc_schema/normalized"

DEFAULT_SUBSET = [
    "postal_code", "construction_year", "floor_area",
    "energy_class", "space_heating_final_energy_total",
]
_PASSTHROUGH = {"none", "rename", "preserve"}
_UNIT_FACTORS = {("mm", "m"): 0.001, ("cm", "m"): 0.01, ("mj", "kwh"): 1 / 3.6}
_CURRENT_YEAR = datetime.now().year


# --------------------------------------------------------------------------- #
def to_numeric(series: pd.Series, style: str | None) -> pd.Series:
    s = series.astype(str).str.strip()
    core, ok = _numeric_cores(s)
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    if ok.any():
        out.loc[ok] = _to_num(core[ok], style or "dot").values
    return out


def _clean_str(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return s.where(~s.str.lower().isin({"", "na", "n/a", "nan", "null", "none", "-"}), other=pd.NA)


def _resolve_operand(token, df: pd.DataFrame, style: dict, out: pd.DataFrame):
    """An operand is 'concept:<id>' (an already-materialized concept) or a source column."""
    if isinstance(token, str) and token.startswith("concept:"):
        cid = token.split(":", 1)[1]
        return out[cid] if cid in out.columns else None
    if token in df.columns:
        return to_numeric(df[token], style.get(token))
    return None


def _derive(operation: str, operands: list, df: pd.DataFrame, style: dict, out: pd.DataFrame):
    parts = [_resolve_operand(t, df, style, out) for t in (operands or [])]
    if not parts or any(p is None for p in parts):
        return None, "operand unresolved (missing column/concept)"
    op = str(operation).lower()
    s = parts[0].copy()
    if op in ("multiply", "mult", "product"):
        for p in parts[1:]:
            s = s * p
    elif op in ("sum", "add"):
        for p in parts[1:]:
            s = s + p
    elif op in ("subtract", "minus", "diff"):
        s = parts[0] - parts[1]
    elif op in ("divide", "ratio"):
        s = parts[0] / parts[1].replace(0, np.nan)
    else:
        return None, f"unknown derive operation '{operation}'"
    return s, None


def _range_ok(vals: pd.Series, meta: dict) -> float | None:
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if v.empty:
        return None
    er = meta.get("expected_range")
    if er and er[0] is not None:
        lo = er[0]
        hi = er[1] if len(er) > 1 and er[1] is not None else np.inf
        return round(float(((v >= lo) & (v <= hi)).mean()), 3)
    if meta.get("expected_min_year"):
        return round(float(((v >= meta["expected_min_year"]) & (v <= _CURRENT_YEAR + 1)).mean()), 3)
    return None


# --------------------------------------------------------------------------- #
def _load():
    vocab = json.loads(Path(VOCAB_PATH).read_text())
    review = json.loads(Path(REVIEW_PATH).read_text())
    vmeta = {c["id"]: c for c in vocab["concepts"]}
    rev = {r["concept_id"]: r for r in review["reviews"]}
    profiles = {}
    for p in Path(PROFILES_DIR).glob("*.json"):
        d = json.loads(p.read_text())
        d["_style"] = {c["original_name"]: c.get("decimal_style") for c in d["columns"]}
        profiles[d["city"]] = d
    return vmeta, rev, profiles


def _needed_columns(rev: dict, concepts: list[str], city: str) -> set[str]:
    cols: set[str] = set()
    for cid in concepts:
        for cell in rev.get(cid, {}).get("cells", []):
            if cell["city"] == city:
                cols.update(cell["source_columns"])
    return cols


def _cell_for(rev: dict, cid: str, city: str) -> dict | None:
    for cell in rev.get(cid, {}).get("cells", []):
        if cell["city"] == city:
            return cell
    return None


def materialize_city(city: str, profile: dict, rev: dict, vmeta: dict,
                     concepts: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    raw_path = Path(RAW_DIR) / profile["source_file"]
    enc = profile.get("encoding") or detect_encoding(raw_path)
    delim = profile.get("delimiter") or detect_delimiter(raw_path, enc)
    needed = _needed_columns(rev, concepts, city)
    df = pd.read_csv(raw_path, sep=delim, dtype=str, na_filter=False, keep_default_na=False,
                     encoding=enc, engine="c", on_bad_lines="skip",
                     usecols=lambda c: c in needed)
    style = profile["_style"]
    out = pd.DataFrame(index=df.index)
    report: list[dict] = []

    # order: non-derive first (so floor_area exists), then derive
    order = sorted([c for c in concepts if c in rev],
                   key=lambda c: (_cell_for(rev, c, city) or {}).get("transform", {}).get("op") == "derive")

    for cid in order:
        cell = _cell_for(rev, cid, city)
        if cell is None:
            continue
        op = cell["transform"]["op"]
        params = cell["transform"].get("params", {})
        srcs = cell["source_columns"]
        vk = vmeta.get(cid, {}).get("value_kind", "text")
        status, note, series = "executed", None, None

        try:
            missing = [s for s in srcs if s not in df.columns]
            if op == "derive":
                operation, operands = params.get("operation"), params.get("operands")
                if operation and operands:
                    series, err = _derive(operation, operands, df, style, out)
                    status = "executed_derive" if series is not None else "unexecutable"
                    note = err
                else:  # backward-compat: old free-text intensity*floor_area formula
                    fa = out.get("floor_area")
                    formula = (params.get("formula") or "").lower()
                    if "floor_area" in formula and "*" in formula and fa is not None and srcs[0] in df.columns:
                        series = to_numeric(df[srcs[0]], style.get(srcs[0])) * fa
                        status = "executed_derive"
                    else:
                        status, note = "unexecutable", "derive not machine-specified (need operation+operands)"
            elif missing:
                status, note = "source_missing", f"columns absent: {missing}"
            elif op in _PASSTHROUGH:
                series = to_numeric(df[srcs[0]], style.get(srcs[0])) if vk == "numeric" else _clean_str(df[srcs[0]])
                status = "preserved" if op == "preserve" else "passthrough"
            elif op == "decimal_normalize":
                series = to_numeric(df[srcs[0]], style.get(srcs[0]))
            elif op == "scale_fix":
                series = to_numeric(df[srcs[0]], style.get(srcs[0])) * float(params.get("factor", 1))
            elif op == "unit_convert":
                f = params.get("factor") or _UNIT_FACTORS.get(
                    (str(params.get("from", "")).lower(), str(params.get("to", "")).lower()))
                if f is None:
                    status, note = "unexecutable", f"no factor for {params.get('from')}->{params.get('to')}"
                else:
                    series = to_numeric(df[srcs[0]], style.get(srcs[0])) * float(f)
            elif op == "recategorize":
                m = params.get("mapping")
                if m:
                    series = _clean_str(df[srcs[0]]).map(lambda x: m.get(x, x))
                else:
                    status, note = "unexecutable", "recategorize without mapping"
                    series = _clean_str(df[srcs[0]])
            else:
                status, note = "unexecutable", f"unhandled op {op}"
        except Exception as e:  # noqa: BLE001
            status, note = "error", str(e)[:120]

        if series is not None:
            out[cid] = series
        nn = int(series.notna().sum()) if series is not None else 0
        report.append({
            "city": city, "concept": cid, "op": op, "verdict": cell["verdict"],
            "comparability": rev[cid]["comparability_verdict"], "status": status,
            "n_rows": len(df), "n_non_null": nn,
            "fill_rate": round(nn / len(df), 3) if len(df) else 0.0,
            "range_ok": _range_ok(series, vmeta.get(cid, {})) if series is not None and vk == "numeric" else None,
            "note": note,
        })
    return out, report


# --------------------------------------------------------------------------- #
def run(concepts: list[str]) -> dict:
    vmeta, rev, profiles = _load()
    concepts = [c for c in concepts if c in rev]
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    all_report: list[dict] = []
    comparable_ids = [c for c in concepts if rev[c]["comparability_verdict"] == "comparable"]
    long_rows: list[pd.DataFrame] = []

    for city, profile in profiles.items():
        print(f"[exec] {city} ...")
        table, rep = materialize_city(city, profile, rev, vmeta, concepts)
        slug = unidecode(city).lower().replace(" ", "_")
        table.to_csv(Path(OUT_DIR) / f"{slug}.csv", index=False)
        all_report.extend(rep)
        for cid in comparable_ids:
            if cid in table.columns:
                long_rows.append(pd.DataFrame({"city": city, "concept": cid, "value": table[cid]}))

    if long_rows:
        pd.concat(long_rows, ignore_index=True).dropna(subset=["value"]).to_csv(
            Path(OUT_DIR) / "comparable_core.csv", index=False)

    from collections import Counter
    summary = {
        "cities": len(profiles), "concepts": len(concepts),
        "by_status": dict(Counter(r["status"] for r in all_report)),
        "by_op": dict(Counter(r["op"] for r in all_report)),
        "cells": len(all_report),
    }
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "concepts": concepts, "summary": summary, "cells": all_report}
    Path("data/epc_schema/execution_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    _write_report_md(report)
    return summary


def _write_report_md(report: dict) -> None:
    s = report["summary"]
    lines = [f"# Transform execution report — {report['generated_at']}",
             f"_{s['cities']} cities · {s['concepts']} concepts · {s['cells']} cells_",
             f"**Status:** {s['by_status']}  **Ops:** {s['by_op']}", "",
             "| concept | city | op | status | fill | range_ok | note |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(report["cells"], key=lambda x: (x["concept"], x["city"])):
        lines.append(f"| `{r['concept']}` | {r['city']} | {r['op']} | {r['status']} | "
                     f"{r['fill_rate']} | {r['range_ok'] if r['range_ok'] is not None else ''} | {r['note'] or ''} |")
    Path("data/epc_schema/execution_report.review.md").write_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic transform executor.")
    ap.add_argument("run", nargs="?", default="run")
    ap.add_argument("--concepts", default=None, help="comma-separated concept ids")
    ap.add_argument("--all", action="store_true", help="materialize every reviewed concept")
    args = ap.parse_args()

    if args.all:
        rev = json.loads(Path(REVIEW_PATH).read_text())
        concepts = [r["concept_id"] for r in rev["reviews"]]
    elif args.concepts:
        concepts = [c.strip() for c in args.concepts.split(",")]
    else:
        concepts = DEFAULT_SUBSET
    summary = run(concepts)
    print("[exec]", json.dumps(summary, indent=2))
    print("[exec] wrote data/epc_schema/normalized/*.csv + execution_report.{json,review.md}")


if __name__ == "__main__":
    main()
