"""Generate benchmark_queries_madrid.json: the curated full-pipeline query set
for Madrid (same schema as benchmark_queries_turin.json, pipeline-v1; one
language per query: Spanish, English or mixed). Legal ranges come from
city_rules (as the downstream set); landmark coordinates are what the
pipeline's own geocoder returns for each name (Nominatim, Madrid context).
tests/validate_query_files.py re-checks ranges, agent consistency and
feasibility on data/metadata/estates_madrid.parquet.
    python tests/query_generators/make_pipeline_madrid.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_rules import known, prog, prog_unknown, shared, shared_unknown, stratum  # noqa: E402

BACKEND = Path(__file__).resolve().parents[2]
OUT = BACKEND / "tests" / "benchmark_queries_madrid.json"

# name used in the query -> coordinates returned by app.data.loaders.get_coordinates (city madrid).
# Left out: "Universidad Politécnica de Madrid" (the geocoder places it ~12 km north of the city) and
# "Campus Sur UPM" (not resolved).
LANDMARKS = {
    "ciudad universitaria": [40.4438, -3.7264], "moncloa": [40.4350, -3.7192], "puerta del sol": [40.4169, -3.7039],
    "atocha": [40.4070, -3.6886], "nuevos ministerios": [40.4470, -3.6928], "parque del retiro": [40.4149, -3.6833],
    "plaza de castilla": [40.4663, -3.6897], "malasaña": [40.4267, -3.7042], "lavapiés": [40.4085, -3.7009],
    "hospital universitario la paz": [40.4813, -3.6873],
    "hospital general universitario gregorio marañón": [40.4197, -3.6711],
    "estación de chamartín": [40.4742, -3.6850], "argüelles": [40.4307, -3.7162], "plaza de españa": [40.4234, -3.7109],
}
RES = "Abitazioni adibite a residenza con carattere continuativo"
COMMERCIAL = "Attività commerciali e assimilabili"
OFFICES = "Uffici e assimilabili"
C_OR_BETTER = ["A", "B", "C"]

items = []


def add(id_, query, *, lang="es", suites=("benchmark",), complexity="medium", typology=None, landmark=None,
        surface=None, energy=None, services=(), use=None, capacity=None, expected=(), optional=(),
        activate=False, family=None, rng=None, user_values=(), basis="", mirrors=None, covers=(), note=""):
    cap = capacity or {}
    persons = cap.get("persons") or cap.get("beds")
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


def beds(s=0, d=0):
    return {"beds": s + 2 * d, "single_rooms": s, "double_rooms": d}


def units(s=0, d=0):
    return {"persons": s + 2 * d, "single_units": s, "double_units": d}


B, S = ("benchmark",), ("benchmark", "sensitivity")
COM, CPT, DW = "es_res_comunitaria", "es_res_compartida", "es_dwelling"

# ------------------------------------------------------------ residencia comunitaria (law at or below the heuristic)
add("PQ-M01", "Busca un inmueble para una residencia de estudiantes con 60 habitaciones individuales.", suites=S,
    complexity="simple", use="student_residence", capacity=beds(s=60), expected=["regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(60)),
    basis="PGOUM 7.3.11.2 -> 7.3.4: 29 + 7 x 60", mirrors="DQ-M06", covers=["regulatory-only", "student"])
add("PQ-M02", "Find a building near Ciudad Universitaria for a student residence with 40 single rooms.", lang="en",
    suites=S, landmark="ciudad universitaria", use="student_residence", capacity=beds(s=40),
    expected=["location", "regulatory"], optional=["building", "proximity"], activate=True, family=COM,
    rng=known(prog(40)), basis="29 + 7 x 40", mirrors="DQ-M01", covers=["english", "location", "student"])
add("PQ-M03", "Edificio cerca de Moncloa para una residencia de 50 estudiantes en habitaciones individuales, con "
    "certificado energético C o mejor y bien comunicado.", suites=S, complexity="complex", landmark="moncloa",
    energy=C_OR_BETTER, services=["mobility"], use="student_residence", capacity=beds(s=50),
    expected=["location", "energy", "proximity", "regulatory"], optional=["building"], activate=True, family=COM,
    rng=known(prog(50)), basis="29 + 7 x 50", covers=["four-agents", "energy", "mobility"])
add("PQ-M04", "Residencia de estudiantes con 20 habitaciones dobles y 10 individuales cerca de Argüelles.", suites=S,
    landmark="argüelles", use="student_residence", capacity=beds(s=10, d=20), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(30)),
    basis="30 bedrooms: 29 + 7 x 30", mirrors="DQ-M03", covers=["mixed-rooms"])
add("PQ-M05", "Residence for 60 students near Plaza de España; the room types are not decided yet.", suites=S, lang="en",
    landmark="plaza de españa", use="student_residence", capacity={"beds": 60}, expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=prog_unknown(60),
    basis="60 beds, 1-2 per room: [P(30), 1.25 P(60)]", mirrors="DQ-M02", covers=["mix-unknown"])
add("PQ-M06", "Hola, buenos días. Represento a una fundación educativa y buscamos un edificio cerca de Moncloa para "
    "abrir una residencia para 45 estudiantes de grado, cada uno en su habitación individual. Nos importa el "
    "transporte y tener zonas verdes cerca.", suites=S, complexity="complex", landmark="moncloa",
    services=["mobility", "green"], use="student_residence", capacity=beds(s=45),
    expected=["location", "proximity", "regulatory"], optional=["building"], activate=True, family=COM,
    rng=known(prog(45)), basis="29 + 7 x 45", mirrors="DQ-M08", covers=["long-query", "location-noise"])
add("PQ-M07", "Residencia de estudiantes con sesenta habitaciones individuales cerca de Ciudad Universitaria.",
    landmark="ciudad universitaria", use="student_residence", capacity=beds(s=60), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(60)), basis="29 + 7 x 60",
    mirrors="DQ-M06", covers=["number-in-words"])
add("PQ-M08", "Recidencia de estudiantes con 25 avitaciones individuales serca de Moncloa.", landmark="moncloa",
    use="student_residence", capacity=beds(s=25), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(25)), basis="29 + 7 x 25",
    mirrors="DQ-M07", covers=["typos"])
add("PQ-M09", "Residencia para 30 estudiantes cerca de Malasaña... mejor que sean 40, todos en habitaciones "
    "individuales.", suites=S, landmark="malasaña", use="student_residence", capacity=beds(s=40),
    expected=["location", "regulatory"], optional=["building", "proximity"], activate=True, family=COM,
    rng=known(prog(40)), user_values=[30], basis="Corrected capacity: 29 + 7 x 40", mirrors="DQ-M09",
    covers=["self-correction"])
add("PQ-M10", "Residencia para 50 estudiantes en habitaciones individuales cerca de Nuevos Ministerios; el edificio "
    "debe tener más de 2000 m2.", suites=S, landmark="nuevos ministerios", surface={"min": 2000, "max": None},
    use="student_residence", capacity=beds(s=50), expected=["location", "building", "regulatory"],
    optional=["proximity"], activate=True, family=COM, rng=known(prog(50)), user_values=[2000],
    basis="29 + 7 x 50; the user's 2000 m2 is a preference", mirrors="DQ-M05",
    covers=["echo-guard", "user-bound-above-regulation"])
add("PQ-M11", "Edificio cerca de Argüelles para una residencia de estudiantes con 30 habitaciones individuales; "
    "entiendo que cada habitación tiene que ser una vivienda mínima.", suites=S, landmark="argüelles",
    use="student_residence", capacity=beds(s=30), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(30)),
    basis="Lex specialis: establishment as a whole (7.3.11.2, FAQ 20.b); per-room reading 1200 is outside",
    mirrors="DQ-M04", covers=["lex-specialis", "user-misconception"])
add("PQ-M12", "A small student residence with 4 single rooms near Lavapiés.", lang="en", landmark="lavapiés",
    use="student_residence", capacity=beds(s=4), expected=["location", "regulatory"],
    optional=["building", "proximity"], activate=True, family=COM, rng=known(prog(4)), basis="29 + 7 x 4",
    mirrors="DQ-M10", covers=["small-capacity"])

# ------------------------------------------------------------ residencia compartida (law above the heuristic)
add("PQ-M13", "Busco un edificio completo cerca de Atocha para un coliving con 20 unidades de alojamiento "
    "individuales.", suites=S, landmark="atocha", use="co_living", capacity=units(s=20),
    expected=["location", "regulatory"], optional=["building"], activate=True, family=CPT, rng=known(shared(20)),
    basis="7.3.11.3: 20 x 15 + 25 + 5 x 19", mirrors="DQ-M11", covers=["co-living"])
add("PQ-M14", "Co-living in a dedicated building near Malasaña with 10 single units and 5 double units.", suites=S, lang="en",
    landmark="malasaña", use="co_living", capacity=units(s=10, d=5), expected=["location", "regulatory"],
    optional=["building"], activate=True, family=CPT, rng=known(shared(10, 5)),
    basis="10 x 15 + 5 x 20 + 25 + 5 x 14", mirrors="DQ-M12", covers=["co-living", "mixed-units"])
add("PQ-M15", "Residencia compartida para 30 personas en un edificio exclusivo cerca de Plaza de Castilla, con "
    "certificado energético C o mejor.", complexity="complex", landmark="plaza de castilla", energy=C_OR_BETTER,
    use="co_living", capacity={"persons": 30}, expected=["location", "energy", "regulatory"], optional=["building"],
    activate=True, family=CPT, rng=shared_unknown(30), basis="Units not stated: [most doubles, 1.25 x all singles]",
    mirrors="DQ-M13", covers=["co-living", "energy"])
add("PQ-M16", "Busco un building para un coliving con 15 private units individuales near Puerta del Sol.",
    lang="mixed", landmark="puerta del sol", use="co_living", capacity=units(s=15), expected=["location", "regulatory"],
    optional=["building"], activate=True, family=CPT, rng=known(shared(15)), basis="15 x 15 + 25 + 5 x 14",
    mirrors="DQ-M15", covers=["code-switching"])
add("PQ-M17", "Coliving para 12 personas en unidades individuales de 30 m2 cada una, cerca de Lavapiés.", suites=S,
    landmark="lavapiés", use="co_living", capacity=units(s=12), expected=["location", "regulatory"],
    optional=["building"], activate=True, family=CPT, rng=known(shared(12)), user_values=[30, 360],
    basis="12 x 15 + 25 + 5 x 11; the user's 30 m2 per unit is a preference", mirrors="DQ-M14",
    covers=["echo-guard"])

# ------------------------------------------------------------ dwellings (art. 7.3.4)
add("PQ-M18", "Quiero convertir un local comercial cerca de Lavapiés en una vivienda de un dormitorio para una "
    "persona.", suites=S, landmark="lavapiés", typology=COMMERCIAL, use="dwelling",
    capacity={"persons": 1, "bedrooms": 1}, expected=["location", "building", "regulatory"], activate=True,
    family=DW, rng=known(prog(1)), basis="7.3.14 -> 7.3.4 (FAQ 21.a): 40", mirrors="DQ-M16",
    covers=["change-of-use", "commercial-typology"])
add("PQ-M19", "A three-bedroom flat for a family of four near Retiro Park, close to schools.", suites=S, lang="en",
    complexity="complex", landmark="parque del retiro", services=["education"], typology=RES, use="dwelling",
    capacity={"persons": 4, "bedrooms": 3}, expected=["location", "proximity", "regulatory"], optional=["building"],
    activate=True, family=DW, rng=known(prog(3)), basis="14 + 7 + 3 + 12 + 7 x 2", mirrors="DQ-M17",
    covers=["family", "education"])
add("PQ-M20", "Vivienda de uso turístico con dos dormitorios para cuatro huéspedes cerca de la Puerta del Sol.", suites=S,
    landmark="puerta del sol", typology=RES, use="tourist_dwelling", capacity={"persons": 4, "bedrooms": 2},
    expected=["location", "regulatory"], optional=["building"], activate=True, family=DW, rng=known(prog(2)),
    basis="7.6.3.2 -> 7.3.4 (FAQ 25.a)", mirrors="DQ-M18", covers=["tourist-dwelling"])
add("PQ-M21", "Un estudio para una persona cerca de Malasaña.", suites=S, complexity="simple", landmark="malasaña",
    typology=RES, use="studio", capacity={"persons": 1, "bedrooms": 1}, expected=["location", "regulatory"],
    optional=["building"], activate=True, family=DW, rng=known(prog(1)), basis="Minimum dwelling 40 (open plan allowed)",
    mirrors="DQ-M19", covers=["studio"])
add("PQ-M22", "A two-bedroom flat for a couple with a child near Atocha, between 30 and 40 square metres.", lang="en",
    landmark="atocha", surface={"min": 30, "max": 40}, typology=RES, use="dwelling",
    capacity={"persons": 3, "bedrooms": 2}, expected=["location", "building", "regulatory"], activate=True,
    family=DW, rng=known(prog(2)), user_values=[30, 40], basis="Programme for 2 bedrooms: 43, above the user's bounds",
    covers=["conflict-user-vs-regulation"], note="No result can satisfy both: tests how the pipeline resolves it.")
add("PQ-M23", "Vivienda de cuatro dormitorios para una familia de seis cerca de Plaza de Castilla.",
    landmark="plaza de castilla", typology=RES, use="dwelling", capacity={"persons": 6, "bedrooms": 4},
    expected=["location", "regulatory"], optional=["building"], activate=True, family=DW, rng=known(prog(4)),
    basis="14 + 7 + 3 + 12 + 7 x 3", mirrors="DQ-M21", covers=["large-family"])
add("PQ-M24", "A shared flat for 4 university students near Ciudad Universitaria.", lang="en",
    landmark="ciudad universitaria", typology=RES, use="shared_flat", capacity={"persons": 4},
    expected=["location", "regulatory"], optional=["building", "proximity"], activate=True, family=DW,
    rng=prog_unknown(4), basis="Dwelling, bedrooms not stated: [P(2), 1.25 P(4)]", mirrors="DQ-M23",
    covers=["shared-flat", "student"])
add("PQ-M25", "Una vivienda para una pareja, no una residencia, con un solo dormitorio, cerca de Argüelles.",
    landmark="argüelles", typology=RES, use="dwelling", capacity={"persons": 2, "bedrooms": 1},
    expected=["location", "regulatory"], optional=["building"], activate=True, family=DW, rng=known(prog(1)),
    basis="Minimum dwelling 40", mirrors="DQ-M22", covers=["negation"])

# ------------------------------------------------------------ either
add("PQ-M26", "Residencia de mayores para 20 personas cerca del Hospital Universitario La Paz, cerca de servicios "
    "sanitarios.", landmark="hospital universitario la paz", services=["healthcare"], use="senior_residence",
    capacity={"beds": 20}, expected=["location", "proximity"], optional=["building", "regulatory"],
    activate="either", family=COM, rng=prog_unknown(20), basis="May be a social-services facility; if 7.3.11.2 applies",
    mirrors="DQ-M24", covers=["senior", "healthcare"])
add("PQ-M27", "A building to house 15 seasonal workers in shared rooms near Atocha.", lang="en", landmark="atocha",
    use="worker_housing", capacity={"beds": 15}, expected=["location"], optional=["building", "regulatory"],
    activate="either", family=COM, rng=prog_unknown(15), basis="Residencia comunitaria is defensible; if applied",
    mirrors="DQ-M25", covers=["worker-housing"])
add("PQ-M28", "Residencia compartida para 8 personas en dos plantas de un edificio de viviendas cerca de Malasaña.",
    landmark="malasaña", use="co_living", capacity={"persons": 8}, expected=["location"],
    optional=["building", "regulatory"], activate="either", family=CPT, rng=shared_unknown(8),
    basis="7.3.11.5: only in an exclusive building (declining is better); a grounded surface is accepted",
    mirrors="DQ-M26", covers=["exclusive-building"])

# ------------------------------------------------------------ no rule applies
add("PQ-M29", "Piso con certificado energético A o B cerca de Nuevos Ministerios.", complexity="simple",
    landmark="nuevos ministerios", energy=["A", "B"], typology=RES, expected=["location", "energy"],
    optional=["building"], basis="Energy preference only", covers=["energy", "no-rule"])
add("PQ-M30", "Office of at least 250 square metres near Nuevos Ministerios.", lang="en", suites=S,
    landmark="nuevos ministerios", surface={"min": 250, "max": None}, typology=OFFICES, use="office",
    expected=["location", "building"], user_values=[250], basis="Office use: no regulatory area",
    covers=["office", "echo-guard"])
add("PQ-M31", "Local comercial de al menos 100 m2 cerca de la Puerta del Sol, bien comunicado.", complexity="complex",
    landmark="puerta del sol", surface={"min": 100, "max": None}, typology=COMMERCIAL, services=["mobility"],
    use="shop", expected=["location", "building", "proximity"], user_values=[100], basis="Shop: no regulatory area",
    covers=["commercial", "mobility"])
add("PQ-M32", "Vivienda de al menos 120 m2 cerca del Parque del Retiro.", suites=S, complexity="simple",
    landmark="parque del retiro", surface={"min": 120, "max": None}, typology=RES, expected=["location", "building"],
    user_values=[120], basis="User surface, no regulation", covers=["echo-without-capacity"])
add("PQ-M33", "Property near Malasaña close to parks and public transport.", lang="en", suites=S, landmark="malasaña",
    services=["green", "mobility"], expected=["location", "proximity"], basis="Services only", covers=["proximity"])
add("PQ-M34", "Quiero transformar un edificio de oficinas cerca de Moncloa en residencia de estudiantes.", suites=S,
    landmark="moncloa", typology=OFFICES, use="student_residence", expected=["location", "building"],
    optional=["proximity"], basis="No capacity: no building-level threshold", mirrors="DQ-M28",
    covers=["no-capacity", "change-of-use"])
add("PQ-M35", "Residencia de estudiantes con habitaciones en planta sótano cerca de Ciudad Universitaria.",
    landmark="ciudad universitaria", use="student_residence", expected=["location"], optional=["building", "proximity"],
    basis="7.3.11.4 forbids basement units: a prohibition, not an area", mirrors="DQ-M31", covers=["non-surface"])
add("PQ-M36", "Vivienda en una buhardilla con techos bajos cerca de Lavapiés.", landmark="lavapiés", typology=RES,
    expected=["location"], optional=["building"], basis="7.3.5 clear heights, not areas", mirrors="DQ-M35",
    covers=["height-rule"])
add("PQ-M37", "Guardería para 40 niños cerca de Plaza de Castilla.", landmark="plaza de castilla", use="nursery",
    capacity={"persons": 40}, expected=["location"], optional=["building", "proximity"],
    basis="No nursery standard in the corpus", mirrors="DQ-M38", covers=["out-of-corpus"])
add("PQ-M38", "An industrial warehouse of about 1500 square metres near Atocha.", lang="en", landmark="atocha",
    surface={"min": 1500, "max": None}, use="industrial", expected=["location", "building"], user_values=[1500],
    basis="Industrial use, no regulatory area", mirrors="DQ-M39", covers=["industrial", "echo-guard"],
    note="No industrial typology in the Madrid dataset: the building agent can only filter by surface.")
add("PQ-M39", "Piso construido después del año 2000 cerca de la Estación de Chamartín, con certificado energético B.",
    complexity="complex", landmark="estación de chamartín", energy=["B"], typology=RES,
    expected=["location", "energy", "building"], basis="Construction period and energy only",
    covers=["construction-period", "energy"])
add("PQ-M40", "Large home near Gregorio Marañón hospital close to sports facilities.", lang="en",
    landmark="hospital general universitario gregorio marañón", services=["sport"], typology=RES,
    expected=["location", "proximity"], optional=["building"], basis="'Large' is relative, no regulation",
    covers=["relative-size", "sport"])

meta = {
    "description": "Hand-curated full-pipeline benchmark for MURENA (Madrid). Same design as benchmark_queries_turin.json: "
                   "each query runs end to end through all agents; ground truth is the expected agent activation and "
                   "the regulatory expectation. One language per query (es / en / mixed).",
    "schema": "pipeline-v1 (see benchmark_queries_turin.json). id PQ-Mnn. slots.landmark = key of meta.landmarks "
              "(coordinates the pipeline's geocoder returns for that name in the Madrid context). rule_family / "
              "heuristic_stratum as in downstream_queries_madrid.json; regulatory ranges follow its value_basis.",
    "country": "ES",
    "dataset": "data/metadata/estates_madrid.parquet (residential, commercial premises and offices)",
    "landmarks": LANDMARKS,
    "composition": "",
    "caveats": [
        "Author-constructed; regulatory ranges follow downstream_queries_madrid.json value_basis.",
        "Feasibility (non-empty results) is checked on the dataset with the slots as SQL-like filters within 3 km.",
        "The pipeline's geocoder misplaces 'Universidad Politécnica de Madrid' (~12 km north) and does not resolve "
        "'Campus Sur UPM'; neither is used.",
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
