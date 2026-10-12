"""Download the open geodata the city-dataset build needs.

Everything lands in data/static_data/geo/_cache/ (git-ignored); a source that
is already cached is skipped. Spec: docs/city-dataset-schema.md.

  madrid  Catastro INSPIRE cadastral parcels (municipality 28900), Madrid
          barrios (geoportal.madrid.es), OSM POIs
  london  OS Open UPRN (GB CSV, reduced to a box around Greater London), ONS
          wards Dec 2024 (BGC, London boroughs E09*), OSM POIs

POIs: Turin's Overpass query (data/metadata/pois_query.txt) with only the area
filter changed — Madrid a circle around the city like Turin's, London the
Greater London box + 5 km in 3×3 tiles (one query that size is refused). The
raw answer is then grouped by Turin's own data/metadata/pois_download.py,
unchanged.

Run (from backend/):
    python -m app.services.schema_alignment.download_city_geodata --city london
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from pyproj import Transformer

GEO_CACHE = Path("data/static_data/geo/_cache")
TURIN_QUERY = Path("data/metadata/pois_query.txt")
POI_SCRIPT = Path("data/metadata/pois_download.py")
# The mirrors (kumi.systems, mail.ru, private.coffee) were down or answered 504
# after minutes in Oct 2026; the main instance's 504s are load, so it is retried.
OVERPASS = ["https://overpass-api.de/api/interpreter"]
HEADERS = {"User-Agent": "MURENA-thesis/1.0 (city dataset build)"}
POI_MARGIN_M = 5000          # Turin's scoring radius (create_estate_dataset.MAX_DISTANCE_M)

CATASTRO_PARCELS = ("https://www.catastro.hacienda.gob.es/INSPIRE/CadastralParcels/28/28900-MADRID/"
                    "A.ES.SDGC.CP.28900.zip")
MADRID_BARRIOS = "https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/LIMITES_ADMINISTRATIVOS/Barrios/Barrios.zip"
OS_OPEN_UPRN = "https://api.os.uk/downloads/v1/products/OpenUPRN/downloads?area=GB&format=CSV&redirect"
ONS_WARDS = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
             "Wards_December_2024_Boundaries_UK_BGC/FeatureServer/0/query")
LONDON_WARDS = GEO_CACHE / "london_wards" / "wards_dec2024_london_27700.geojson"
LONDON_UPRN = GEO_CACHE / "os_open_uprn" / "london_bbox_uprn.parquet"


def log(msg: str) -> None:
    print(f"[geodata] {msg}", flush=True)


def download(url: str, path: Path) -> Path:
    """Stream ``url`` to ``path`` unless it exists (TLS verified with certifi)."""
    if path.exists():
        log(f"cached {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    tmp = path.with_suffix(path.suffix + ".part")
    with requests.get(url, stream=True, timeout=600, headers=HEADERS) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 22):
                f.write(chunk)
    tmp.rename(path)
    return path


# --------------------------------------------------------------------------- #
# POIs
# --------------------------------------------------------------------------- #
def turin_query(area_filter: str, settings: str = "[timeout:900]") -> str:
    """Turin's query with its area filter replaced; tags untouched. ``settings``
    replaces the declared resources: a loaded Overpass server schedules small
    declarations sooner, and a result that exceeds them comes back with a remark
    (an error here), never silently cut."""
    q = TURIN_QUERY.read_text()
    q = re.sub(r"\(around:[^)]*\)", area_filter, q)
    return re.sub(r"\[timeout:\d+\]", settings, q, count=1)


def overpass(query: str, attempts: int = 10) -> dict:
    for attempt in range(attempts):
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=1200)
            except requests.RequestException as e:
                log(f"  {url}: {type(e).__name__}")
                continue
            log(f"  {url}: HTTP {r.status_code}, {len(r.content) // 1_000_000} MB")
            if r.status_code == 200:
                data = r.json()
                if data.get("remark"):         # Overpass reports timeouts/limits here
                    raise RuntimeError(f"Overpass remark: {data['remark']}")
                return data
        time.sleep(min(30 * (attempt + 1), 180))
    raise RuntimeError("Overpass: no endpoint answered")


def fetch_pois(city: str, area_filters: list[str], settings: str = "[timeout:900]") -> Path:
    """Raw Overpass answer for every area (each cached as it arrives, so a rerun
    resumes; elements merged by type+id), then Turin's pois_download.py groups
    it -> pois_by_category_<city>.json."""
    d = GEO_CACHE / f"pois_{city}"
    d.mkdir(parents=True, exist_ok=True)
    raw, out = d / f"pois_{city}_raw.json", d / f"pois_by_category_{city}.json"
    query_file = d / f"pois_query_{city}.txt"
    query_file.write_text(turin_query(area_filters[0], settings))     # first area, for the record
    if not raw.exists():
        elements: dict[tuple, dict] = {}
        for i, area in enumerate(area_filters, 1):
            part = d / f"area_{len(area_filters)}_{i}.json"
            if not part.exists():
                log(f"POIs {city}: area {i}/{len(area_filters)} {area}")
                part.write_text(json.dumps(overpass(turin_query(area, settings))))
            for el in json.loads(part.read_text()).get("elements", []):
                elements[(el["type"], el["id"])] = el
        raw.write_text(json.dumps({"elements": list(elements.values())}))
        log(f"POIs {city}: {len(elements)} raw elements")
    if not out.exists():
        env = {**os.environ, "POI_QUERY_FILE": str(query_file), "POI_RAW_DATA_FILE": str(raw), "POI_PATH": str(out)}
        with open(d / "process.log", "w") as logf:
            subprocess.run([sys.executable, str(POI_SCRIPT)], env=env, check=True, stdout=logf, stderr=subprocess.STDOUT)
        log(f"POIs {city}: grouped by Turin's pois_download.py -> {out}")
    return out


def bbox_tiles(south: float, west: float, north: float, east: float, n: int = 2) -> list[str]:
    lat = [south + (north - south) * k / n for k in range(n + 1)]
    lon = [west + (east - west) * k / n for k in range(n + 1)]
    return [f"({lat[i]:.5f},{lon[j]:.5f},{lat[i + 1]:.5f},{lon[j + 1]:.5f})" for i in range(n) for j in range(n)]


# --------------------------------------------------------------------------- #
# Cities
# --------------------------------------------------------------------------- #
def madrid() -> None:
    download(CATASTRO_PARCELS, GEO_CACHE / "catastro_28900" / "A.ES.SDGC.CP.28900.zip")
    download(MADRID_BARRIOS, GEO_CACHE / "madrid_barrios" / "Barrios.zip")
    # circle like Turin's: centre of the municipality, radius to its farthest
    # corner (~24 km) + the 5 km scoring margin
    fetch_pois("madrid", ["(around:29000,40.4765,-3.6925)"])


def london_wards() -> dict:
    if not LONDON_WARDS.exists():
        LONDON_WARDS.parent.mkdir(parents=True, exist_ok=True)
        params = {"where": "LAD24CD LIKE 'E09%'", "outFields": "WD24CD,WD24NM,LAD24CD,LAD24NM",
                  "outSR": 27700, "f": "geojson", "resultRecordCount": 2000}
        gj = requests.get(ONS_WARDS, params=params, timeout=120, headers=HEADERS).json()
        if gj.get("properties", {}).get("exceededTransferLimit"):
            raise RuntimeError("ONS wards: more features than one page")
        LONDON_WARDS.write_text(json.dumps(gj))
        log(f"wards: {len(gj['features'])} London wards")
    return json.loads(LONDON_WARDS.read_text())


def _extent_27700(gj: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    for f in gj["features"]:
        walk(f["geometry"]["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def london() -> None:
    xmin, ymin, xmax, ymax = _extent_27700(london_wards())
    if not LONDON_UPRN.exists():
        z = download(OS_OPEN_UPRN, GEO_CACHE / "os_open_uprn" / "osopenuprn_202609_csv.zip")
        member = next(n for n in zipfile.ZipFile(z).namelist() if n.lower().endswith(".csv"))
        parts = []
        with zipfile.ZipFile(z).open(member) as f:
            for ch in pd.read_csv(f, chunksize=2_000_000, encoding="utf-8-sig"):
                parts.append(ch[ch.X_COORDINATE.between(xmin - 1000, xmax + 1000) &
                                ch.Y_COORDINATE.between(ymin - 1000, ymax + 1000)])
        pts = pd.concat(parts, ignore_index=True)
        pts.columns = ["uprn", "x", "y", "lat", "lon"]
        pts.to_parquet(LONDON_UPRN, index=False)
        log(f"OS Open UPRN: {len(pts)} points around Greater London")
    to_ll = Transformer.from_crs(27700, 4326, always_xy=True)
    west, south = to_ll.transform(xmin - POI_MARGIN_M, ymin - POI_MARGIN_M)
    east, north = to_ll.transform(xmax + POI_MARGIN_M, ymax + POI_MARGIN_M)
    # a 1/9 tile is a few tens of MB: declare 5 min / 256 MB so it is scheduled sooner
    fetch_pois("london", bbox_tiles(south, west, north, east, n=3), settings="[timeout:300][maxsize:268435456]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Download open geodata for a city dataset build.")
    ap.add_argument("--city", required=True, choices=["madrid", "london"])
    args = ap.parse_args()
    {"madrid": madrid, "london": london}[args.city]()


if __name__ == "__main__":
    main()
