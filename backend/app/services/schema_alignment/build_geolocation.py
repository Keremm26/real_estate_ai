"""
Build per-city geolocation artifacts for the common-schema EPC/APE dataset.

For each city we produce a GeoJSON point layer plus a combined, self-contained
interactive HTML map. This is the cross-city analogue of Turin's OMI-zone file:
instead of market-zone polygons (which have no uniform pan-European source), we
place each city's certificates on the map at the best resolution its data allows.

Coordinate sourcing per city (see CITY_CONFIG):
  * latlon    - the normalized data already carries WGS84 lat/lon (building level)
  * proj_l93  - projected Lambert-93 x/y -> WGS84 via pyproj (Paris, building level)
  * postal    - postal code -> centroid via GeoNames free postal files (postcode level)
  * admin     - admin-unit name -> centroid via Nominatim, cached (municipality level)

Run:  .venv/bin/python -m app.services.schema_alignment.build_geolocation
"""

from __future__ import annotations

import csv
import io
import json
import os
import random
import sys
import time
import zipfile
from collections import Counter, defaultdict
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------

CAP = 10_000            # max certificates sampled per city
SEED = 42               # reproducible sampling
NOMINATIM_SLEEP = 1.1   # seconds between live Nominatim calls (usage policy)
MAX_ADMIN_UNITS = 200   # cap distinct admin units geocoded per city

HERE = os.path.dirname(os.path.abspath(__file__))
# .../backend/app/services/schema_alignment -> .../backend
BACKEND_DIR = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DATA_DIR = os.path.join(BACKEND_DIR, "data")
NORM_DIR = os.path.join(DATA_DIR, "epc_schema", "normalized")
RAW_DIR = os.path.join(DATA_DIR, "epc_raw")
OUT_DIR = os.path.join(DATA_DIR, "static_data", "geo")
CACHE_DIR = os.path.join(OUT_DIR, "_cache")
ADMIN_CACHE_FILE = os.path.join(CACHE_DIR, "admin_geocode_cache.json")

# GeoNames free postal archives (downloaded + cached under _cache/)
GEONAMES = {
    "GB": ("https://download.geonames.org/export/zip/GB_full.csv.zip", "GB_full.txt"),
    "NL": ("https://download.geonames.org/export/zip/NL_full.csv.zip", "NL_full.txt"),
    "ES": ("https://download.geonames.org/export/zip/ES.zip", "ES.txt"),
}

# Per-city configuration. source="norm" reads the clean normalized CSV;
# source="raw" reads the original dump (used where normalization dropped geo).
CITY_CONFIG = {
    "turin":      dict(country="IT", source="norm", strategy="latlon",
                       energy="energy_class", resolution="building"),
    "barcelona":  dict(country="ES", source="norm", strategy="latlon",
                       energy="energy_class", resolution="building"),
    "copenhagen": dict(country="DK", source="norm", strategy="latlon",
                       energy="energy_class", resolution="building"),
    "paris":      dict(country="FR", source="norm", strategy="proj_l93",
                       energy="energy_class", resolution="building"),
    "london":     dict(country="GB", source="norm", strategy="postal", geonames="GB",
                       energy="energy_class", resolution="postcode"),
    "amsterdam":  dict(country="NL", source="norm", strategy="postal", geonames="NL",
                       energy="energy_class", resolution="postcode"),
    "madrid":     dict(country="ES", source="norm", strategy="postal", geonames="ES",
                       energy="energy_class", resolution="postcode"),
    "dublin":     dict(country="IE", source="raw", file="Ireland_EPC_Dublin_city.csv",
                       strategy="admin", admin_field="dublin_district",
                       admin_query="{v}, Dublin, Ireland",
                       energy="EnergyRating", resolution="district"),
    "lisbon":     dict(country="PT", source="raw", file="Lisbon_residential.csv",
                       strategy="admin", admin_field="Freguesia_Fiscal",
                       admin_query="{v}, Lisboa, Portugal",
                       energy=None, resolution="parish"),
    # Liège dump is a single municipality with no sub-municipal or coordinate
    # field, so every certificate resolves to the city centre (one point).
    "liege":      dict(country="BE", source="raw", file="Liege_residential.csv",
                       strategy="admin", admin_field="communes",
                       admin_query="{v}, Belgium",
                       energy="e_spec_label", resolution="city"),
}

# Approx. city centres (fallback + initial map view)
CITY_CENTER = {
    "turin": (45.0703, 7.6869), "barcelona": (41.3874, 2.1686),
    "copenhagen": (55.6761, 12.5683), "paris": (48.8566, 2.3522),
    "london": (51.5074, -0.1278), "amsterdam": (52.3676, 4.9041),
    "madrid": (40.4168, -3.7038), "dublin": (53.3498, -6.2603),
    "lisbon": (38.7223, -9.1393), "liege": (50.6326, 5.5797),
}

ENERGY_COLORS = {
    "A": "#1a9850", "B": "#66bd63", "C": "#a6d96a", "D": "#fee08b",
    "E": "#fdae61", "F": "#f46d43", "G": "#d73027",
}
UNKNOWN_COLOR = "#9e9e9e"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def energy_band(value: Optional[str]) -> Optional[str]:
    """Reduce a heterogeneous energy label (A, B2, A1, '3'...) to a band A-G."""
    if not value:
        return None
    ch = value.strip().upper()[:1]
    return ch if ch in ENERGY_COLORS else None


def reservoir_sample(path: str, cap: int, delimiter: str = ",",
                     encoding: str = "utf-8") -> list[dict]:
    """Uniform random sample of <=cap rows from a (possibly huge) CSV."""
    rng = random.Random(SEED)
    sample: list[dict] = []
    # latin-1 never raises on decode; use it for raw dumps of unknown encoding
    with open(path, encoding=encoding, errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for i, row in enumerate(reader):
            if i < cap:
                sample.append(row)
            else:
                j = rng.randint(0, i)
                if j < cap:
                    sample[j] = row
    return sample


# ---------------------------------------------------------------------------
# Coordinate sources
# ---------------------------------------------------------------------------

def load_geonames_postal(code: str) -> dict[str, tuple[float, float]]:
    """postal_key -> (lat, lon) from a cached GeoNames archive (download if needed)."""
    url, member = GEONAMES[code]
    zip_path = os.path.join(CACHE_DIR, f"{code}.zip")
    if not os.path.exists(zip_path):
        log(f"  downloading GeoNames {code} ...")
        req = Request(url, headers={"User-Agent": "epc-geo-builder/1.0"})
        with urlopen(req, timeout=60) as resp, open(zip_path, "wb") as out:
            out.write(resp.read())
    mapping: dict[str, tuple[float, float]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 11:
                    continue
                key = parts[1].replace(" ", "").upper()
                try:
                    mapping[key] = (float(parts[9]), float(parts[10]))
                except ValueError:
                    continue
    log(f"  GeoNames {code}: {len(mapping):,} postal centroids")
    return mapping


class AdminGeocoder:
    """Nominatim geocoder for admin-unit names, with a persistent JSON cache."""

    def __init__(self) -> None:
        self.cache: dict[str, list] = {}
        if os.path.exists(ADMIN_CACHE_FILE):
            try:
                with open(ADMIN_CACHE_FILE, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

    def _save(self) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(ADMIN_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=1)

    def get(self, query: str) -> Optional[tuple[float, float]]:
        if query in self.cache:
            v = self.cache[query]
            return (v[0], v[1]) if v else None
        params = urlencode({"q": query, "format": "json", "limit": 1})
        req = Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"User-Agent": "epc-geo-builder/1.0 (thesis research)"},
        )
        result = None
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.load(resp)
            if data:
                result = (float(data[0]["lat"]), float(data[0]["lon"]))
        except Exception as e:
            log(f"    ! geocode failed for {query!r}: {e}")
        self.cache[query] = list(result) if result else None
        self._save()
        time.sleep(NOMINATIM_SLEEP)
        return result


# ---------------------------------------------------------------------------
# Per-city feature building
# ---------------------------------------------------------------------------

def build_city(city: str, cfg: dict, geocoder: AdminGeocoder) -> dict:
    """Return {features, stats} for one city."""
    if cfg["source"] == "norm":
        path = os.path.join(NORM_DIR, f"{city}.csv")
        encoding = "utf-8"
    else:
        path = os.path.join(RAW_DIR, cfg["file"])
        encoding = "utf-8"  # raw dumps are UTF-8; errors="replace" guards anyway

    if not os.path.exists(path):
        log(f"  ! missing source {path}")
        return {"features": [], "stats": {}}

    rows = reservoir_sample(path, CAP, encoding=encoding)
    log(f"  sampled {len(rows):,} rows from {os.path.basename(path)}")

    energy_field = cfg.get("energy")
    strategy = cfg["strategy"]
    features: list[dict] = []
    placed = 0

    if strategy == "postal":
        postal = load_geonames_postal(cfg["geonames"])
        for r in rows:
            key = (r.get("postal_code") or "").replace(" ", "").upper()
            coord = postal.get(key)
            if coord:
                features.append(_point(coord, r.get("postal_code"),
                                       energy_band(r.get(energy_field)), cfg))
                placed += 1

    elif strategy == "admin":
        field = cfg["admin_field"]
        counts = Counter((r.get(field) or "").strip() for r in rows if (r.get(field) or "").strip())
        keep = {name for name, _ in counts.most_common(MAX_ADMIN_UNITS)}
        coords: dict[str, Optional[tuple[float, float]]] = {}
        log(f"  geocoding {len(keep)} admin units ({field}) ...")
        for name in keep:
            coords[name] = geocoder.get(cfg["admin_query"].format(v=name))
        for r in rows:
            name = (r.get(field) or "").strip()
            coord = coords.get(name)
            if coord:
                features.append(_point(coord, name,
                                       energy_band(r.get(energy_field)), cfg))
                placed += 1

    elif strategy == "proj_l93":
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
        for r in rows:
            try:
                x = float(r["projected_x_coordinate"])
                y = float(r["projected_y_coordinate"])
            except (KeyError, ValueError, TypeError):
                continue
            lon, lat = tr.transform(x, y)
            if 40 < lat < 55 and -6 < lon < 12:  # sanity: within France-ish
                features.append(_point((lat, lon), r.get("postal_code"),
                                       energy_band(r.get(energy_field)), cfg))
                placed += 1

    else:  # latlon
        for r in rows:
            try:
                lat = float(r["latitude"])
                lon = float(r["longitude"])
            except (KeyError, ValueError, TypeError):
                continue
            if lat == 0 and lon == 0:
                continue
            features.append(_point((lat, lon), None,
                                   energy_band(r.get(energy_field)), cfg))
            placed += 1

    band_counts = Counter(f["properties"]["band"] or "?" for f in features)
    stats = {
        "country": cfg["country"],
        "resolution": cfg["resolution"],
        "strategy": strategy,
        "sampled": len(rows),
        "placed": placed,
        "placed_pct": round(100 * placed / len(rows), 1) if rows else 0,
        "energy_bands": dict(sorted(band_counts.items())),
    }
    log(f"  placed {placed:,}/{len(rows):,} ({stats['placed_pct']}%) at {cfg['resolution']} level")
    return {"features": features, "stats": stats}


def _point(coord: tuple[float, float], label: Optional[str],
           band: Optional[str], cfg: dict) -> dict:
    lat, lon = coord
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": {
            "band": band,
            "resolution": cfg["resolution"],
            "label": label,
        },
    }


# ---------------------------------------------------------------------------
# HTML map
# ---------------------------------------------------------------------------

def build_html(city_layers: dict[str, dict], inventory: dict) -> str:
    payload = {c: {"features": d["features"], "center": CITY_CENTER[c],
                   "stats": inventory[c]} for c, d in city_layers.items()}
    data_json = json.dumps(payload, ensure_ascii=False)
    colors_json = json.dumps(ENERGY_COLORS)

    rows = "".join(
        f"<tr><td>{c.title()}</td><td>{s['country']}</td>"
        f"<td>{s['resolution']}</td><td>{s['placed']:,}</td>"
        f"<td>{s['placed_pct']}%</td></tr>"
        for c, s in inventory.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>EPC / APE geolocation — all cities</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
  #map{{position:absolute;inset:0}}
  .panel{{position:absolute;top:10px;right:10px;z-index:1000;background:#fff;
    border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);padding:10px 12px;
    max-height:90vh;overflow:auto;font-size:12px;max-width:320px}}
  .panel h3{{margin:.2em 0 .5em;font-size:13px}}
  .panel table{{border-collapse:collapse;width:100%}}
  .panel td,.panel th{{padding:2px 5px;text-align:left;border-bottom:1px solid #eee}}
  .panel th{{font-weight:600}}
  .legend span{{display:inline-block;width:12px;height:12px;border-radius:2px;
    margin-right:4px;vertical-align:middle}}
  .cities label{{display:block;cursor:pointer;margin:1px 0}}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h3>EPC / APE geolocation</h3>
  <div class="cities" id="cities"></div>
  <p style="margin:.6em 0 .2em"><b>Energy band</b></p>
  <div class="legend" id="legend"></div>
  <p style="margin:.8em 0 .2em"><b>Coverage</b> (sampled, cap {CAP:,}/city)</p>
  <table>
    <tr><th>City</th><th>C.</th><th>Resolution</th><th>Placed</th><th>%</th></tr>
    {rows}
  </table>
  <p style="color:#777;margin:.6em 0 0">Point precision varies by city: building
  level where coordinates exist, else postcode or municipality centroid.</p>
</div>
<script>
const DATA = {data_json};
const COLORS = {colors_json};
const UNKNOWN = "{UNKNOWN_COLOR}";
const map = L.map('map', {{preferCanvas:true}}).setView([48.5, 6], 5);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom:19, attribution:'&copy; OpenStreetMap'}}).addTo(map);

const layers = {{}};
const bounds = [];
for (const [city, d] of Object.entries(DATA)) {{
  const grp = L.layerGroup();
  for (const f of d.features) {{
    const [lon, lat] = f.geometry.coordinates;
    const b = f.properties.band;
    L.circleMarker([lat, lon], {{
      radius: 3, weight: 0, fillOpacity: 0.6,
      fillColor: COLORS[b] || UNKNOWN
    }}).bindPopup(
      `<b>${{city}}</b><br>band: ${{b||'?'}}<br>` +
      `${{f.properties.label?f.properties.label+'<br>':''}}` +
      `res: ${{f.properties.resolution}}`
    ).addTo(grp);
    bounds.push([lat, lon]);
  }}
  grp.addTo(map);
  layers[city] = grp;
}}
if (bounds.length) map.fitBounds(bounds, {{padding:[30,30]}});

const citiesDiv = document.getElementById('cities');
for (const city of Object.keys(DATA)) {{
  const id = 'ck_'+city;
  const lbl = document.createElement('label');
  lbl.innerHTML = `<input type="checkbox" id="${{id}}" checked> ${{city}} `+
    `<span style="color:#888">(${{DATA[city].stats.placed.toLocaleString()}})</span>`;
  citiesDiv.appendChild(lbl);
  lbl.querySelector('input').addEventListener('change', e => {{
    if (e.target.checked) layers[city].addTo(map); else map.removeLayer(layers[city]);
  }});
}}

const legend = document.getElementById('legend');
for (const [k,v] of Object.entries(COLORS))
  legend.innerHTML += `<span style="background:${{v}}"></span>${{k}} &nbsp;`;
legend.innerHTML += `<span style="background:${{UNKNOWN}}"></span>n/a`;
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(cities: Optional[list[str]] = None) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    geocoder = AdminGeocoder()
    targets = cities or list(CITY_CONFIG)

    city_layers: dict[str, dict] = {}
    inventory: dict[str, dict] = {}

    for city in targets:
        cfg = CITY_CONFIG[city]
        log(f"\n[{city}] {cfg['country']} / {cfg['strategy']}")
        result = build_city(city, cfg, geocoder)
        city_layers[city] = result
        inventory[city] = result["stats"]

        geojson = {"type": "FeatureCollection",
                   "name": f"epc_points_{city}",
                   "features": result["features"]}
        with open(os.path.join(OUT_DIR, f"{city}.geojson"), "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False)

    with open(os.path.join(OUT_DIR, "geo_inventory.json"), "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=2)

    html = build_html(city_layers, inventory)
    with open(os.path.join(OUT_DIR, "cities_map.html"), "w", encoding="utf-8") as f:
        f.write(html)

    total = sum(s["placed"] for s in inventory.values())
    log(f"\nDone. {total:,} points across {len(inventory)} cities -> {OUT_DIR}")
    log("Open: data/static_data/geo/cities_map.html")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
