"""Build a MURENA estates dataset for a new city from the common schema.

Spec: docs/city-dataset-schema.md. The output has exactly the columns and dtypes
of Turin's ``data/metadata/estates.parquet``, so the MURENA pipeline loads it
unchanged. Every target column is filled from one kind of source, and the build
report counts them per column:

  concept   a common-schema concept: the executor reads the raw column that
            reviewed_mappings.json assigns to it and applies its transform
  raw       a raw column the common schema does not cover (row keys, extras)
  geo       open geodata: coordinates, zone, proximity scores
  derived   Turin's own rules applied to the above (energy sub-scores, ids,
            whole-building rows); the scoring rules and constants are imported
            from data/metadata/create_estate_dataset.py, not copied
  constant / empty

A city is one entry in CITIES: which concepts and raw columns it reads, a
function that turns them into certificate rows in Turin's columns, where its
building-level coordinates come from, and its zones. Everything after that is
shared and follows Turin's builder in the same order: score every certificate,
drop undated ones, proximity -> percentile within the city, drop rows outside
every zone, keep the latest certificate per unit, add the whole-building rows.

Geodata comes from download_city_geodata.py. Run (from backend/):
    python -m app.services.schema_alignment.build_city_dataset --city madrid
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from pyproj import Transformer
from sklearn.neighbors import BallTree
from unidecode import unidecode

from .build_geolocation import load_geonames_postal
from .executor import _load, apply_cells, read_city_raw

# Turin's builder (scoring constants + map_*_score rules). Importing it needs the
# APE/target-city variables in backend/.env, which Turin's build already uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data" / "metadata"))
import create_estate_dataset as turin  # noqa: E402

TURIN_PARQUET = "data/metadata/estates.parquet"
GEO_CACHE = "data/static_data/geo/_cache"
OUT_PARQUET = "data/metadata/estates_{city}.parquet"
REPORT_DIR = "data/epc_schema/city_datasets"

# Turin's POI categories (amenities_config.py) -> Turin dataset columns
POI_CATEGORY_COLUMN = {
    "commercial": "commerciale", "education": "educazione", "mobility": "mobilita",
    "healthcare": "sanita", "sport": "sport", "green": "verde",
}
PROXIMITY = list(POI_CATEGORY_COLUMN.values())
# Concepts whose values outside the vocabulary's expected_range are blanked
# (e.g. construction year 28037, floor area 855,351 m²): such values would
# otherwise match range filters like "built after 2000".
RANGE_CHECKED = ("floor_area",)
SCORE_MAX_COLS = ["ape_score_impianto", "ape_score_involucro", "ape_score_rinnovabili"]
CONSUMPTION_COLS = ["epglnren_ape", "epglren_ape", "emissioni_co2"]
YEAR_RANGE = (1000, 2030)    # vocabulary construction_year expected_range

RESIDENTIAL = "Abitazioni adibite a residenza con carattere continuativo"
COMMERCIAL = "Attività commerciali e assimilabili"
OFFICES = "Uffici e assimilabili"


# --------------------------------------------------------------------------- #
# Small parsers
# --------------------------------------------------------------------------- #
_ADDR = re.compile(r"^\s*(?P<street>.*?)[\s,]+(?P<num>\d+[A-Za-z]?)\b")
_UK_NUMBERED = re.compile(r"^\s*(?P<num>\d+[A-Za-z]?)\s+(?P<street>\D.*?)\s*$")
_UK_SUBUNIT = re.compile(r"(?i)^\s*(flat|apartment|apt|unit|room|studio|maisonette|suite)\b")


def split_address(text) -> tuple[str | None, str | None]:
    """Spanish style 'Calle Calamocha 2 2º D' -> ('Calle Calamocha', '2');
    no number -> (text, None)."""
    s = str(text or "").strip()
    if not s:
        return None, None
    m = _ADDR.match(s)
    if not m or not m.group("street").strip(" ,"):
        return s, None
    return m.group("street").strip(" ,"), m.group("num")


def split_uk_address(*lines) -> tuple[str | None, str | None]:
    """UK style lines ('Flat 3, 51 Dunsmure Road', ...) -> ('Dunsmure Road', '51'):
    the first comma-separated part that starts with a house number; otherwise
    the first part that is not a flat/unit designation, without a number."""
    parts = [p.strip() for line in lines for p in str(line or "").split(",") if p.strip() and p.strip() != "nan"]
    for p in parts:
        m = _UK_NUMBERED.match(p)
        if m:
            return m.group("street"), m.group("num")
    for p in parts:
        if not _UK_SUBUNIT.match(p) and not re.fullmatch(r"\d+[A-Za-z]?", p):
            return p, None
    return None, None


def norm_text(text) -> str:
    """Accent-, case- and punctuation-insensitive form used in row keys."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unidecode(str(text or "")).lower()).split())


def year_text(values: pd.Series) -> pd.Series:
    """Years as Turin stores them (text, e.g. '1939'); missing or outside the
    vocabulary's construction-year range stays missing."""
    v = pd.to_numeric(values, errors="coerce").round()
    v = v.where(v.between(*YEAR_RANGE))
    return v.map(lambda x: str(int(x)) if pd.notna(x) else None)


def _int_or_zero(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.where(s.str.fullmatch(r"\d+", na=False)), errors="coerce").fillna(0).astype("int64")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce")


# --------------------------------------------------------------------------- #
# Madrid: certificate rows in Turin's columns
# --------------------------------------------------------------------------- #
MADRID_TYPES = {
    "ViviendaIndividualEnBloque": RESIDENTIAL,
    "ViviendaUnifamiliar": RESIDENTIAL,
    "BloqueDeViviendaCompleto": RESIDENTIAL,
    "Vivendas": RESIDENTIAL,                     # typo in a few source rows
    "LocalUsoTerciario": COMMERCIAL,             # tertiary premises: mostly shops
    "EdificioUsoTerciario": OFFICES,             # whole tertiary building: mostly offices
    "Edificio terciario": OFFICES,
}
# Spanish heating-demand class -> Turin's envelope quality labels
MADRID_DEMAND_QUALITY = {"A": "Good", "B": "Good", "C": "Neutral", "D": "Neutral",
                         "E": "Poor", "F": "Poor", "G": "Poor"}


def madrid_plant_label(system, fuel) -> str | None:
    """Spanish heating generator (+ fuel) -> the English terms Turin's
    map_impianto_score looks for (heat pump / district heating / condensing /
    biomass). Accents are dropped first, so mis-encoded source spellings match."""
    t, f = norm_text(system), norm_text(fuel)
    if not t and not f:
        return None
    if any(k in t for k in ("bomba de calor", "aerotermia", "aire agua", "vrf",
                            "refrigerante variable", "caudal ref")):
        return "heat pump"
    if "red de distrito" in t:
        return "district heating"
    if "condensac" in t:
        return "condensing boiler"
    if "biomasa" in f:
        return "biomass boiler"
    return str(system) if system is not None and str(system).strip() else None


def sheet_index(codes: pd.Series) -> dict[str, int]:
    """Cadastral map-sheet code ('VK4659F') -> integer 1..K in sorted order, so
    ``foglio`` keeps Turin's int dtype (0 = unknown, as in Turin)."""
    uniq = sorted(c for c in codes.dropna().unique() if c)
    return {c: i + 1 for i, c in enumerate(uniq)}


def madrid_rows(c: pd.DataFrame, raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    """Concept table ``c`` + raw extras -> certificate rows in Turin's columns,
    plus helper columns (prefixed ``_``) and the source of each column."""
    stats: dict = {"raw_rows": len(c)}
    in_city = c["municipality"].fillna("").str.strip().str.upper().eq("MADRID")
    tipo = c["building_type"].map(MADRID_TYPES)
    stats["in_municipality"] = int(in_city.sum())
    stats["dropped_unknown_type"] = int((in_city & tipo.isna()).sum())
    keep = in_city & tipo.notna()
    c, raw, tipo = c[keep], raw[keep], tipo[keep]

    ref = raw["edif_refcat"].fillna("").str.strip().str.upper()
    has_parcel = ref.str.match(r"^[0-9A-Z]{14}", na=False)
    sheets = ref.str[7:14].where(has_parcel)
    idx = sheet_index(sheets)
    addr = raw["edif_direc"].map(split_address)
    energy_class = c["energy_class"].fillna("").str.strip().str.upper()
    renew = (c["pv_electricity_production_total"].fillna(0) > 0) | \
            (c["renewable_energy_share"].fillna(0) > 0) | (_num(raw["termica_calef"]).fillna(0) > 0)

    rows = pd.DataFrame({
        "lista_file_ape": "['" + raw["edif_codreg"].str.strip() + "']",
        "codice_comune": CITIES["madrid"]["comune_code"],
        "foglio": sheets.map(idx).fillna(0).astype("int64"),
        "particella": _int_or_zero(ref.str[0:7].where(has_parcel)),
        "subalterno": _int_or_zero(ref.str[14:18]),
        "superficie_di_riferimento_mq": c["floor_area"],
        "indirizzo": addr.str[0],
        "numero_civico": addr.str[1],
        "tipologia_bene_immobile": tipo,
        "epoca_costruzione": year_text(c["construction_year"]),
        "data_decorrenza": pd.to_datetime(c["registration_date"], format="%d/%m/%Y", errors="coerce"),
        "classe_energetica_ape": energy_class.where(energy_class.isin(list("ABCDEFG"))),
        "epglnren_ape": c["primary_energy_total"],     # per m² despite the concept's "total" label
        "epglren_ape": np.nan,
        "emissioni_co2": c["co2_emissions_total"],     # per m² despite the concept's "total" label
        "classe_target_ape": None,
        "_plant": [madrid_plant_label(s, f) for s, f in zip(c["heating_system_type"], c["main_heating_fuel"])],
        "_envelope": raw["cal_calefdem"].str.strip().str.upper().map(MADRID_DEMAND_QUALITY),
        "_renewables": renew.astype(bool),
        "_parcel": ref.str[:14].where(has_parcel),
        "_building": ref.str[:14].where(has_parcel),
        "_unit": ref + "|" + raw["edif_direc"].map(norm_text),
        "_postcode": c["postal_code"].fillna("").str.replace(" ", "").str.upper(),
        "_block": c["building_type"].eq("BloqueDeViviendaCompleto"),
    })
    stats["sheet_codes"] = len(idx)
    stats["rows_without_parcel_ref"] = int((~has_parcel).sum())
    sources = {
        "lista_file_ape": "raw:edif_codreg", "list_file_ape_filtered": "raw:edif_codreg",
        "list_file_ape_filtered_parsed": "raw:edif_codreg",
        "codice_comune": "constant", "foglio": "raw:edif_refcat[7:14]",
        "particella": "raw:edif_refcat[0:7]", "subalterno": "raw:edif_refcat[14:18]",
        "superficie_di_riferimento_mq": "concept:floor_area",
        "indirizzo": "raw:edif_direc", "numero_civico": "raw:edif_direc",
        "tipologia_bene_immobile": "concept:building_type (+crosswalk)",
        "epoca_costruzione": "concept:construction_year",
        "data_decorrenza": "concept:registration_date",
        "classe_energetica_ape": "concept:energy_class",
        "epglnren_ape": "concept:primary_energy_total (per m²)",
        "epglren_ape": "empty", "emissioni_co2": "concept:co2_emissions_total (per m²)",
        "classe_target_ape": "empty",
        "ape_score_classe": "derived:concept:energy_class",
        "ape_score_impianto": "derived:concept:heating_system_type+main_heating_fuel",
        "ape_score_involucro": "derived:raw:cal_calefdem",
        "ape_score_rinnovabili": "derived:concept:pv_electricity_production_total+renewable_energy_share, raw:termica_calef",
        "ape_score_total": "derived",
        "latitudine": "geo:catastro parcel / postcode centroid",
        "longitudine": "geo:catastro parcel / postcode centroid",
        "zona_omi": "geo:barrio",
    }
    return rows, sources, {**stats, "sheet_index": idx}


# --------------------------------------------------------------------------- #
# London: certificate rows in Turin's columns
# --------------------------------------------------------------------------- #
LONDON_TYPES = {"Flat": RESIDENTIAL, "House": RESIDENTIAL, "Maisonette": RESIDENTIAL, "Bungalow": RESIDENTIAL}
# EPC element ratings (walls, roof, windows, floor) as numbers
LONDON_ELEMENT_RATING = {"very good": 5, "good": 4, "average": 3, "poor": 2, "very poor": 1}
LONDON_ENVELOPE_COLS = ["walls_energy_eff", "roof_energy_eff", "windows_energy_eff", "floor_energy_eff"]
# Old EPC age-band letters -> their ranges
LONDON_AGE_LETTERS = {"A": "before 1900", "B": "1900-1929", "C": "1930-1949", "D": "1950-1966",
                      "E": "1967-1975", "F": "1976-1982", "G": "1983-1990", "H": "1991-1995",
                      "I": "1996-2002", "J": "2003-2006", "K": "2007-2011", "L": "2012 onwards"}
LONDON_OPEN_BANDS = {"before 1900": 1890, "2012 onwards": 2016, "2022 onwards": 2024}


def london_year(band) -> int | None:
    """EPC construction age band -> one year: a single year as is, a range its
    middle year (1900-1929 -> 1915), open bands a fixed year (spec)."""
    s = str(band or "").replace("England and Wales:", "").strip()
    s = LONDON_AGE_LETTERS.get(s, s)
    if re.fullmatch(r"\d{4}", s):
        return int(s)
    m = re.fullmatch(r"(\d{4})-(\d{4})", s)
    if m:
        return (int(m.group(1)) + int(m.group(2)) + 1) // 2
    return LONDON_OPEN_BANDS.get(s.lower())


def london_plant_label(description) -> str | None:
    """Turin's heating rule already matches English 'heat pump'; a community
    scheme (communal or district heat network) counts as district heating."""
    s = str(description or "").strip()
    if not s or s == "nan":
        return None
    return "district heating" if "community scheme" in s.lower() else s


def london_envelope_label(ratings: pd.DataFrame) -> pd.Series:
    """Mean of the available element ratings (Very Good = 5 … Very Poor = 1):
    >= 4 Good, >= 3 Neutral, else Poor; no rating at all -> missing."""
    num = ratings.apply(lambda s: s.str.strip().str.lower().map(LONDON_ELEMENT_RATING))
    mean = num.mean(axis=1, skipna=True)
    return pd.Series(np.select([mean >= 4, mean >= 3, mean.notna()], ["Good", "Neutral", "Poor"], default=None),
                     index=ratings.index).replace({None: np.nan})


def london_rows(c: pd.DataFrame, raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict]:
    stats: dict = {"raw_rows": len(c)}
    in_city = c["municipality"].fillna("").str.strip().str.upper().eq("LONDON")
    tipo = c["building_type"].map(LONDON_TYPES)
    stats["in_municipality"] = int(in_city.sum())
    stats["dropped_unknown_type"] = int((in_city & tipo.isna()).sum())
    keep = in_city & tipo.notna()
    c, raw, tipo = c[keep], raw[keep], tipo[keep]

    uprn = pd.to_numeric(raw["uprn"], errors="coerce")
    addr = [split_uk_address(a, b, d) for a, b, d in zip(raw["address1"], raw["address2"], raw["address3"])]
    energy_class = c["energy_class"].fillna("").str.strip().str.upper()
    target = raw["potential_energy_rating"].fillna("").str.strip().str.upper()
    postcode = c["postal_code"].fillna("").str.replace(" ", "").str.upper()
    renew = (pd.to_numeric(c["solar_pv_present"], errors="coerce").fillna(0) > 0) | \
            c["solar_hot_water_present"].fillna("").str.strip().str.upper().eq("Y") | \
            (pd.to_numeric(raw["wind_turbine_count"], errors="coerce").fillna(0) > 0)
    address_key = [norm_text(f"{a} {b}") for a, b in zip(raw["address1"], raw["address2"])]

    rows = pd.DataFrame({
        "lista_file_ape": "['" + raw["certificate_number"].str.strip() + "']",
        "codice_comune": None,                                # the ward's borough, after coordinates
        "foglio": 0,                                          # no cadastre in England
        "particella": 0,                                      # building group id, set after coordinates
        "subalterno": uprn.fillna(0).astype("int64"),         # unique address number
        "superficie_di_riferimento_mq": c["floor_area"],
        "indirizzo": [a[0] for a in addr],
        "numero_civico": [a[1] for a in addr],
        "tipologia_bene_immobile": tipo,
        "epoca_costruzione": year_text(c["construction_period"].map(london_year)),
        "data_decorrenza": pd.to_datetime(c["registration_date"], errors="coerce"),
        "classe_energetica_ape": energy_class.where(energy_class.isin(list("ABCDEFG"))),
        "epglnren_ape": _num(raw["energy_consumption_current"]),   # primary energy, kWh/m²·yr
        "epglren_ape": np.nan,
        "emissioni_co2": c["co2_emissions_intensity"],
        "classe_target_ape": target.where(target.isin(list("ABCDEFG"))),
        "_plant": c["heating_system_type"].map(london_plant_label),
        "_envelope": london_envelope_label(raw[LONDON_ENVELOPE_COLS]),
        "_renewables": renew.astype(bool),
        "_uprn": uprn,
        "_building": None,                                    # postcode + shared UPRN point, after coordinates
        "_unit": np.where(uprn.notna(), "U" + uprn.astype("Int64").astype(str),
                          "A" + pd.Series(address_key, index=c.index) + "|" + postcode),
        "_postcode": postcode,
        "_block": False,                                      # no whole-building certificates
    })
    stats["rows_without_uprn"] = int(uprn.isna().sum())
    sources = {
        "lista_file_ape": "raw:certificate_number", "list_file_ape_filtered": "raw:certificate_number",
        "list_file_ape_filtered_parsed": "raw:certificate_number",
        "codice_comune": "geo:ward's borough (LAD24CD)",
        "foglio": "constant", "particella": "derived:building group (postcode + UPRN point)",
        "subalterno": "raw:uprn",
        "superficie_di_riferimento_mq": "concept:floor_area",
        "indirizzo": "raw:address1-3", "numero_civico": "raw:address1-3",
        "tipologia_bene_immobile": "concept:building_type (+crosswalk)",
        "epoca_costruzione": "concept:construction_period (band -> year)",
        "data_decorrenza": "concept:registration_date",
        "classe_energetica_ape": "concept:energy_class",
        "epglnren_ape": "raw:energy_consumption_current",
        "epglren_ape": "empty", "emissioni_co2": "concept:co2_emissions_intensity",
        "classe_target_ape": "raw:potential_energy_rating",
        "ape_score_classe": "derived:concept:energy_class",
        "ape_score_impianto": "derived:concept:heating_system_type",
        "ape_score_involucro": "derived:raw:walls/roof/windows/floor_energy_eff",
        "ape_score_rinnovabili": "derived:concept:solar_pv_present+solar_hot_water_present, raw:wind_turbine_count",
        "ape_score_total": "derived",
        "latitudine": "geo:OS Open UPRN / postcode centroid",
        "longitudine": "geo:OS Open UPRN / postcode centroid",
        "zona_omi": "geo:ward",
    }
    return rows, sources, stats


def london_after_geo(rows: pd.DataFrame) -> pd.DataFrame:
    """Borough = the borough of the ward the point lies in, so ``codice_comune``
    and ``zona_omi`` always agree (the register's local_authority differs for
    0.2 % of rows, all neighbouring boroughs). Building = postcode + shared UPRN
    point (flats of one building share the point in 85 % of address-defined
    buildings), numbered 1..K into ``particella``."""
    rows = rows.copy()
    rows["codice_comune"] = rows["_lad"]
    on_point = rows["_coord_source"].eq("uprn")
    key = rows["_postcode"] + "|" + rows["_x"].round(2).astype(str) + "|" + rows["_y"].round(2).astype(str)
    rows["_building"] = key.where(on_point)
    codes, _ = pd.factorize(rows["_building"], sort=True)
    rows["particella"] = np.where(codes >= 0, codes + 1, 0)
    return rows


# --------------------------------------------------------------------------- #
# Geodata
# --------------------------------------------------------------------------- #
def catastro_points(rows: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Madrid: parcel reference -> Catastro reference point (x, y in cfg['crs']).
    A parcel made of several pieces has one point per piece (a few hundred,
    within ~100 m of each other); their mean is used. Cached as parquet."""
    zip_path, member = cfg["parcels_zip"], cfg["parcels_member"]
    cache = Path(zip_path).with_name("parcels_points.parquet")
    p = pd.read_parquet(cache) if cache.exists() else pd.DataFrame()
    if not {"ref", "x", "y"} <= set(p.columns):
        con = duckdb.connect()
        con.execute("LOAD spatial")
        p = con.execute(f"SELECT nationalCadastralReference AS ref, pos FROM ST_Read('/vsizip/{zip_path}/{member}')").df()
        xy = p["pos"].str.split(expand=True).astype(float)
        p = pd.DataFrame({"ref": p["ref"], "x": xy[0], "y": xy[1]})
        p.to_parquet(cache, index=False)
    p = p.groupby("ref")[["x", "y"]].mean()
    return rows["_parcel"].map(p["x"]), rows["_parcel"].map(p["y"])


def uprn_points(rows: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """London: UPRN -> OS Open UPRN point (British National Grid)."""
    p = pd.read_parquet(cfg["uprn_points"], columns=["uprn", "x", "y"]).drop_duplicates("uprn").set_index("uprn")
    return rows["_uprn"].map(p["x"]), rows["_uprn"].map(p["y"])


def assign_coordinates(rows: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Building-level point from the city's own source first, else the postcode
    centroid. Adds latitudine, longitudine and helpers _x/_y (metres, cfg['crs'])
    and _coord_source."""
    x, y = cfg["points"](rows, cfg)
    x, y = x.astype(float), y.astype(float)
    src = pd.Series(np.where(x.notna(), cfg["points_label"], None), index=rows.index, dtype=object)

    to_xy = Transformer.from_crs(4326, cfg["crs"], always_xy=True)
    postal = load_geonames_postal(cfg["postal_country"])
    need = x.isna() & rows["_postcode"].isin(postal.keys())
    if need.any():
        lat_lon = rows.loc[need, "_postcode"].map(postal)
        px, py = to_xy.transform([p[1] for p in lat_lon], [p[0] for p in lat_lon])
        x.loc[need], y.loc[need] = px, py
        src.loc[need] = "postcode"

    to_ll = Transformer.from_crs(cfg["crs"], 4326, always_xy=True)
    lon, lat = to_ll.transform(x.to_numpy(dtype=float), y.to_numpy(dtype=float))
    no_point = x.isna().to_numpy()                 # some projections turn NaN into inf
    lat, lon = np.where(no_point, np.nan, lat), np.where(no_point, np.nan, lon)
    out = rows.assign(_x=x, _y=y, _coord_source=src, latitudine=lat, longitudine=lon)
    return out, {"coordinate_source": {str(k): int(v) for k, v in src.value_counts(dropna=False).items()}}


def assign_zones(rows: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Zone attributes for each point (point-in-polygon in the zones' CRS);
    returns one column per entry of cfg['zone_fields'], missing outside."""
    pts = pd.DataFrame({"i": np.arange(len(rows)), "x": rows["_x"].to_numpy(), "y": rows["_y"].to_numpy()}).dropna()
    fields = cfg["zone_fields"]
    select = ", ".join(f"z.{src} AS {dst}" for dst, src in fields.items())
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.register("pts", pts)
    hit = con.execute(
        f"SELECT p.i, {select} FROM pts p "
        f"JOIN ST_Read('{cfg['zones']}') z ON ST_Within(ST_Point(p.x, p.y), z.geom)"
    ).df().drop_duplicates("i").set_index("i")
    pos = pd.Series(np.arange(len(rows)), index=rows.index)
    return pd.DataFrame({dst: pos.map(hit[dst]) for dst in fields}, index=rows.index)


def proximity_raw_scores(lat, lon, pois_by_cat: dict) -> pd.DataFrame:
    """Turin's amenity score for every point, vectorised: per POI within
    MAX_DISTANCE_M, exp(-(d / (S·U))³); summed per amenity type and averaged over
    the category's amenity types (= category sum / number of types). Same
    constants and result as create_estate_dataset.get_amenity_scores."""
    cats = sorted(pois_by_cat)
    coords, cat_idx, s_base = [], [], []
    n_types = np.array([len(pois_by_cat[c]) for c in cats], dtype=float)
    for ci, cat in enumerate(cats):
        for amenity, points in pois_by_cat[cat].items():
            s = turin.SERVICE_BASE[turin.AMENITY_TO_CLASS.get(amenity, "lungo")]
            for p in points:
                coords.append((np.radians(p["lat"]), np.radians(p["lon"])))
                cat_idx.append(ci)
                s_base.append(s)
    tree = BallTree(np.array(coords), metric="haversine")
    cat_idx, s_base = np.array(cat_idx), np.array(s_base)
    u = turin.URBAN_MULTIPLIER[turin.DEFAULT_ZONE]
    radius = turin.MAX_DISTANCE_M / 1000 / turin.EARTH_RADIUS_KM

    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    out = np.zeros((len(lat), len(cats)))
    ok = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    query = np.radians(np.column_stack([lat[ok], lon[ok]]))
    for start in range(0, len(ok), 2000):
        ind, dist = tree.query_radius(query[start:start + 2000], r=radius, return_distance=True)
        for k, (ii, dd) in enumerate(zip(ind, dist)):
            if len(ii):
                score = np.exp(-((dd * turin.EARTH_RADIUS_KM) / (s_base[ii] * u)) ** 3)
                sums = np.bincount(cat_idx[ii], weights=score, minlength=len(cats))
                out[ok[start + k]] = np.divide(sums, n_types, out=np.zeros_like(sums), where=n_types > 0)
    return pd.DataFrame(out, columns=[POI_CATEGORY_COLUMN.get(c, c) for c in cats])


# --------------------------------------------------------------------------- #
# Turin's row-level steps
# --------------------------------------------------------------------------- #
def energy_scores(rows: pd.DataFrame) -> pd.DataFrame:
    """Turin's four sub-scores (imported rules) and their sum."""
    out = rows.copy()
    out["ape_score_classe"] = out["classe_energetica_ape"].map(turin.map_classe_score)
    out["ape_score_impianto"] = out["_plant"].map(turin.map_impianto_score)
    out["ape_score_involucro"] = out["_envelope"].map(turin.map_involucro_score)
    out["ape_score_rinnovabili"] = out["_renewables"].map(lambda v: turin.map_rinnovabili_score(bool(v)))
    out["ape_score_total"] = out[["ape_score_classe", "ape_score_impianto",
                                  "ape_score_involucro", "ape_score_rinnovabili"]].sum(axis=1)
    return out


def latest_per_unit(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per unit: the most recent certificate (Turin keeps the latest APE)."""
    rows = rows.sort_values(["_unit", "data_decorrenza"], ascending=[True, False])
    return rows.drop_duplicates("_unit", keep="first").sort_values("data_decorrenza", ascending=False)


def _mode(s: pd.Series):
    m = s.mode()
    return m.iloc[0] if not m.empty else None


def building_rows(units: pd.DataFrame) -> pd.DataFrame:
    """Whole-building rows (Turin's metaimmobili): one per building with more
    than one unit, aggregated with Turin's rules. Buildings that already have a
    whole-building certificate get none (the certificate is the building row)."""
    meta = []
    grouped = units[units["_building"].notna()].groupby("_building", sort=True)
    for _, g in grouped:
        if len(g) < 2 or g["_block"].any():
            continue
        row = {
            "foglio": g["foglio"].iloc[0], "particella": g["particella"].iloc[0], "subalterno": 0,
            "superficie_di_riferimento_mq": g["superficie_di_riferimento_mq"].sum(),
            "tipologia_bene_immobile": g.groupby("tipologia_bene_immobile")["superficie_di_riferimento_mq"].sum().idxmax(),
            "id": f"M{len(meta) + 1:05d}", "meta_immobile": True,
            "numero_immobili_per_catasto": len(g),
            "id_list": str(g["id"].astype(int).tolist()),
        }
        files = sorted({f for s in g["lista_file_ape"] for f in eval(s)})  # noqa: S307 - our own "['..']" strings
        for col in ("lista_file_ape", "list_file_ape_filtered", "list_file_ape_filtered_parsed"):
            row[col] = str(files)
        row["epoca_costruzione"] = _mode(g["epoca_costruzione"])
        by_class = g.groupby("classe_energetica_ape")["superficie_di_riferimento_mq"].sum()
        row["classe_energetica_ape"] = by_class.idxmax() if not by_class.empty else None
        for col in SCORE_MAX_COLS:
            row[col] = g[col].max()
        row["ape_score_classe"] = turin.map_classe_score(row["classe_energetica_ape"])
        row["ape_score_total"] = sum(int(row[c] or 0) for c in ["ape_score_classe", *SCORE_MAX_COLS])
        for col in CONSUMPTION_COLS:
            row[col] = g[col].min()
        for col in g.columns:
            if col not in row and not col.startswith("_"):
                row[col] = _mode(g[col])
        meta.append(row)
    return pd.DataFrame(meta)


def to_turin_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Turin's column order and dtypes (read from the Turin parquet itself)."""
    schema = pq.read_schema(TURIN_PARQUET)
    turin_types = pd.read_parquet(TURIN_PARQUET).head(0).dtypes
    out = pd.DataFrame(index=df.index)
    for name in schema.names:
        col = df[name] if name in df.columns else pd.Series(None, index=df.index, dtype=object)
        t = turin_types[name]
        if pd.api.types.is_bool_dtype(t):
            out[name] = col.astype(bool)
        elif pd.api.types.is_integer_dtype(t):
            out[name] = pd.to_numeric(col, errors="coerce").fillna(0).astype(t)
        elif pd.api.types.is_float_dtype(t):
            out[name] = pd.to_numeric(col, errors="coerce").astype(t)
        elif pd.api.types.is_datetime64_any_dtype(t):
            out[name] = pd.to_datetime(col, errors="coerce").astype(t)
        else:
            out[name] = col.astype(t)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Cities
# --------------------------------------------------------------------------- #
CITIES = {
    "madrid": {
        "profile": "Madrid",
        "rows": madrid_rows,
        "comune_code": "28079",          # INE code of the municipality of Madrid
        "concepts": [
            "municipality", "postal_code", "floor_area", "building_type", "construction_year",
            "registration_date", "energy_class", "primary_energy_total", "co2_emissions_total",
            "heating_system_type", "main_heating_fuel", "pv_electricity_production_total",
            "renewable_energy_share",
        ],
        "extra_raw": ("edif_refcat", "edif_direc", "edif_codreg", "cal_calefdem", "termica_calef"),
        "points": catastro_points, "points_label": "parcel",
        "parcels_zip": f"{GEO_CACHE}/catastro_28900/A.ES.SDGC.CP.28900.zip",
        "parcels_member": "A.ES.SDGC.CP.28900.cadastralparcel.gml",
        "postal_country": "ES",
        "crs": 25830,                    # ETRS89 / UTM 30N (Catastro and the barrios file)
        "zones": f"/vsizip/{GEO_CACHE}/madrid_barrios/Barrios.zip/BARRIOS.shp",
        "zone_fields": {"zona_omi": "NOMBRE"},
        "pois": f"{GEO_CACHE}/pois_madrid/pois_by_category_madrid.json",
    },
    "london": {
        "profile": "London",
        "rows": london_rows,
        "after_geo": london_after_geo,
        "concepts": [
            "municipality", "postal_code", "floor_area", "building_type", "construction_period",
            "registration_date", "energy_class", "co2_emissions_intensity", "heating_system_type",
            "solar_pv_present", "solar_hot_water_present",
        ],
        "extra_raw": ("certificate_number", "uprn", "address1", "address2", "address3",
                      "energy_consumption_current", "potential_energy_rating", *LONDON_ENVELOPE_COLS,
                      "wind_turbine_count"),
        "points": uprn_points, "points_label": "uprn",
        "uprn_points": f"{GEO_CACHE}/os_open_uprn/london_bbox_uprn.parquet",
        "postal_country": "GB",
        "crs": 27700,                    # British National Grid (OS Open UPRN and the ONS wards)
        "zones": f"{GEO_CACHE}/london_wards/wards_dec2024_london_27700.geojson",
        "zone_fields": {"zona_omi": "WD24NM", "_lad": "LAD24CD"},
        "pois": f"{GEO_CACHE}/pois_london/pois_by_category_london.json",
    },
}


def build(city: str) -> dict:
    cfg = CITIES[city]
    vmeta, rev, profiles = _load()
    profile = profiles[cfg["profile"]]
    raw = read_city_raw(cfg["profile"], profile, rev, cfg["concepts"], extra_columns=cfg["extra_raw"])
    concepts, exec_report = apply_cells(raw, cfg["profile"], profile, rev, vmeta, cfg["concepts"])
    out_of_range = {}
    for cid in RANGE_CHECKED:
        lo, hi = vmeta[cid]["expected_range"]
        out_of_range[cid] = concepts[cid].notna() & ~concepts[cid].between(lo, hi)
        concepts.loc[out_of_range[cid], cid] = np.nan
    rows, sources, stats = cfg["rows"](concepts, raw)
    stats["blanked_floor_area_out_of_vocab_range"] = int(out_of_range["floor_area"].loc[rows.index].sum())
    stats["missing_construction_year"] = int(rows["epoca_costruzione"].isna().sum())

    rows, geo_stats = assign_coordinates(rows, cfg)
    zones = assign_zones(rows, cfg)
    rows[list(zones.columns)] = zones
    if cfg.get("after_geo"):
        rows = cfg["after_geo"](rows)
    pois = json.loads(Path(cfg["pois"]).read_text())
    rows[PROXIMITY] = proximity_raw_scores(rows["latitudine"], rows["longitudine"], pois).set_index(rows.index)
    rows = energy_scores(rows)

    steps = {"certificates": len(rows)}
    rows = rows.dropna(subset=["data_decorrenza"])
    steps["with_date"] = len(rows)
    for col in PROXIMITY:                      # Turin: percentile over all certificates
        rows[col] = (rows[col].rank(pct=True) * 100).round(2)
    rows = rows[rows["zona_omi"].notna()]
    steps["inside_zones"] = len(rows)
    units = latest_per_unit(rows).reset_index(drop=True)
    steps["units"] = len(units)

    units["id"] = (units.index + 1).astype(str)
    units["meta_immobile"] = False
    units["numero_immobili_per_catasto"] = 1
    units["id_list"] = None
    units["list_file_ape_filtered"] = units["lista_file_ape"]
    units["list_file_ape_filtered_parsed"] = units["lista_file_ape"]
    meta = building_rows(units)
    steps["building_rows"] = len(meta)
    final = to_turin_schema(pd.concat([units, meta], ignore_index=True))

    out_path = OUT_PARQUET.format(city=city)
    final.to_parquet(out_path, index=False)
    sources.update({
        "id": "derived", "meta_immobile": "derived", "numero_immobili_per_catasto": "derived",
        "id_list": "derived", **{c: "geo:OSM POIs (Turin formula, percentile)" for c in PROXIMITY},
    })
    return _report(city, final, sources, {**stats, **geo_stats, "steps": steps}, exec_report, out_path)


def _report(city: str, df: pd.DataFrame, sources: dict, stats: dict, exec_report: list, out_path: str) -> dict:
    idx = stats.pop("sheet_index", None)
    units = df[~df["meta_immobile"]]
    columns = []
    for name in df.columns:
        col = df[name]
        filled = col.notna() & (col.astype(str).str.strip() != "")
        if pd.api.types.is_integer_dtype(col) and name in ("foglio", "particella", "subalterno"):
            filled = col != 0
        columns.append({"column": name, "source": sources.get(name, "?"),
                        "fill_units": round(float(filled[~df["meta_immobile"]].mean()), 3)})
    kinds = Counter(c["source"].split(":")[0] for c in columns)
    report = {
        "city": city, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output": out_path, "rows": len(df), "units": len(units), "building_rows": int(df["meta_immobile"].sum()),
        "source_kinds": dict(kinds), "stats": stats, "columns": columns,
        "distributions": {
            "tipologia_bene_immobile": units["tipologia_bene_immobile"].value_counts().to_dict(),
            "classe_energetica_ape": units["classe_energetica_ape"].value_counts().sort_index().to_dict(),
            "ape_score_total": {int(k): int(v) for k, v in units["ape_score_total"].value_counts().sort_index().items()},
            "zona_omi_count": int(units["zona_omi"].nunique()),
        },
        "concept_cells": [{k: r[k] for k in ("concept", "status", "fill_rate", "range_ok")} for r in exec_report],
    }
    Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
    Path(REPORT_DIR, f"{city}.build_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if idx:
        pd.Series(idx, name="foglio").rename_axis("sheet_code").to_csv(Path(REPORT_DIR, f"{city}_sheet_index.csv"))
    lines = [f"# {city.title()} dataset build — {report['generated_at']}", "",
             f"Output `{out_path}`: {report['rows']} rows ({report['units']} units + {report['building_rows']} building rows).",
             f"Source kinds over the {len(columns)} columns: {report['source_kinds']}", "",
             f"Steps: {stats['steps']}", f"Coordinates: {stats.get('coordinate_source')}", "",
             "| column | source | fill (units) |", "|---|---|---|"]
    lines += [f"| `{c['column']}` | {c['source']} | {c['fill_units']} |" for c in columns]
    Path(REPORT_DIR, f"{city}.build_report.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a MURENA estates dataset for a city.")
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    args = ap.parse_args()
    report = build(args.city)
    print(json.dumps({k: report[k] for k in ("output", "rows", "units", "building_rows", "source_kinds")}, indent=2))
    print(json.dumps(report["stats"], indent=2, default=str))


if __name__ == "__main__":
    main()
