# City datasets in the MURENA schema (London, Madrid)

Goal: build `estates.parquet`-style datasets for **London** and **Madrid** with exactly the
same columns, names and types as Turin's `backend/data/metadata/estates.parquet`, so the whole
MURENA pipeline (agents → SQL → ranking → map/API) runs on them unchanged.

- **Target schema = Turin's 36 columns** (Italian names, as in the current parquet). Turin is not
  rebuilt.
- **Certificate columns** come from the common-schema output
  (`backend/data/epc_schema/reviewed_mappings.json`, vocabulary v1.1): the mapping says which raw
  column holds each concept. A few small derivations are added here.
- **Non-certificate columns** (coordinates, zone, proximity, building grouping) are computed
  deterministically from open geodata. Coordinates come first; the rest is computed from them.
- **Scope:** Madrid = municipality of Madrid only (34,215 of 69,364 rows). London = the raw file
  as is (31,581 domestic certificates lodged 2026-03-01 … 2026-04-30).

## Build steps (per city)

1. **Load and clean.** Madrid: keep `edif_muni` = Madrid (any case); drop 59 malformed rows
   (dates or typos in `edif_tipo`: `Vivendas` → residential and `Edificio terciario` → tertiary
   are repaired, the shifted rows are dropped); parse decimals and `dd/mm/yyyy` dates.
   London: keep `region` = E12000007 (Greater London).
2. **Certificate columns**, from the reviewed mappings plus the derivations in the tables below.
3. **Coordinates** (see *Location*).
4. **Zone and proximity scores** from the coordinates. As in Turin, rows that fall outside every
   zone of the city are dropped; this also removes failed geocodes.
5. **One row per property unit**: keep the most recent certificate per unit (Turin keeps the most
   recent APE per cadastral unit).
6. **Whole-building rows** (`meta_immobile = True`), built with Turin's aggregation rules: sum of
   surfaces, majority type and energy class by surface, most frequent construction year, maximum
   sub-scores with class score and total recomputed, minimum consumption/emission values, most
   frequent value for the rest.
7. **Percentiles and IDs**: proximity raw scores → 0–100 percentile within the city; IDs `1…N`
   for units and `M00001…` for building rows (Turin's scheme).
8. Write `backend/data/metadata/estates_<city>.parquet` with Turin's column order and dtypes.

## Columns

"Used by" = where the pipeline reads the column (SQL agents, ranking, API/map); "—" = not read by
the code today, kept for schema parity.

### Identity and provenance

| Column | Used by | Turin | London | Madrid |
|---|---|---|---|---|
| `id` | everywhere | `1…N`, `M00001…` | same scheme | same scheme |
| `meta_immobile` | API, service | False / True for building rows | same | same |
| `numero_immobili_per_catasto` | API, map | 1, or units in the building | same | same |
| `id_list` | API, map | unit ids of a building row | same | same |
| `lista_file_ape`, `list_file_ape_filtered`, `list_file_ape_filtered_parsed` | API | APE XML file names, as a list string | `['<certificate_number>']` | `['<edif_codreg>']` (registration code) |

### Cadastral and administrative

| Column | Used by | Turin | London | Madrid |
|---|---|---|---|---|
| `codice_comune` | SQL metadata | `L219` (one value) | borough code (E09…; London's boroughs are its municipalities) of the ward the point lies in, so borough and `zona_omi` always agree; the register's `local_authority` differs for 0.2 % of rows (neighbouring boroughs) and is missing for 3 % | `28079` (INE code of Madrid) |
| `foglio` | building agent, service | cadastral sheet (int) | `0` (no cadastre) | `edif_refcat` chars 8–14 (map sheet, e.g. `VK4659F`) → stored as an integer index of the sheet code so the dtype stays int; the code ↔ index table is saved next to the dataset |
| `particella` | building agent, map | cadastral parcel (int) | building group id (see *Whole-building rows*) | `edif_refcat` chars 1–7 (block + parcel) |
| `subalterno` | building agent | unit within the parcel (int) | `uprn` (unique address number, 93 %), else 0 | `edif_refcat` chars 15–18 (unit) |
| `zona_omi` | API, map (display only) | OMI market zone (47) | electoral ward (ONS boundaries) | barrio (131, Madrid open data) |

`zona_omi` is only displayed, so a neighbourhood-level official area is enough. Wards and barrios
are administrative areas, not price-based market zones; this is stated in the thesis.

### Location

| Column | Used by | Turin | London | Madrid |
|---|---|---|---|---|
| `latitudine`, `longitudine` | SQL, ranking, map | from the APE, improved with OSM geocoding | `uprn` → **OS Open UPRN** point (building level, 93 %); otherwise full-postcode centroid (a UK postcode covers ~15 addresses) | `edif_refcat`[:14] → **Catastro INSPIRE parcel** centroid (building level; ≥14 chars in 99.9 %); otherwise address geocoding on the OSM extract (Turin's geocoder), then postcode centroid |
| `indirizzo` | API, service | street name | street part of `address1`/`address2` (best effort) | street part of `edif_direc` (best effort) |
| `numero_civico` | API | house number (87 %) | number parsed from the address (best effort) | number parsed from `edif_direc` (best effort) |

### Building

| Column | Used by | Turin | London | Madrid |
|---|---|---|---|---|
| `superficie_di_riferimento_mq` | SQL, ranking, regulatory | reference floor area m² | `total_floor_area` (total usable floor area) | `edif_superf` (useful habitable area) |
| `tipologia_bene_immobile` | SQL, building agent | 14 Italian categories | crosswalk below | crosswalk below |
| `epoca_costruzione` | SQL (TRY_CAST), scoring | year as text | single years kept; ranges → middle year (see below) | `edif_año` (year) |
| `data_decorrenza` | API (display) | certificate date | `lodgement_date` | `edif_fecha` |

Floor-area definitions differ slightly between countries (common-schema qualifier); values stay in
m² and are not converted.

### Energy (certificate values)

| Column | Used by | Turin | London | Madrid |
|---|---|---|---|---|
| `classe_energetica_ape` | SQL, ranking, map | Italian class (A1–A4, B–G) | `current_energy_rating` (A–G) | `cal_norenovglobal` (A–G, rating on non-renewable primary energy, the same basis as the Italian class) |
| `epglnren_ape` | prompt (energy) | non-renewable primary energy, kWh/m²·yr | `energy_consumption_current` (primary energy, kWh/m²·yr; the UK figure counts all primary energy, not only non-renewable) | `norenov_global` (non-renewable primary energy, kWh/m²·yr) |
| `epglren_ape` | — | renewable primary energy, kWh/m²·yr | not available → NULL | not available → NULL |
| `emissioni_co2` | — | kgCO₂/m²·yr | `co2_emiss_curr_per_floor_area` | `norenov_co2global` (kgCO₂/m²·yr) |
| `classe_target_ape` | energy agent | class reachable after the recommended measures | `potential_energy_rating` (same meaning) | not available → NULL |

Energy classes are **not comparable across countries** (different methods; a common-schema
finding). Inside one city they rank buildings consistently, which is all the pipeline needs.

### Energy sub-scores (1 / 3 / 5, total 4–20)

Turin derives four sub-scores from the APE (`create_estate_dataset.py`, `map_*_score`). The same
rules are applied to each country's equivalent fields.

| Column | Turin rule | London input | Madrid input |
|---|---|---|---|
| `ape_score_classe` | A* → 5, B–E → 3, F–G → 1 | `current_energy_rating` | `cal_norenovglobal` |
| `ape_score_impianto` | heat pump or district heating → 5; condensing or biomass → 3; else 1 | `mainheat_description` (`heat pump` 1.9 %, `community scheme` 21.5 %; "condensing" never appears) | `calefac_tipo` (`Bomba de Calor` → 5; `Caldera Condensación`, `Biomasa…` vector → 3; `Caldera Estándar`, `Efecto Joule` → 1) |
| `ape_score_involucro` | envelope quality good / neutral / poor → 5 / 3 / 1 | mean of `walls_`, `roof_`, `windows_`, `floor_energy_eff` (Very Good = 5 … Very Poor = 1, available elements only): ≥ 4 good, ≥ 3 neutral, else poor | `cal_calefdem` (heating-demand class): A–B good, C–D neutral, E–G poor |
| `ape_score_rinnovabili` | PV or solar thermal present → 5, else 1 | `photo_supply` > 0, `solar_water_heating_flag` = Y or `wind_turbine_count` > 0 | `elec_energia` > 0 or a `termica_*` contribution > 0 |
| `ape_score_total` | sum of the four | same | same |

### Proximity scores (0–100)

`commerciale`, `educazione`, `mobilita`, `sanita`, `sport`, `verde` — used by the proximity agent,
SQL and ranking.

Turin's method, unchanged: points of interest per category from OpenStreetMap; per building the
sum of `exp(-(d / (S·U))³)` over POIs within 5 km (S = short/medium/long service distance per
amenity type, U = urban multiplier), averaged per category; then the **percentile within the
city**. A score of 50 therefore means "average for this city", which is what makes the scores
comparable across cities. POIs come from Turin's own Overpass query (`pois_query.txt`) with only
the area filter changed — Madrid a circle around the municipality (29 km = farthest corner +
5 km), London the Greater London box + 5 km in four tiles (one query that size is refused) —
and are grouped by Turin's unchanged `pois_download.py`
(`download_city_geodata.py` does both).

## Building-type crosswalk

| Source value | → `tipologia_bene_immobile` |
|---|---|
| London `Flat`, `House`, `Maisonette`, `Bungalow` | Abitazioni adibite a residenza con carattere continuativo |
| Madrid `ViviendaIndividualEnBloque`, `ViviendaUnifamiliar`, `BloqueDeViviendaCompleto` (and the typo `Vivendas`) | Abitazioni adibite a residenza con carattere continuativo |
| Madrid `LocalUsoTerciario` (tertiary premises in a building, 2,267) | Attività commerciali e assimilabili |
| Madrid `EdificioUsoTerciario` (whole tertiary building, and `Edificio terciario`) | Uffici e assimilabili |

The London file is domestic only, so London has no non-residential rows. Spanish "terciario"
covers both shops and offices and nothing in the data separates them; the split above is a
heuristic (premises are mostly shops, whole tertiary buildings mostly offices).

## Construction year (London)

| Source | → `epoca_costruzione` |
|---|---|
| single year (`2023`, `1930`, …) | that year |
| range `1900-1929` | middle year, `1915` |
| `before 1900` | `1890` |
| `2012 onwards` / `2022 onwards` | `2016` / `2024` |
| old letter codes A–J | their range (A = before 1900, B = 1900–1929, … J = 2003–2006), then as above |

## One row per unit, and whole-building rows

| | London | Madrid |
|---|---|---|
| unit key (keep latest certificate) | `uprn`; without UPRN: normalised address + postcode | `edif_refcat` + normalised address (2,015 references are shared by several flats, so the reference alone would merge different flats) |
| building key | postcode + identical OS Open UPRN point. Checked: in 1,718 address-defined buildings with ≥ 2 certified flats, all flats share one point in 85 % (the rest stay separate rows — no false merges) | `edif_refcat`[:14] (cadastral parcel = building) |
| whole-block certificates | — | `BloqueDeViviendaCompleto` (6,815 rows) are one certificate for the whole building and stay ordinary rows. In the 1,481 parcels that also have flat certificates, no aggregated building row is made, because the block certificate already represents the building |

## Corrections to the common-schema mapping found while building

Building real data from the mapping is a check on the mapping; worth a paragraph in the thesis.

- **Profiler number parsing (root cause, fixed).** Madrid `norenov_global` and
  `norenov_co2global` use dot decimals (`135.44`), but the profiler chose comma style because a
  few rows (0.08 %) contain a comma and `135.44` also *parses* as comma style (→ 13544). The
  profile therefore showed values ×100 (median 19,982 instead of 200 kWh/m²), and from those
  numbers the LLM mapping agent labelled the columns as totals (`primary_energy_total`,
  `co2_emissions_total`). The value-range check did not catch it, because the "total" concepts
  allow large values. Fix: the decimal separator is the one that comes last in most values
  (`profiler.parse_numeric`, test `tests/test_schema_profiler.py`). Re-profiling all 10 cities
  changed exactly these two columns; the executor output changed only for Madrid. The July
  mappings are not re-run: the build uses the two concepts as per-m² values.
- Madrid `edif_acrist` is glazing per orientation, not `ventilation_type` (not used here).
- Turin `data_decorrenza` is the certificate date, not `certificate_valid_until` (not used here).

Values outside the vocabulary's `expected_range` are blanked for floor area (1–50,000 m²) and
construction year (1000–2030): Madrid had 3 and 18 such rows (e.g. year 28037).

## Data to download

| Source | Used for | Licence |
|---|---|---|
| OS Open UPRN (Ordnance Survey) | London coordinates | Open Government Licence |
| ONS postcode centroids (or GeoNames GB, already cached by `build_geolocation.py`) | London fallback coordinates | OGL / CC BY |
| ONS ward boundaries | London `zona_omi`, `codice_comune` fill | OGL |
| Catastro INSPIRE cadastral parcels, Madrid | Madrid coordinates | Catastro open licence |
| Madrid barrios boundaries (datos.madrid.es) | Madrid `zona_omi` | open data, attribution |
| OpenStreetMap via Overpass (Turin's query) | POIs for proximity | ODbL |

All downloads: `python -m app.services.schema_alignment.download_city_geodata --city <city>`
(cached in `data/static_data/geo/_cache/`). Postcode centroids for the fallback come from the
GeoNames files `build_geolocation.py` already caches.

## Notes for the pipeline step

- **All-empty columns.** Madrid has no `epglren_ape` (unused by the code) and no
  `classe_target_ape` (read by the energy agent). The column still exists, so
  `update_runtime_metadata` lists it as filterable and the SQL agent may filter on it and get
  zero rows. When the pipeline is switched to a city, columns that are empty in that city's
  dataset should be left out of what the agents are shown.
- **London class score is almost constant.** Recent UK certificates cluster at B–D (C = 57 %),
  and Turin's rule gives 3 to every class from B to E, so 99 % of London units have
  `ape_score_classe` = 3; `ape_score_total` still varies through the heating and envelope
  scores. This is Turin's rule as designed, worth one line when comparing cities.

## Open choices

1. **London coverage**: the raw file holds only two months of certificates (Mar–Apr 2026). The
   official EPC open data has every certificate since 2008 (free registration), which would give
   a much larger and more representative London dataset.
2. **Madrid tertiary split** (premises → commercial, whole buildings → offices).
3. **Construction ranges → middle year** (London).
4. **Envelope thresholds** (London element mean; Madrid heating-demand class).
5. **Heating score**: Turin's keyword rule as is. In London no description says "condensing", so
   gas boilers score 1, and "community scheme" (21.5 %) counts as district heating → 5.
6. **London building key** (postcode + shared UPRN point): to be validated on a sample.
