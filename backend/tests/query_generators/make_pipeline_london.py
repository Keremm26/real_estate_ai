"""Generate benchmark_queries_london.json: the curated full-pipeline query set
for London (same schema as benchmark_queries_turin.json, pipeline-v1). Legal
ranges come from city_rules (as the downstream set); landmark coordinates are
what the pipeline's own geocoder returns for each name (Nominatim, London
context). tests/validate_query_files.py re-checks ranges, agent consistency
and feasibility on data/metadata/estates_london.parquet.
    python tests/query_generators/make_pipeline_london.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_rules import (hmo_nat, hmo_nat_unknown, known, lp_range, stratum, th_range,  # noqa: E402
                        th_rooms)

BACKEND = Path(__file__).resolve().parents[2]
OUT = BACKEND / "tests" / "benchmark_queries_london.json"

# name used in the query -> coordinates returned by app.data.loaders.get_coordinates (city london)
LANDMARKS = {
    "king's cross": [51.5324, -0.1230], "university college london": [51.5242, -0.1330],
    "queen mary university of london": [51.5247, -0.0393], "london school of economics": [51.5146, -0.1165],
    "imperial college london": [51.4990, -0.1756], "king's college london": [51.5031, -0.0900],
    "canary wharf": [51.5049, -0.0190], "whitechapel": [51.5175, -0.0660], "brixton": [51.4634, -0.1148],
    "camden town": [51.5393, -0.1428], "stratford": [51.5413, -0.0035], "shoreditch": [51.5267, -0.0799],
    "royal london hospital": [51.5175, -0.0599], "greenwich": [51.4821, -0.0045],
    "clapham junction": [51.4645, -0.1705], "hackney central": [51.5471, -0.0569],
}
RES = "Abitazioni adibite a residenza con carattere continuativo"
C_OR_BETTER = ["A", "B", "C"]

items = []


def add(id_, query, *, lang="en", suites=("benchmark",), complexity="medium", typology=None, landmark=None,
        surface=None, energy=None, services=(), use=None, capacity=None, expected=(), optional=(),
        activate=False, family=None, rng=None, user_values=(), basis="", mirrors=None, covers=(), note=""):
    persons = (capacity or {}).get("persons")
    vmin, vmax = rng if rng else (None, None)
    items.append({
        "id": id_, "lang": lang, "query": query, "suites": list(suites), "complexity": complexity,
        "slots": {"typology": typology, "landmark": landmark, "surface": surface, "energy_classes": energy,
                  "services": list(services), "intended_use": use, "capacity": capacity},
        "expected_agents": list(expected), "optional_agents": list(optional),
        "regulatory": {"activate": activate,
                       "target_column": "surface_area" if activate is not False else None,
                       "operator": ">=" if activate is not False else None,
                       "value_min": vmin, "value_max": vmax, "user_values": list(user_values), "basis": basis},
        "rule_family": family if activate is not False else None,
        "heuristic_stratum": stratum(vmin, persons) if activate is True else None,
        "mirrors": mirrors, "covers": list(covers), "note": note,
    })


def singles(n):
    return {"persons": n, "single_rooms": n, "double_rooms": 0}


B, S = ("benchmark",), ("benchmark", "sensitivity")

# ------------------------------------------------------------ HMO, national (law at or below the heuristic)
add("PQ-L01", "Find a house in London to convert into a licensed HMO for 6 tenants.", suites=S, complexity="simple",
    use="hmo", capacity={"persons": 6}, expected=["regulatory"], optional=["building"], activate=True,
    family="hmo_national", rng=hmo_nat_unknown(6), basis="SI 2018/616: N=6, mix not stated",
    mirrors="DQ-L01", covers=["regulatory-only", "hmo"], note="The regulatory threshold is the only filter.")
add("PQ-L02", "Find a property near King's Cross for a shared house for 6 university students, each in a single room.",
    suites=S, landmark="king's cross", use="student_hmo", capacity=singles(6), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family="hmo_national", rng=known(hmo_nat(s=6)),
    basis="SI 2018/616: 6 single rooms x 6.51", mirrors="DQ-L05", covers=["student", "hmo", "location"])
add("PQ-L03", "HMO for 8 tenants in single rooms near Brixton, close to public transport.", complexity="complex",
    landmark="brixton", services=["mobility"], use="hmo", capacity=singles(8),
    expected=["location", "proximity", "regulatory"], optional=["building"], activate=True, family="hmo_national",
    rng=known(hmo_nat(s=8)), basis="8 x 6.51", covers=["hmo", "mobility", "location"])
add("PQ-L04", "House share for 7 people with 3 single rooms and 2 double rooms near Clapham Junction, EPC rating C "
    "or better.", suites=S, complexity="complex", landmark="clapham junction", energy=C_OR_BETTER, use="hmo",
    capacity={"persons": 7, "single_rooms": 3, "double_rooms": 2}, expected=["location", "energy", "regulatory"],
    optional=["building"], activate=True, family="hmo_national", rng=known(hmo_nat(s=3, d=2)),
    basis="3 x 6.51 + 2 x 10.22", mirrors="DQ-L04", covers=["hmo", "mixed-rooms", "energy"])
add("PQ-L05", "Student house near Imperial College London for 6 students in three double bedrooms.",
    landmark="imperial college london", use="student_hmo", capacity={"persons": 6, "single_rooms": 0, "double_rooms": 3},
    expected=["location", "regulatory"], optional=["building", "proximity"], activate=True, family="hmo_national",
    rng=known(hmo_nat(d=3)), basis="3 double rooms x 10.22", mirrors="DQ-L06", covers=["student", "double-room-rate"])
add("PQ-L06", "Hi! My brother and I are starting a small letting business. We'd like to buy a terraced house near "
    "University College London and rent it to 6 students. Budget is tight, we care about transport links and a "
    "garden would be nice. What should we look for?", suites=S, complexity="complex",
    landmark="university college london", services=["mobility"], typology=RES, use="student_hmo",
    capacity={"persons": 6}, expected=["location", "proximity", "regulatory"], optional=["building"], activate=True,
    family="hmo_national", rng=hmo_nat_unknown(6), basis="N=6, mix not stated (Camden: national rule)",
    mirrors="DQ-L07", covers=["long-query", "student", "location-noise"])
add("PQ-L07", "An HMO for ten tenants near Hackney Central, all in single rooms.", landmark="hackney central",
    use="hmo", capacity=singles(10), expected=["location", "regulatory"], optional=["building"], activate=True,
    family="hmo_national", rng=known(hmo_nat(s=10)), basis="10 x 6.51", covers=["number-in-words", "hmo"])
add("PQ-L08", "Lisenced HMO for 5 tenents near Stratford.", landmark="stratford", use="hmo",
    capacity={"persons": 5}, expected=["location", "regulatory"], optional=["building"], activate=True,
    family="hmo_national", rng=hmo_nat_unknown(5), basis="N=5, mix not stated", mirrors="DQ-L08", covers=["typos"])
add("PQ-L09", "HMO for 6 tenants near Camden Town... actually make that 8, all in single rooms.", suites=S,
    landmark="camden town", use="hmo", capacity=singles(8), expected=["location", "regulatory"], optional=["building"],
    activate=True, family="hmo_national", rng=known(hmo_nat(s=8)), user_values=[6],
    basis="Corrected capacity: 8 x 6.51", mirrors="DQ-L10", covers=["self-correction"])
add("PQ-L10", "HMO near Shoreditch for 6 tenants in single bedrooms; I'd like every bedroom to be at least 15 square "
    "metres.", suites=S, landmark="shoreditch", use="hmo", capacity=singles(6), expected=["location", "regulatory"],
    optional=["building"], activate=True, family="hmo_national", rng=known(hmo_nat(s=6)), user_values=[15, 90],
    basis="6 x 6.51; the user's 15 m2 per room is a preference", mirrors="DQ-L12", covers=["echo-guard"])
add("PQ-L11", "Cerco una casa near University College London per 5 studenti, each one in a single room.", lang="mixed",
    landmark="university college london", typology=RES, use="student_hmo", capacity=singles(5),
    expected=["location", "regulatory"], optional=["building", "proximity"], activate=True, family="hmo_national",
    rng=known(hmo_nat(s=5)), basis="5 x 6.51", mirrors="DQ-L24", covers=["code-switching", "student"])
add("PQ-L12", "A house near Stratford for 5 working tenants, not students, each in a single room.",
    landmark="stratford", typology=RES, use="hmo", capacity=singles(5), expected=["location", "regulatory"],
    optional=["building"], activate=True, family="hmo_national", rng=known(hmo_nat(s=5)), basis="5 x 6.51",
    mirrors="DQ-L11", covers=["negation"])
add("PQ-L13", "HMO for 6 tenants in single rooms near Hackney Central, EPC rating C or better, close to shops and "
    "public transport.", suites=S, complexity="complex", landmark="hackney central", energy=C_OR_BETTER,
    services=["commercial", "mobility"], use="hmo", capacity=singles(6),
    expected=["location", "energy", "proximity", "regulatory"], optional=["building"], activate=True,
    family="hmo_national", rng=known(hmo_nat(s=6)), basis="6 x 6.51", covers=["five-constraints", "hmo"])

# ------------------------------------------------------------ Tower Hamlets (borough standard)
add("PQ-L14", "Licensed HMO near Whitechapel, Tower Hamlets, for 6 tenants in single bedrooms with a shared kitchen.",
    suites=S, landmark="whitechapel", use="hmo_th", capacity=singles(6), expected=["location", "regulatory"],
    optional=["building"], activate=True, family="hmo_th", rng=th_range(s=6),
    basis="TH: 6 x 8.5 (+ kitchen 9.5); national would give 39.06", mirrors="DQ-L13",
    covers=["precedence", "borough-standard"],
    note="The 3 km radius also covers neighbouring boroughs; the expectation follows the stated borough.")
add("PQ-L15", "HMO near Queen Mary University of London in Mile End for 4 people living as two couples in two double "
    "bedrooms.", suites=S, landmark="queen mary university of london", use="hmo_th",
    capacity={"persons": 4, "single_rooms": 0, "double_rooms": 2}, expected=["location", "regulatory"],
    optional=["building"], activate=True, family="hmo_th", rng=th_range(d=2),
    basis="TH (Mile End is in Tower Hamlets): 2 x 13 (+ kitchen 7.5)", mirrors="DQ-L14",
    covers=["borough-standard", "implicit-borough"], note="The borough is implied by the place, not named.")
add("PQ-L16", "Five bedsits near the Royal London Hospital in Tower Hamlets, each with its own kitchen facilities "
    "inside the room, one person per bedsit.", suites=S, landmark="royal london hospital", use="bedsit_th", capacity=singles(5),
    expected=["location", "regulatory"], optional=["building"], activate=True, family="hmo_th_bedsit",
    rng=known(th_rooms(s=5, bedsit=True)), basis="TH bedsits with kitchen within the room: 5 x 13",
    mirrors="DQ-L15", covers=["bedsit", "borough-standard"])

# ------------------------------------------------------------ London Plan Table 3.1 (law above the heuristic)
add("PQ-L17", "Find a property near Canary Wharf to convert into a two-bedroom flat for four people.", suites=S,
    landmark="canary wharf", use="dwelling", capacity={"persons": 4, "bedrooms": 2}, expected=["location", "regulatory"],
    optional=["building"], activate=True, family="lp_dwelling", rng=lp_range(2, 4), basis="Table 3.1 2b4p 70..79",
    mirrors="DQ-L17", covers=["dwelling", "space-standard"])
add("PQ-L18", "One-bedroom flat for a couple near the London School of Economics, EPC rating B or C.",
    complexity="complex", landmark="london school of economics", energy=["B", "C"], typology=RES, use="dwelling",
    capacity={"persons": 2, "bedrooms": 1}, expected=["location", "energy", "regulatory"], optional=["building"],
    activate=True, family="lp_dwelling", rng=lp_range(1, 2), basis="Table 3.1 1b2p 50..58", mirrors="DQ-L18",
    covers=["dwelling", "energy"])
add("PQ-L19", "Studio flat for one person near King's College London, with a shower room instead of a bathroom.",
    suites=S, landmark="king's college london", typology=RES, use="studio",
    capacity={"persons": 1, "bedrooms": 1, "shower_room": True}, expected=["location", "regulatory"],
    optional=["building"], activate=True, family="lp_dwelling", rng=lp_range(1, 1, shower_studio=True),
    basis="Table 3.1 1b1p 39, 37 with a shower room", mirrors="DQ-L19", covers=["studio"])
add("PQ-L20", "Three-bedroom family home for five people near Greenwich, close to schools.", suites=S, complexity="complex",
    landmark="greenwich", services=["education"], typology=RES, use="dwelling", capacity={"persons": 5, "bedrooms": 3},
    expected=["location", "proximity", "regulatory"], optional=["building"], activate=True, family="lp_dwelling",
    rng=lp_range(3, 5), basis="Table 3.1 3b5p 86 / 93 / 99", mirrors="DQ-L20", covers=["family", "education"])
add("PQ-L21", "Two-storey house with four bedrooms for six people near Clapham Junction.", landmark="clapham junction",
    typology=RES, use="dwelling", capacity={"persons": 6, "bedrooms": 4, "storeys": 2},
    expected=["location", "regulatory"], optional=["building"], activate=True, family="lp_dwelling",
    rng=lp_range(4, 6, storeys=2), basis="Table 3.1 4b6p, 2 storeys: 106", mirrors="DQ-L21", covers=["storeys-stated"])
add("PQ-L22", "Find a building near Shoreditch to convert into flats with two bedrooms for three people.", suites=S,
    landmark="shoreditch", use="dwelling", capacity={"persons": 3, "bedrooms": 2}, expected=["location", "regulatory"],
    optional=["building"], activate=True, family="lp_dwelling", rng=lp_range(2, 3), basis="Table 3.1 2b3p 61..70 per unit",
    mirrors="DQ-L22", covers=["conversion", "per-unit"])
add("PQ-L23", "Two-bedroom flat for four people near Brixton, at least 100 square metres.", suites=S,
    landmark="brixton", surface={"min": 100, "max": None}, typology=RES, use="dwelling",
    capacity={"persons": 4, "bedrooms": 2}, expected=["location", "building", "regulatory"], activate=True,
    family="lp_dwelling", rng=lp_range(2, 4), user_values=[100], basis="Table 3.1 2b4p; the user's 100 m2 is a preference",
    mirrors="DQ-L23", covers=["echo-guard", "user-bound-above-regulation"])
add("PQ-L24", "Two-bedroom flat for four people near Camden Town, between 50 and 60 square metres.",
    landmark="camden town", surface={"min": 50, "max": 60}, typology=RES, use="dwelling",
    capacity={"persons": 4, "bedrooms": 2}, expected=["location", "building", "regulatory"], activate=True,
    family="lp_dwelling", rng=lp_range(2, 4), user_values=[50, 60],
    basis="Table 3.1 2b4p 70..79: the user's bounds are below the space standard",
    covers=["conflict-user-vs-regulation"], note="No result can satisfy both: tests how the pipeline resolves it.")

# ------------------------------------------------------------ either
add("PQ-L25", "Purpose-built student accommodation block for 200 students near Stratford.", suites=S, landmark="stratford",
    use="pbsa", capacity={"persons": 200}, expected=["location"], optional=["building", "proximity", "regulatory"],
    activate="either", family="hmo_national", rng=hmo_nat_unknown(200),
    basis="No PBSA space standard in the corpus; if the HMO minima are applied", mirrors="DQ-L25", covers=["pbsa"])
add("PQ-L26", "A house share for 4 young professionals near Canary Wharf.", landmark="canary wharf", use="house_share",
    capacity={"persons": 4}, expected=["location"], optional=["building", "regulatory"], activate="either",
    family="hmo_national", rng=hmo_nat_unknown(4), basis="Below the mandatory-licensing size", mirrors="DQ-L26",
    covers=["licensing-threshold"])
add("PQ-L27", "Supported shared housing for 6 elderly residents near the Royal London Hospital, close to healthcare "
    "services.", landmark="royal london hospital", services=["healthcare"], use="supported_housing",
    capacity={"persons": 6}, expected=["location", "proximity"], optional=["building", "regulatory"], activate="either",
    family="hmo_national", rng=hmo_nat_unknown(6), basis="No senior standard; if treated as an HMO", mirrors="DQ-L27",
    covers=["senior", "healthcare"])

# ------------------------------------------------------------ no rule applies
add("PQ-L28", "Flat near King's Cross with an EPC rating of B or better.", suites=S, complexity="simple", landmark="king's cross",
    energy=["A", "B"], typology=RES, expected=["location", "energy"], optional=["building"],
    basis="Energy preference only", covers=["energy", "no-rule"])
add("PQ-L29", "House of at least 120 square metres near Greenwich.", suites=S, complexity="simple",
    landmark="greenwich", surface={"min": 120, "max": None}, typology=RES, expected=["location", "building"],
    user_values=[120], basis="User surface, no regulation", covers=["echo-without-capacity", "no-rule"])
add("PQ-L30", "Property near Brixton close to parks and public transport.", suites=S, landmark="brixton",
    services=["green", "mobility"], expected=["location", "proximity"], basis="Services only",
    covers=["proximity", "no-rule"])
add("PQ-L31", "A hotel with 40 rooms near King's Cross.", landmark="king's cross", use="hotel",
    capacity={"rooms": 40}, expected=["location"], optional=["building"], basis="No hotel standard in the corpus",
    covers=["out-of-corpus"])
add("PQ-L32", "Office of at least 300 square metres in Shoreditch.", landmark="shoreditch",
    surface={"min": 300, "max": None}, use="office", expected=["location", "building"], user_values=[300],
    basis="Office use: no regulatory area", covers=["office", "echo-guard"],
    note="The London dataset is domestic only: the building agent finds no office typology.")
add("PQ-L33", "Find a house near Camden Town to turn into an HMO.", suites=S, landmark="camden town", typology=RES,
    use="hmo", expected=["location"], optional=["building"], basis="No capacity: no building-level threshold",
    covers=["no-capacity"])
add("PQ-L34", "Flat near Whitechapel with an EPC rating of A or B, close to schools.", complexity="complex",
    landmark="whitechapel", energy=["A", "B"], services=["education"], typology=RES,
    expected=["location", "energy", "proximity"], optional=["building"], basis="No rule", covers=["energy", "education"])
add("PQ-L35", "Student accommodation in Tower Hamlets with wheelchair-accessible rooms near Queen Mary University of "
    "London.", landmark="queen mary university of london", use="pbsa", expected=["location"],
    optional=["building", "proximity"], basis="5% wheelchair-accessible rooms: a share, not an area",
    covers=["non-surface", "accessibility"])
add("PQ-L36", "Converting a shop to residential near Whitechapel under permitted development.", landmark="whitechapel",
    expected=["location"], optional=["building"], basis="Article 4 / Class MA: procedural", covers=["procedural"])
add("PQ-L37", "A day nursery for 30 children near Hackney Central.", landmark="hackney central", use="nursery",
    capacity={"persons": 30}, expected=["location"], optional=["building", "proximity"],
    basis="No nursery standard in the corpus", covers=["out-of-corpus", "childcare"])
add("PQ-L38", "Flat near Canary Wharf built after 2000 with an EPC rating of B.", complexity="complex",
    landmark="canary wharf", energy=["B"], typology=RES, expected=["location", "energy", "building"],
    basis="Construction period and energy only", covers=["construction-period", "energy"])
add("PQ-L39", "Home near Greenwich close to green spaces and schools, EPC rating C or better.", complexity="complex",
    landmark="greenwich", energy=C_OR_BETTER, services=["green", "education"], typology=RES,
    expected=["location", "energy", "proximity"], optional=["building"], basis="No rule", covers=["four-agents"])
add("PQ-L40", "Large house near Clapham Junction close to sports facilities.", landmark="clapham junction",
    services=["sport"], typology=RES, expected=["location", "proximity"], optional=["building"],
    basis="'Large' is relative (75th percentile), no regulation", covers=["relative-size", "sport"])

meta = {
    "description": "Hand-curated full-pipeline benchmark for MURENA (London). Same design as benchmark_queries_turin.json: "
                   "each query runs end to end through all agents; ground truth is the expected agent activation and "
                   "the regulatory expectation.",
    "schema": "pipeline-v1 (see benchmark_queries_turin.json). id PQ-Lnn. slots.landmark = key of meta.landmarks "
              "(coordinates the pipeline's geocoder returns for that name in the London context). rule_family / "
              "heuristic_stratum as in downstream_queries_london.json; regulatory ranges follow its value_basis.",
    "country": "UK",
    "dataset": "data/metadata/estates_london.parquet (domestic EPCs only: one residential typology)",
    "landmarks": LANDMARKS,
    "composition": "",
    "caveats": [
        "Author-constructed; regulatory ranges follow downstream_queries_london.json value_basis.",
        "Feasibility (non-empty results) is checked on the dataset with the slots as SQL-like filters within 3 km.",
        "Tower Hamlets items: the 3 km radius also covers neighbouring boroughs, where only the national minimum "
        "applies; the expectation follows the borough the user names.",
        "The London dataset holds domestic certificates only, so non-residential requests (office, hotel, nursery) "
        "have no matching typology.",
    ],
}
n_rule = sum(i["regulatory"]["activate"] is True for i in items)
n_either = sum(i["regulatory"]["activate"] == "either" for i in items)
meta["composition"] = (f"{len(items)} queries: {n_rule} with an applicable rule, {n_either} 'either', "
                       f"{len(items) - n_rule - n_either} without a rule. Strata on rule queries: "
                       f"{dict(Counter(i['heuristic_stratum'] for i in items if i['heuristic_stratum']))}.")
OUT.write_text(json.dumps({"meta": meta, "queries": items}, indent=2, ensure_ascii=False) + "\n")
print(meta["composition"])
print("sensitivity:", sum("sensitivity" in i["suites"] for i in items), "| langs:", dict(Counter(i["lang"] for i in items)),
      "| complexity:", dict(Counter(i["complexity"] for i in items)))
