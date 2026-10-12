"""Deterministic profiler for cross-city EPC/APE CSV datasets.

Reads raw CSVs (auto-detecting delimiter / encoding / decimal convention),
classifies each column, and emits a privacy-safe `DatasetProfile` JSON.

Run:
    uv run python -m app.services.schema_alignment.profiler \
        --input backend/data/epc_raw --output backend/data/epc_profiles
    # or a single file, with a preview:
    uv run python backend/app/services/schema_alignment/profiler.py \
        --input backend/data/epc_raw/Liege_residential.csv --preview
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from unidecode import unidecode

try:  # runnable both as a module and as a bare script
    from .models import (
        PROFILING_VERSION,
        ColumnProfile,
        DatasetProfile,
        DetectedClues,
    )
except ImportError:  # pragma: no cover - script fallback
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from models import (  # type: ignore
        PROFILING_VERSION,
        ColumnProfile,
        DatasetProfile,
        DetectedClues,
    )

# --------------------------------------------------------------------------- #
# Static knowledge: filename -> (city, country, language).
# Language/country are external metadata, not inferred, to keep profiling
# deterministic. EPC-methodology descriptors come later (vocabulary phase).
# --------------------------------------------------------------------------- #
CITY_META: list[tuple[str, tuple[str, str, str]]] = [
    ("turin", ("Turin", "Italy", "it")),
    ("copenhagen", ("Copenhagen", "Denmark", "da")),
    ("amsterdam", ("Amsterdam", "Netherlands", "nl")),
    ("barcelona", ("Barcelona", "Spain", "ca")),
    ("france", ("Paris", "France", "fr")),
    ("paris", ("Paris", "France", "fr")),
    ("ireland", ("Dublin", "Ireland", "en")),
    ("dublin", ("Dublin", "Ireland", "en")),
    ("liege", ("Liège", "Belgium", "fr")),
    ("lisbon", ("Lisbon", "Portugal", "pt")),
    ("madrid", ("Madrid", "Spain", "es")),
    ("uk", ("London", "United Kingdom", "en")),
    ("london", ("London", "United Kingdom", "en")),
]

_MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", ".", "?", "s/n", "sn"}

_BOOL_TOKENS = {
    "0", "1", "true", "false", "yes", "no", "y", "n", "t", "f",
    "ja", "nee", "oui", "non", "si", "sí", "sim", "não", "nao",
    "waar", "onwaar", "verdadero", "falso", "vrai", "faux",
}

_ID_NAME_RE = re.compile(
    r"\b(id|ident|code|cod|codi|ref|refer|uprn|mprn|numero|num_cas|cadastr|"
    r"catastr|refcat|insee|rnb|rpls|certificate|certificado|matricul)\b",
    re.IGNORECASE,
)

_YEAR_NAME_RE = re.compile(
    r"(year|a[nñ]o|ano|jaar|annee|ann[eé]e|bouwjaar|construccio|construcao|"
    r"construction|periodo|periode)",
    re.IGNORECASE,
)
_DATE_NAME_RE = re.compile(r"(date|fecha|datum|dato|visite|lodgement|inspection)", re.IGNORECASE)
_POSTAL_NAME_RE = re.compile(r"(post.?code|codi_postal|codpost|cod_?post|postcode|cp\b|zip)", re.IGNORECASE)
_COORD_NAME_RE = re.compile(r"(lat|lon|lng|utm|coord|geo|_x_|_y_|\bx\b|\by\b)", re.IGNORECASE)

_UNIT_NAME_PATTERNS = {
    "m2": re.compile(r"(m2|m²|m\^2|metres|metros|superf|oppervlakt|floor_?area|surface|area)", re.IGNORECASE),
    "kwh": re.compile(r"(kwh|energ|consum|energie|energia|demand|behoefte)", re.IGNORECASE),
    "co2": re.compile(r"(co2|emission|emiss|emissie)", re.IGNORECASE),
    "pct": re.compile(r"(percent|aandeel|%|ratio|fraction)", re.IGNORECASE),
    "eur": re.compile(r"(cost|cout|coste|kost|price|precio|prijs|€|eur)", re.IGNORECASE),
    "year": _YEAR_NAME_RE,
}

_ENERGY_CLASS_RE = re.compile(r"^[A-G]([+]{1,3}|-)?$")
_DATE_VALUE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b")
# A real inline unit follows a number and *starts with a letter or unit symbol*
# (not a '.'/','/'-' — those are decimal/date fragments, not units).
_INLINE_UNIT_RE = re.compile(r"\d\s*([A-Za-z%€$£²³][A-Za-z0-9%€$£²³/·.^\- ]{0,10})\s*$")

# A value is numeric only if, after stripping one currency prefix and one unit
# suffix, its core is *entirely* digits + separators + sign. This rejects
# category codes like "BETWEEN_1971_AND_1984" that merely contain digits.
_NUM_CORE_RE = re.compile(r"^[-+]?\d[\d.,\s]*$")
_UNIT_STRIP_RE = re.compile(r"\s*(m²|m2|m\^2|kwh[\w/·]*|kg[\w/·]*|%|€|\$|£|/[\w²]+)\s*$", re.IGNORECASE)
_CUR_STRIP_RE = re.compile(r"^\s*[€$£]\s*")

TOP_K = 20
SAMPLE_K = 6
DECISION_SAMPLE = 5000


# --------------------------------------------------------------------------- #
# CSV dialect / IO
# --------------------------------------------------------------------------- #
def detect_encoding(path: Path) -> str:
    raw = path.open("rb").read(200_000)
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def detect_delimiter(path: Path, encoding: str) -> str:
    with path.open("r", encoding=encoding, errors="replace") as fh:
        header = fh.readline()
    candidates = {",": header.count(","), ";": header.count(";"),
                  "\t": header.count("\t"), "|": header.count("|")}
    return max(candidates, key=candidates.get)


def read_dataframe(path: Path, delimiter: str, encoding: str, max_rows: int | None):
    return pd.read_csv(
        path,
        sep=delimiter,
        dtype=str,
        na_filter=False,          # keep our own missing definition
        keep_default_na=False,
        encoding=encoding,
        engine="c",
        on_bad_lines="skip",
        nrows=max_rows,
    )


def city_meta(filename: str) -> tuple[str, str, str]:
    low = filename.lower()
    for needle, meta in CITY_META:
        if needle in low:
            return meta
    stem = Path(filename).stem.split("_")[0]
    return (stem.capitalize(), "Unknown", "und")


# --------------------------------------------------------------------------- #
# Numeric parsing (European vs Anglo conventions)
# --------------------------------------------------------------------------- #
def _numeric_cores(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Strip currency/unit affixes; return (core, is_clean_number_mask)."""
    core = series.str.replace(_CUR_STRIP_RE, "", regex=True)
    core = core.str.replace(_UNIT_STRIP_RE, "", regex=True).str.strip()
    return core, core.str.match(_NUM_CORE_RE)


def _to_num(cores: pd.Series, style: str) -> pd.Series:
    if style == "comma":
        cores = cores.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        cores = cores.str.replace(",", "", regex=False)
    return pd.to_numeric(cores.str.replace(" ", "", regex=False), errors="coerce")


def parse_numeric(nonmiss: pd.Series) -> tuple[bool, str | None, pd.Series | None]:
    sample = nonmiss if len(nonmiss) <= DECISION_SAMPLE else nonmiss.sample(DECISION_SAMPLE, random_state=0)
    core, ok = _numeric_cores(sample)
    if ok.mean() < 0.9:  # <90% clean numbers -> not a numeric column
        return False, None, None

    ok_core = core[ok]
    has_comma = ok_core.str.contains(",", regex=False).mean()
    dot_ok = _to_num(ok_core, "dot").notna().mean()
    comma_ok = _to_num(ok_core, "comma").notna().mean()
    style = "comma" if (comma_ok >= dot_ok and has_comma > 0) else "dot"

    full_core, full_ok = _numeric_cores(nonmiss)
    return True, style, _to_num(full_core[full_ok], style).dropna()


def looks_datetime(name: str, nonmiss: pd.Series) -> bool:
    if _DATE_NAME_RE.search(name):
        return True
    return bool(nonmiss.head(DECISION_SAMPLE).str.match(_DATE_VALUE_RE).mean() >= 0.8)


def numeric_stats(nums: pd.Series) -> dict:
    q = nums.quantile([0.01, 0.25, 0.5, 0.75, 0.99])
    return {
        "min": float(nums.min()),
        "max": float(nums.max()),
        "mean": round(float(nums.mean()), 4),
        "std": round(float(nums.std()), 4) if len(nums) > 1 else 0.0,
        "p1": float(q.loc[0.01]),
        "p25": float(q.loc[0.25]),
        "p50": float(q.loc[0.5]),
        "p75": float(q.loc[0.75]),
        "p99": float(q.loc[0.99]),
        "n_zero": int((nums == 0).sum()),
        "n_negative": int((nums < 0).sum()),
    }


# --------------------------------------------------------------------------- #
# Clue extraction
# --------------------------------------------------------------------------- #
def units_from_name(name: str) -> list[str]:
    return [unit for unit, rx in _UNIT_NAME_PATTERNS.items() if rx.search(name)]


def units_from_values(samples: list[str]) -> list[str]:
    found: set[str] = set()
    for v in samples:
        m = _INLINE_UNIT_RE.search(v.strip())
        if m:
            tok = m.group(1).strip(" .-")
            if tok and not tok.isdigit():
                found.add(tok)
    return sorted(found)


def structural_mask(value: str) -> str:
    out = []
    for ch in value[:40]:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X")
        else:
            out.append(ch)
    return "".join(out)


def detect_patterns(name: str, kind: str, top_vals: list[str] | None,
                    stats: dict | None) -> list[str]:
    pats: list[str] = []
    if top_vals and sum(bool(_ENERGY_CLASS_RE.match(v.strip().upper())) for v in top_vals) >= max(2, len(top_vals) // 2):
        pats.append("energy_class_like")
    if _POSTAL_NAME_RE.search(name):
        pats.append("postal_code")
    if _COORD_NAME_RE.search(name):
        pats.append("coordinate")
    if _YEAR_NAME_RE.search(name) or (stats and 1700 <= stats.get("p25", 0) and stats.get("p99", 0) <= 2035):
        if stats and 1700 <= (stats.get("min") or 0) <= 2035:
            pats.append("year")
    return pats


# --------------------------------------------------------------------------- #
# Column profiling
# --------------------------------------------------------------------------- #
def profile_column(name: str, original: str, position: int, is_dup: bool,
                   series: pd.Series, language: str) -> ColumnProfile:
    stripped = series.astype(str).str.strip()
    missing_mask = stripped.str.lower().isin(_MISSING_TOKENS)
    nonmiss = stripped[~missing_mask]

    n_total = len(stripped)
    n_missing = int(missing_mask.sum())
    n_nonmiss = len(nonmiss)
    n_unique = int(nonmiss.nunique())
    unique_ratio = round(n_unique / n_nonmiss, 4) if n_nonmiss else 0.0

    flags: list[str] = []
    numeric_stats_d: dict | None = None
    decimal_style: str | None = None
    top_values = None
    sample_values = None
    masked_samples = None
    value_length = None

    # --- classify -------------------------------------------------------- #
    if n_nonmiss == 0:
        kind = "empty"
        flags.append("all_missing")
    elif n_unique == 1:
        kind = "constant"
        sample_values = [nonmiss.iloc[0]]
        flags.append("constant")
    else:
        is_num, decimal_style, nums = parse_numeric(nonmiss)
        looks_id = bool(_ID_NAME_RE.search(name)) or unique_ratio > 0.9

        if is_num and _POSTAL_NAME_RE.search(name):
            kind, decimal_style = "id_like", None
        elif is_num and looks_id and not units_from_name(name):
            kind, decimal_style = "id_like", None
        elif is_num and nums is not None:
            is_int = bool((nums == nums.round()).all())
            kind = "numeric_discrete" if (is_int and n_unique <= 40) else "numeric_continuous"
            numeric_stats_d = numeric_stats(nums)
        elif looks_datetime(name, nonmiss):
            kind = "datetime"
            sample_values = list(nonmiss.head(SAMPLE_K).astype(str))
        else:
            lowered = set(nonmiss.str.lower().unique()[:10])
            if n_unique <= 3 and lowered.issubset(_BOOL_TOKENS):
                kind = "boolean"
            elif looks_id:
                kind = "id_like"
            elif n_unique <= 50 or unique_ratio <= 0.2:
                kind = "categorical"
            else:
                kind = "text"

    # --- values / redaction --------------------------------------------- #
    safe_kinds = {"categorical", "boolean", "numeric_continuous", "numeric_discrete",
                  "datetime", "constant"}
    if kind in {"categorical", "boolean"}:
        vc = nonmiss.value_counts().head(TOP_K)
        top_values = [{"value": str(k), "count": int(v), "pct": round(v / n_nonmiss, 4)}
                      for k, v in vc.items()]
        sample_values = list(vc.index[:SAMPLE_K].astype(str))
    elif kind in safe_kinds and sample_values is None:
        sample_values = list(nonmiss.head(SAMPLE_K).astype(str))
    elif kind in {"id_like", "text"}:
        masked_samples = [structural_mask(v) for v in nonmiss.head(SAMPLE_K)]
        lengths = nonmiss.str.len()
        value_length = {"min": float(lengths.min()), "max": float(lengths.max()),
                        "mean": round(float(lengths.mean()), 2)}
        flags.append("probable_id" if kind == "id_like" else "free_text")

    if n_total and n_missing / n_total > 0.5:
        flags.append("high_missing")

    raw_samples = list(nonmiss.head(SAMPLE_K).astype(str))
    clues = DetectedClues(
        unit_from_name=units_from_name(name),
        unit_from_values=units_from_values(raw_samples),
        patterns=detect_patterns(name, kind,
                                 [t["value"] for t in top_values] if top_values else None,
                                 numeric_stats_d),
        language_hint=language,
    )

    return ColumnProfile(
        name=name, original_name=original, position=position, is_duplicate_name=is_dup,
        inferred_kind=kind, n_total=n_total, n_missing=n_missing,
        missing_ratio=round(n_missing / n_total, 4) if n_total else 0.0,
        n_unique=n_unique, unique_ratio=unique_ratio,
        numeric_stats=numeric_stats_d, decimal_style=decimal_style,
        top_values=top_values, sample_values=sample_values,
        masked_samples=masked_samples, value_length=value_length,
        detected=clues, flags=flags,
    )


# --------------------------------------------------------------------------- #
# Dataset profiling
# --------------------------------------------------------------------------- #
def profile_file(path: Path, max_rows: int | None = None) -> DatasetProfile:
    encoding = detect_encoding(path)
    delimiter = detect_delimiter(path, encoding)
    df = read_dataframe(path, delimiter, encoding, max_rows)

    with path.open("r", encoding=encoding, errors="replace") as fh:
        raw_header = [h.strip().strip('"') for h in fh.readline().rstrip("\n").split(delimiter)]
    dup_counts: dict[str, int] = {}
    for h in raw_header:
        dup_counts[h] = dup_counts.get(h, 0) + 1

    city, country, language = city_meta(path.name)
    notes: list[str] = []
    dups = sorted({h for h, c in dup_counts.items() if c > 1})
    if dups:
        notes.append(f"Duplicate header names: {dups}")

    columns: list[ColumnProfile] = []
    for pos, col in enumerate(df.columns):
        original = raw_header[pos] if pos < len(raw_header) else str(col)
        is_dup = dup_counts.get(original, 0) > 1
        columns.append(profile_column(str(col), original, pos, is_dup, df[col], language))

    return DatasetProfile(
        city=city, country=country, language=language,
        source_file=path.name, delimiter=delimiter, encoding=encoding,
        n_rows=len(df), n_cols=len(df.columns),
        sampled=max_rows is not None, sample_rows=max_rows,
        profiling_version=PROFILING_VERSION,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        notes=notes, columns=columns,
    )


def print_preview(p: DatasetProfile, limit: int = 200) -> None:
    print(f"\n=== {p.city} ({p.country}, {p.language}) — {p.source_file} ===")
    print(f"delimiter={p.delimiter!r} encoding={p.encoding} rows={p.n_rows} cols={p.n_cols}")
    for n in p.notes:
        print(f"  NOTE: {n}")
    print(f"{'col':<34} {'kind':<18} {'miss%':>6} {'uniq':>7}  detail")
    for c in p.columns[:limit]:
        detail = ""
        if c.numeric_stats:
            s = c.numeric_stats
            detail = f"[{s['min']:g}..{s['max']:g}] p50={s['p50']:g} dec={c.decimal_style}"
        elif c.top_values:
            detail = " | ".join(f"{t['value']}:{t['count']}" for t in c.top_values[:4])
        elif c.masked_samples:
            detail = "mask=" + (c.masked_samples[0] if c.masked_samples else "")
        units = ",".join(c.detected.unit_from_name)
        pats = ",".join(c.detected.patterns)
        tag = " ".join(x for x in [f"⟨{units}⟩" if units else "", f"«{pats}»" if pats else ""] if x)
        print(f"{c.name[:33]:<34} {c.inferred_kind:<18} {c.missing_ratio*100:>5.1f} "
              f"{c.n_unique:>7}  {tag} {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Profile EPC/APE city CSVs.")
    ap.add_argument("--input", required=True, help="CSV file or directory of CSVs")
    ap.add_argument("--output", default=None, help="Directory to write <city>.json profiles")
    ap.add_argument("--max-rows", type=int, default=None, help="Profile only first N rows")
    ap.add_argument("--preview", action="store_true", help="Print a console preview")
    args = ap.parse_args()

    in_path = Path(args.input)
    files = sorted(in_path.glob("*.csv")) if in_path.is_dir() else [in_path]
    out_dir = Path(args.output) if args.output else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        print(f"\n>>> profiling {f.name} ...")
        profile = profile_file(f, max_rows=args.max_rows)
        if out_dir:
            slug = unidecode(profile.city).lower().replace(" ", "_")
            dest = out_dir / f"{slug}.json"
            dest.write_text(profile.model_dump_json(indent=2))
            print(f"    wrote {dest}  ({profile.n_cols} cols, {profile.n_rows} rows)")
        if args.preview or not out_dir:
            print_preview(profile)


if __name__ == "__main__":
    main()
