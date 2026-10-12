"""Build the updated cross-country mapping workbook from the latest pipeline results.

Concept-anchored (bottom-up vocab v1.1) with a TIMEPAC cross-reference column,
per-city Column(s)/Match/Notes triples, and inline example values pulled from the
redacted profiles (no raw PII rows). Mirrors the layout of the original
epc_cross_country_mapping.xlsx reference file.

Run:  cd backend && python3 -m app.services.schema_alignment.build_excel
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BACKEND = Path(__file__).resolve().parents[3]
SCHEMA = BACKEND / "data" / "epc_schema"
PROFILES = BACKEND / "data" / "epc_profiles"
OUT = Path.home() / "Desktop" / "epc_cross_country_mapping_updated.xlsx"

# City display order (alphabetical, matches existing matrix artifacts) + country.
CITIES = [
    ("Amsterdam", "Netherlands"),
    ("Barcelona", "Spain (Catalonia)"),
    ("Copenhagen", "Denmark"),
    ("Dublin", "Ireland"),
    ("Liège", "Belgium"),
    ("Lisbon", "Portugal"),
    ("London", "United Kingdom"),
    ("Madrid", "Spain"),
    ("Paris", "France"),
    ("Turin", "Italy"),
]


def city_key(city: str) -> str:
    return city.lower().replace("è", "e")


# ---------------------------------------------------------------------------
# concept_id -> (TIMEPAC requirement, Italian APE XPath). Blank where the
# bottom-up schema has no TIMEPAC counterpart (itself a finding).
# ---------------------------------------------------------------------------
XPATH = {
    "Assessed object": "//ape:datiGenerali/ape:oggettoAttestato",
    "Application type": "",
    "Adopted simulation software": "",
    "EPC ID code": "//ape:datiAttestato/ape:codiceIdentificativo",
    "Geographical location": "",
    "Building address": "//ape:datiGenerali/ape:indirizzo",
    "Cadastre informations": "//ape:datiGenerali/ape:datiIdentificativi/ape:catasto",
    "Building typology": "",
    "Building use": "//ape:datiGenerali/ape:destinazioneUso",
    "Year of construction": "",
    "Climatic region": "//ape:datiGenerali/ape:zonaClimatica",
    "Thermally conditioned floor area": "//ape:datiGenerali/ape:datiIdentificativi/ape:superficieUtileRiscaldata",
    "Thermally conditioned gross volume": "",
    "Compactness ratio": "",
    "Opaque thermal envelope area": "//ape:fabbricato/ape:altriDatiSintetici/ape:superficieOpacaTotale",
    "Transparent thermal envelope area": "//ape:fabbricato/ape:altriDatiSintetici/ape:superficieVetrataTotale",
    "Thermal transmittance per building envelope component": "",
    "Energy services": "//ape:datiGenerali/ape:serviziEnergeticiPresenti",
    "TBS type of generator per energy service": "//ape:datiImpianti/*/ape:impianto/ape:tipoImpianto",
    "TBS energy carrier per energy service": "//ape:datiImpianti/*/ape:impianto/ape:vettoriEnergeticiUtilizzati",
    "TBS mean global seasonal efficiency per energy service": "",
    "Energy need for space heating": "//ape:datiFabbricato/ape:ephnd",
    "Energy need for space cooling": "//ape:datiFabbricato/ape:epcnd",
    "Energy need for domestic hot water": "//ape:impianti/ape:impianto/ape:servizio/ape:acs/ape:fabbisogno/ape:valoreAnnuale",
    "Overall non-renewable energy performance indicator": "//ape:prestazioneGlobale/ape:prestazioneEnergeticaGlobale/ape:classificazione/ape:epglnren",
    "Delivered energy per energy carrier": "//ape:prestazioneImpianti/*[ape:consumoAnnuo]",
    "CO2 emissions": "//ape:prestazioneImpianti/ape:emissioniCO2",
    "Energy efficiency rating/class": "//ape:prestazioneGlobale/ape:prestazioneEnergeticaGlobale/ape:classificazione/ape:classeEnergetica",
    "Overall renewable energy performance indicator": "",
    "Ventilation airflow rate": "//ape:ventilazione/ape:portataAria",
}

CONCEPT_TIMEPAC = {
    "construction_year": "Year of construction",
    "construction_period": "Year of construction",
    "building_type": "Building typology",
    "certificate_context": "Application type",
    "certification_method": "Adopted simulation software",
    "postal_code": "Geographical location",
    "street_name": "Building address",
    "house_number": "Building address",
    "municipality": "Geographical location",
    "climate_zone": "Climatic region",
    "longitude": "Geographical location",
    "latitude": "Geographical location",
    "projected_x_coordinate": "Geographical location",
    "projected_y_coordinate": "Geographical location",
    "floor_area": "Thermally conditioned floor area",
    "floor_height": "Thermally conditioned gross volume",
    "wall_area": "Opaque thermal envelope area",
    "roof_area": "Opaque thermal envelope area",
    "floor_element_area": "Opaque thermal envelope area",
    "door_area": "Opaque thermal envelope area",
    "window_area": "Transparent thermal envelope area",
    "wall_u_value": "Thermal transmittance per building envelope component",
    "roof_u_value": "Thermal transmittance per building envelope component",
    "floor_u_value": "Thermal transmittance per building envelope component",
    "door_u_value": "Thermal transmittance per building envelope component",
    "window_u_value": "Thermal transmittance per building envelope component",
    "building_compactness": "Compactness ratio",
    "energy_class": "Energy efficiency rating/class",
    "energy_efficiency_score": "Energy efficiency rating/class",
    "primary_energy_intensity": "Overall non-renewable energy performance indicator",
    "primary_energy_total": "Overall non-renewable energy performance indicator",
    "final_energy_intensity": "Delivered energy per energy carrier",
    "final_energy_total": "Delivered energy per energy carrier",
    "delivered_energy_total": "Delivered energy per energy carrier",
    "space_heating_demand_intensity": "Energy need for space heating",
    "space_heating_demand_total": "Energy need for space heating",
    "cooling_demand_intensity": "Energy need for space cooling",
    "cooling_demand_total": "Energy need for space cooling",
    "domestic_hot_water_demand_total": "Energy need for domestic hot water",
    "space_heating_final_energy_total": "Delivered energy per energy carrier",
    "domestic_hot_water_final_energy_total": "Delivered energy per energy carrier",
    "cooling_final_energy_total": "Delivered energy per energy carrier",
    "lighting_final_energy_total": "Delivered energy per energy carrier",
    "auxiliary_final_energy_total": "Delivered energy per energy carrier",
    "co2_emissions_intensity": "CO2 emissions",
    "co2_emissions_total": "CO2 emissions",
    "space_heating_co2_total": "CO2 emissions",
    "domestic_hot_water_co2_total": "CO2 emissions",
    "cooling_co2_total": "CO2 emissions",
    "lighting_co2_total": "CO2 emissions",
    "auxiliary_co2_total": "CO2 emissions",
    "renewable_energy_share": "Overall renewable energy performance indicator",
    "pv_electricity_production_total": "Overall renewable energy performance indicator",
    "main_heating_fuel": "TBS energy carrier per energy service",
    "main_hot_water_fuel": "TBS energy carrier per energy service",
    "heating_system_type": "TBS type of generator per energy service",
    "hot_water_system_type": "TBS type of generator per energy service",
    "cooling_system_type": "TBS type of generator per energy service",
    "heating_installation_scope": "Energy services",
    "hot_water_installation_scope": "Energy services",
    "solar_hot_water_present": "Energy services",
    "solar_pv_present": "Energy services",
    "heating_system_efficiency": "TBS mean global seasonal efficiency per energy service",
    "hot_water_system_efficiency": "TBS mean global seasonal efficiency per energy service",
    "ventilation_type": "Ventilation airflow rate",
}

GROUP_LABELS = {
    "temporal": "Temporal / certificate lifecycle",
    "building_identity": "Building identity",
    "location": "Location & geography",
    "geometry": "Geometry",
    "envelope": "Envelope (areas & U-values)",
    "rating": "Rating / class",
    "energy": "Energy (demand / final / delivered)",
    "emissions": "Emissions (CO2)",
    "systems": "Technical building systems",
    "other": "Other (costs, context)",
}
GROUP_ORDER = ["building_identity", "location", "geometry", "envelope", "rating",
               "energy", "emissions", "systems", "temporal", "other"]

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
vocab = json.loads((SCHEMA / "concept_vocabulary.v1.1.json").read_text())
reviewed = json.loads((SCHEMA / "reviewed_mappings.json").read_text())
concepts = {c["id"]: c for c in vocab["concepts"]}
reviews = {r["concept_id"]: r for r in reviewed["reviews"]}

profiles: dict[str, dict] = {}
for city, _ in CITIES:
    p = json.loads((PROFILES / f"{city_key(city)}.json").read_text())
    lut = {}
    for col in p["columns"]:
        lut[col["name"]] = col
        lut.setdefault(col.get("original_name", col["name"]), col)
    profiles[city] = lut


def fmt_num(x) -> str:
    if x is None:
        return ""
    if abs(x) >= 1000 or (x == int(x)):
        return f"{x:,.0f}"
    return f"{x:.2f}"


def example_for(city: str, cols: list[str]) -> str:
    """Short inline example value(s) from the redacted profile."""
    lut = profiles.get(city, {})
    out = []
    for col in cols[:2]:
        c = lut.get(col)
        if not c:
            continue
        ns = c.get("numeric_stats")
        if ns:
            lo, mid, hi = ns.get("p25"), ns.get("p50"), ns.get("max")
            out.append(f"{col}≈{fmt_num(mid)} (p25–max {fmt_num(lo)}–{fmt_num(hi)})")
        elif c.get("top_values"):
            vals = [str(v["value"]) for v in c["top_values"][:2]]
            out.append(f"{col}=" + " | ".join(vals))
    return "; ".join(out)


# ---------------------------------------------------------------------------
# Assemble concept x city cells
# ---------------------------------------------------------------------------
def build_rows():
    rows = []
    per_city_counts = {city: {"Direct": 0, "Partial": 0, "Uncertain": 0, "Missing": 0}
                       for city, _ in CITIES}
    for gid in GROUP_ORDER:
        for cid, c in concepts.items():
            if c["group"] != gid:
                continue
            rv = reviews.get(cid, {})
            cells = {cell["city"]: cell for cell in rv.get("cells", [])}
            mapped_n = sum(1 for cell in cells.values() if cell.get("source_columns"))
            row = {
                "group": gid,
                "concept": c,
                "verdict": rv.get("comparability_verdict", ""),
                "verdict_reason": rv.get("comparability_reason", ""),
                "mapped_n": mapped_n,
                "cities": {},
            }
            for city, _ in CITIES:
                cell = cells.get(city)
                if not cell or not cell.get("source_columns"):
                    row["cities"][city] = {"cols": "", "match": "Missing", "notes": ""}
                    per_city_counts[city]["Missing"] += 1
                    continue
                cols = cell["source_columns"]
                match = (cell.get("reviewed_semantic_match") or "").capitalize() or "Direct"
                if match not in ("Direct", "Partial", "Uncertain"):
                    match = "Partial"
                per_city_counts[city][match] += 1
                note_bits = []
                if cell.get("reason"):
                    note_bits.append(cell["reason"].strip().rstrip("."))
                if cell.get("qualifier"):
                    note_bits.append("Qualifier: " + cell["qualifier"].strip().rstrip("."))
                ex = example_for(city, cols)
                if ex:
                    note_bits.append("e.g. " + ex)
                if cell.get("verdict") in ("downgrade", "flag"):
                    note_bits.append(f"[review: {cell['verdict']}]")
                row["cities"][city] = {
                    "cols": "; ".join(cols),
                    "match": match,
                    "notes": ". ".join(note_bits),
                }
            rows.append(row)
    return rows, per_city_counts


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------
HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
GRP_FILL = PatternFill("solid", fgColor="D9E1F2")
GRP_FONT = Font(bold=True, size=11, color="1F3864")
MATCH_FILL = {
    "Direct": PatternFill("solid", fgColor="C6EFCE"),
    "Partial": PatternFill("solid", fgColor="FFEB9C"),
    "Uncertain": PatternFill("solid", fgColor="FCD5B4"),
    "Missing": PatternFill("solid", fgColor="F2F2F2"),
}
RISK_FILL = {
    "low": PatternFill("solid", fgColor="C6EFCE"),
    "medium": PatternFill("solid", fgColor="FFEB9C"),
    "high": PatternFill("solid", fgColor="FFC7CE"),
}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")


def style_header(ws, row_idx, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=c)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER


# ---------------------------------------------------------------------------
# Build workbook
# ---------------------------------------------------------------------------
def main():
    rows, counts = build_rows()
    wb = Workbook()

    # ---- Summary sheet ----
    ws = wb.active
    ws.title = "Summary"
    ncomp = {"comparable": 0, "conditional": 0, "not_comparable": 0}
    for r in rows:
        ncomp[r["verdict"]] = ncomp.get(r["verdict"], 0) + 1

    title = [
        ["EPC / APE cross-country common-schema mapping — updated results",
         f"vocab {vocab.get('vocab_version', 'v1.1')}", "", "", "", ""],
        ["Bottom-up canonical concepts discovered from 10 city datasets; each city "
         "mapped INTO the schema. TIMEPAC/Italian-APE requirements shown only as a "
         "cross-reference column (not the row template).", "", "", "", "", ""],
        [],
        [f"Concepts: {len(concepts)}", f"Cities: {len(CITIES)}",
         f"Coverage core (≥4 cities): 32", f"Comparable core (≥4 & not high-risk): 18",
         "", ""],
        [f"Comparability verdict — comparable: {ncomp.get('comparable',0)}",
         f"conditional: {ncomp.get('conditional',0)}",
         f"not_comparable: {ncomp.get('not_comparable',0)}",
         f"(cell verdicts: {reviewed['stats']['cell_verdicts']})", "", ""],
        [],
    ]
    for r in title:
        ws.append(r)
    ws["A1"].font = Font(bold=True, size=14, color="1F3864")
    ws["A2"].font = Font(italic=True, size=10, color="595959")
    for r in (4, 5):
        for c in range(1, 5):
            ws.cell(row=r, column=c).font = Font(bold=True, size=10)

    hdr = ["City", "Country", "Format", "Rows", "Raw columns",
           "Mapped concepts", "Direct", "Partial", "Uncertain", "Missing", "Notes"]
    hrow = ws.max_row + 1
    ws.append(hdr)
    style_header(ws, hrow, len(hdr))
    for city, country in CITIES:
        p = json.loads((PROFILES / f"{city_key(city)}.json").read_text())
        cc = counts[city]
        mapped = cc["Direct"] + cc["Partial"] + cc["Uncertain"]
        notes = p.get("notes") or ""
        if isinstance(notes, list):
            notes = "; ".join(str(n) for n in notes)
        src = (p.get("source_file") or "").split("/")[-1] or "CSV"
        ws.append([city, country, src,
                   p.get("n_rows"), p.get("n_cols"), mapped,
                   cc["Direct"], cc["Partial"], cc["Uncertain"], cc["Missing"],
                   str(notes)[:200]])
        for c in range(1, len(hdr) + 1):
            ws.cell(row=ws.max_row, column=c).border = BORDER
            ws.cell(row=ws.max_row, column=c).alignment = TOP
        ws.cell(row=ws.max_row, column=11).alignment = WRAP

    widths = [12, 18, 22, 10, 12, 15, 9, 9, 11, 9, 60]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.append([])
    note = ws.max_row + 1
    ws.cell(row=note, column=1,
            value="Match legend: Direct = semantically equivalent · Partial = related "
                  "but differing scope/detail/unit · Uncertain = mapping not confident · "
                  "Missing = no source column. Example values are drawn from redacted "
                  "profiles (no raw personal/address rows).")
    ws.cell(row=note, column=1).alignment = WRAP
    ws.merge_cells(start_row=note, start_column=1, end_row=note, end_column=11)
    ws.freeze_panes = f"A{hrow + 1}"

    # ---- Cross-country mapping sheet ----
    ws = wb.create_sheet("Cross-country mapping")
    base_hdr = ["Group", "Concept", "Definition", "Canonical unit",
                "Comparability risk", "Comparability verdict", "Cities mapped",
                "TIMEPAC requirement (cross-ref)", "Italian APE XPath"]
    city_hdr = []
    for city, country in CITIES:
        city_hdr += [f"{city} column(s)", f"{city} match", f"{city} notes (with example)"]
    header = base_hdr + city_hdr
    ncols = len(header)

    # two-tier header: city group label row + field row
    ws.append([""] * len(base_hdr) + sum(([c, "", ""] for c, _ in CITIES), []))
    ws.append(header)
    # merge base header cells vertically, city labels horizontally
    for i in range(1, len(base_hdr) + 1):
        ws.merge_cells(start_row=1, start_column=i, end_row=2, end_column=i)
    col = len(base_hdr) + 1
    for city, country in CITIES:
        ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + 2)
        cell = ws.cell(row=1, column=col, value=f"{city} · {country}")
        col += 3
    style_header(ws, 1, ncols)
    style_header(ws, 2, ncols)
    for i in range(1, len(base_hdr) + 1):
        ws.cell(row=1, column=i, value=base_hdr[i - 1])

    r = 3
    cur_group = None
    for row in rows:
        gid = row["group"]
        if gid != cur_group:
            ws.cell(row=r, column=1, value=GROUP_LABELS[gid])
            for c in range(1, ncols + 1):
                ws.cell(row=r, column=c).fill = GRP_FILL
                ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=1).font = GRP_FONT
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
            cur_group = gid
            r += 1

        c = row["concept"]
        tp = CONCEPT_TIMEPAC.get(c["id"], "")
        vals = [
            GROUP_LABELS[gid],
            f"{c['label']}\n({c['id']})",
            c["definition"],
            c.get("canonical_unit", ""),
            c.get("comparability_risk", ""),
            row["verdict"],
            row["mapped_n"],
            tp,
            XPATH.get(tp, ""),
        ]
        for city, _ in CITIES:
            cc = row["cities"][city]
            vals += [cc["cols"], cc["match"], cc["notes"]]
        ws.append(vals)

        # styling for the data row
        for cc_i in range(1, ncols + 1):
            cell = ws.cell(row=r, column=cc_i)
            cell.border = BORDER
            cell.alignment = WRAP if cc_i in (2, 3, 8, 9) or cc_i > len(base_hdr) else TOP
        ws.cell(row=r, column=5).fill = RISK_FILL.get(c.get("comparability_risk"), MATCH_FILL["Missing"])
        # per-city match colouring
        col = len(base_hdr) + 1
        for city, _ in CITIES:
            m = row["cities"][city]["match"]
            ws.cell(row=r, column=col + 1).fill = MATCH_FILL.get(m, MATCH_FILL["Missing"])
            col += 3
        r += 1

    # widths
    base_w = [22, 24, 40, 14, 12, 14, 9, 26, 30]
    for i, w in enumerate(base_w, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    col = len(base_hdr) + 1
    for _ in CITIES:
        ws.column_dimensions[get_column_letter(col)].width = 22
        ws.column_dimensions[get_column_letter(col + 1)].width = 10
        ws.column_dimensions[get_column_letter(col + 2)].width = 48
        col += 3
    ws.freeze_panes = "D3"

    # ---- Common schema core sheet (≥4 cities) ----
    ws = wb.create_sheet("Common schema core")
    ws.append(["Concepts materialised in ≥4 cities — the strict cross-city common "
               "schema. ● mapped · blank = not mapped. Risk/verdict carried from review."])
    ws["A1"].font = Font(italic=True, size=10, color="595959")
    hdr = ["Group", "Concept", "Unit", "Risk", "Verdict", "Cities"] + [c for c, _ in CITIES]
    ws.append(hdr)
    style_header(ws, 2, len(hdr))
    core = [row for row in rows if row["mapped_n"] >= 4]
    core.sort(key=lambda x: (GROUP_ORDER.index(x["group"]), -x["mapped_n"]))
    for row in core:
        c = row["concept"]
        line = [GROUP_LABELS[row["group"]], c["id"], c.get("canonical_unit", ""),
                c.get("comparability_risk", ""), row["verdict"], row["mapped_n"]]
        for city, _ in CITIES:
            cc = row["cities"][city]
            line.append("●" if cc["match"] != "Missing" else "")
        ws.append(line)
        rr = ws.max_row
        for i in range(1, len(hdr) + 1):
            ws.cell(row=rr, column=i).border = BORDER
            ws.cell(row=rr, column=i).alignment = Alignment(
                horizontal="center" if i >= 7 else "left", vertical="top")
        ws.cell(row=rr, column=4).fill = RISK_FILL.get(c.get("comparability_risk"), MATCH_FILL["Missing"])
        for i in range(7, len(hdr) + 1):
            if ws.cell(row=rr, column=i).value == "●":
                ws.cell(row=rr, column=i).fill = MATCH_FILL["Direct"]
    for i, w in enumerate([22, 34, 14, 10, 14, 8] + [11] * len(CITIES), 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "B3"

    wb.save(OUT)
    print(f"Saved {OUT}")
    print(f"Rows: {len(rows)} concepts · core(≥4): {len(core)}")
    print(f"Verdicts: {ncomp}")


if __name__ == "__main__":
    main()
