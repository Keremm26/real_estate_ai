"""Condense city `DatasetProfile` JSONs into a compact cross-city column digest.

This is the *input* the concept-discovery agent sees. We deliberately drop
id_like / free-text columns (addresses, refs, holder names -- never mappable
concepts, and already redacted) to cut noise and tokens, and compress every
remaining column to a single evidence-bearing line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Kinds that can carry a mappable EPC concept. id_like/text are excluded.
_CONCEPT_KINDS = {
    "numeric_continuous", "numeric_discrete", "categorical",
    "boolean", "datetime", "constant",
}


def _column_line(col: dict[str, Any]) -> str:
    name = col["original_name"]
    kind = col["inferred_kind"]
    miss = f"miss={col['missing_ratio'] * 100:.0f}%"
    uniq = f"uniq={col['n_unique']}"
    bits = [f"{name} | {kind} | {miss} {uniq}"]

    units = col["detected"].get("unit_from_name") or []
    uvals = col["detected"].get("unit_from_values") or []
    pats = col["detected"].get("patterns") or []
    if units or uvals:
        bits.append("unit:" + ",".join(dict.fromkeys(units + uvals)))
    if pats:
        bits.append("pat:" + ",".join(pats))

    stats = col.get("numeric_stats")
    if stats:
        bits.append(f"~[{stats['min']:g}..{stats['max']:g}] p50={stats['p50']:g}")
    elif col.get("top_values"):
        top = col["top_values"][:4]
        bits.append("vals: " + ", ".join(f"{t['value']}({t['pct'] * 100:.0f}%)" for t in top))
    elif col.get("sample_values"):
        bits.append("e.g. " + ", ".join(col["sample_values"][:3]))
    return " | ".join(bits)


def build_digest(profiles_dir: str | Path) -> dict[str, Any]:
    profiles_dir = Path(profiles_dir)
    cities: list[dict[str, Any]] = []
    for path in sorted(profiles_dir.glob("*.json")):
        p = json.loads(path.read_text())
        kept = [c for c in p["columns"] if c["inferred_kind"] in _CONCEPT_KINDS]
        cities.append({
            "city": p["city"],
            "country": p["country"],
            "language": p["language"],
            "n_rows": p["n_rows"],
            "n_cols_total": p["n_cols"],
            "columns": kept,
        })
    return {"cities": cities}


def render_digest_text(digest: dict[str, Any]) -> str:
    """Render the digest as the compact text block sent to the agent."""
    out: list[str] = []
    for c in digest["cities"]:
        out.append(
            f"\n### {c['city']} ({c['country']}, lang={c['language']}, "
            f"rows={c['n_rows']}, mappable_cols={len(c['columns'])}/{c['n_cols_total']})"
        )
        for col in c["columns"]:
            out.append("- " + _column_line(col))
    return "\n".join(out).strip()


def digest_stats(digest: dict[str, Any]) -> dict[str, Any]:
    text = render_digest_text(digest)
    return {
        "cities": len(digest["cities"]),
        "mappable_columns": sum(len(c["columns"]) for c in digest["cities"]),
        "total_columns": sum(c["n_cols_total"] for c in digest["cities"]),
        "chars": len(text),
        "approx_tokens": len(text) // 4,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build cross-city column digest from profiles.")
    ap.add_argument("--profiles", default="data/epc_profiles")
    ap.add_argument("--show", action="store_true", help="Print the full rendered digest")
    args = ap.parse_args()

    d = build_digest(args.profiles)
    print("STATS:", json.dumps(digest_stats(d), indent=2))
    if args.show:
        print(render_digest_text(d))
