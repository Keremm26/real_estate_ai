"""Generate downstream_queries_madrid.json (the Madrid downstream set of the
RAG study, Spanish + English). Every expected range is computed from the
value_basis formulas below, never typed by hand; tests/validate_query_files.py
recomputes them independently. Edit the items here and regenerate:
    python tests/query_generators/make_downstream_madrid.py
"""
import json
import sys
import math
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "app" / "services" / "llm" / "rag" / "eval" / "downstream_queries_madrid.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_rules import known, prog, prog_unknown, shared, shared_unknown, stratum  # noqa: E402

C7311 = {"doc_key": "pgoum_nnuu_compendio", "article_ref": "TÍTULO 7 > 7.3 > 7.3.11"}
C734 = {"doc_key": "pgoum_nnuu_compendio", "article_ref": "TÍTULO 7 > 7.3 > 7.3.4"}
C731 = {"doc_key": "pgoum_nnuu_compendio", "article_ref": "TÍTULO 7 > 7.3 > 7.3.1"}
C735 = {"doc_key": "pgoum_nnuu_compendio", "article_ref": "TÍTULO 7 > 7.3 > 7.3.5"}
C7535 = {"doc_key": "pgoum_nnuu_compendio", "article_ref": "TÍTULO 7 > 7.5 > 7.5.35"}
FAQ20 = {"doc_key": "pgoum_preguntas_interes", "article_ref": "20"}
FAQ21 = {"doc_key": "pgoum_preguntas_interes", "article_ref": "21"}
FAQ24 = {"doc_key": "pgoum_preguntas_interes", "article_ref": "24"}
FAQ25 = {"doc_key": "pgoum_preguntas_interes", "article_ref": "25"}
L152 = {"doc_key": "ley_9_2001_suelo_madrid", "article_ref": "Artículo 152"}
L155 = {"doc_key": "ley_9_2001_suelo_madrid", "article_ref": "Artículo 155"}
L157 = {"doc_key": "ley_9_2001_suelo_madrid", "article_ref": "Artículo 157"}
HS = {"doc_key": "cte_db_hs", "article_ref": "*"}
SI = {"doc_key": "cte_db_si", "article_ref": "*"}
RES = [C7311, FAQ20, C734]

items = []


def add(id_, type_, tier, use_case, es, en, family, capacity, user_values, activate, rng, basis, sources,
        covers, note):
    cap = capacity or {}
    persons = cap.get("persons") or cap.get("beds")
    vmin, vmax = rng if rng else (None, None)
    items.append({
        "id": id_, "type": type_, "tier": tier, "use_case": use_case, "queries": {"es": es, "en": en},
        "capacity": capacity, "user_values": user_values,
        "expect": {"activate": activate,
                   "target_column": "surface_area" if activate is not False else None,
                   "operator": ">=" if activate is not False else None,
                   "value_min": vmin, "value_max": vmax, "basis": basis},
        "rule_family": family if activate is not False else None,
        "heuristic_stratum": stratum(vmin, persons) if activate is True else None,
        "sources": sources, "covers": covers, "note": note,
    })


def beds(s=0, d=0):
    return {"beds": s + 2 * d, "single_rooms": s, "double_rooms": d}


# ------------------------------------------- residencia comunitaria (students)
add("DQ-M01", "dimensioning", "municipal", "student_housing",
    "Busco un edificio para una residencia de estudiantes con 40 habitaciones individuales.",
    "I'm looking for a building for a student residence with 40 single rooms.",
    "es_res_comunitaria", beds(s=40), [], True, known(prog(40)),
    f"Establishment as a whole (art. 7.3.11.2 -> 7.3.4): 29 + 7 x 40 = {prog(40)}; range [P, 1.25 P]",
    RES, ["student", "single-rooms", "establishment-as-a-whole"],
    "Rooms of a residence count as bedrooms of one establishment: one 12 m2 bedroom, the others 7 m2.")
add("DQ-M02", "dimensioning", "municipal", "student_housing",
    "Necesito un inmueble para una residencia universitaria de 60 plazas.",
    "I need a property for a 60-place university residence.",
    "es_res_comunitaria", {"beds": 60}, [], True, prog_unknown(60),
    f"60 beds, rooms not stated (1-2 per room): [P(30), 1.25 P(60)] = {prog_unknown(60)}",
    RES, ["student", "mix-unknown", "places-not-beds"], "Room mix unknown: the range spans doubles to singles.")
add("DQ-M03", "room_mix", "municipal", "student_housing",
    "Residencia de estudiantes con 20 habitaciones dobles y 10 individuales.",
    "Student residence with 20 double rooms and 10 single rooms.",
    "es_res_comunitaria", beds(s=10, d=20), [], True, known(prog(30)),
    f"30 bedrooms: 29 + 7 x 30 = {prog(30)}; range [P, 1.25 P]", RES, ["student", "mixed-rooms"],
    "The norm sizes bedrooms, not beds: a double room is one 7 m2 bedroom.")
add("DQ-M04", "precedence", "municipal", "student_housing",
    "Edificio para una residencia de estudiantes con 30 habitaciones individuales; entiendo que cada habitación "
    "tiene que cumplir las condiciones de una vivienda mínima.",
    "Building for a student residence with 30 single rooms; I understand each room has to meet the "
    "minimum-dwelling conditions.",
    "es_res_comunitaria", beds(s=30), [], True, known(prog(30)),
    f"Lex specialis: art. 7.3.11.2 applies 7.3.4 to the establishment AS A WHOLE (FAQ 20.b): {prog(30)}; "
    f"range [P, 1.25 P]. The per-room reading (30 x 40 = 1200) is the user's misconception and falls outside.",
    RES, ["lex-specialis", "establishment-as-a-whole", "user-misconception"],
    "The value separates the two readings (both live in the same document, so winner_cited is not used).")
add("DQ-M05", "echo_trap", "municipal", "student_housing",
    "Residencia para 50 estudiantes en habitaciones individuales; el edificio debe tener más de 2000 m2.",
    "Residence for 50 students in single rooms; the building must be larger than 2000 square metres.",
    "es_res_comunitaria", beds(s=50), [2000], True, known(prog(50)),
    f"29 + 7 x 50 = {prog(50)}; the user's 2000 m2 is a preference, not a regulation", RES,
    ["student", "echo-with-capacity", "user-bound-above-regulation"],
    "The regulatory value must come from the PGOUM, not repeat the user's 2000.")
add("DQ-M06", "number_in_words", "municipal", "student_housing",
    "Residencia de estudiantes con sesenta habitaciones individuales.",
    "Student residence with sixty single rooms.",
    "es_res_comunitaria", beds(s=60), [], True, known(prog(60)), f"29 + 7 x 60 = {prog(60)}", RES,
    ["student", "number-in-words"], "Capacity written in words.")
add("DQ-M07", "typos", "municipal", "student_housing",
    "Recidencia de estudiantes con 25 avitaciones individuales en Madird.",
    "Studnet residence with 25 singel rooms in Madird.",
    "es_res_comunitaria", beds(s=25), [], True, known(prog(25)), f"29 + 7 x 25 = {prog(25)}", RES,
    ["student", "typos"], "Spelling errors must not break routing or retrieval.")
add("DQ-M08", "long_query", "municipal", "student_housing",
    "Hola, buenos días. Represento a una fundación educativa y estamos buscando un edificio en Madrid, idealmente "
    "cerca de Moncloa o de la Ciudad Universitaria, para abrir una residencia para 45 estudiantes de grado, cada uno "
    "en su habitación individual. Nos importa que haya buen transporte y algo de zona verde cerca. ¿Qué deberíamos "
    "buscar?",
    "Hello, good morning. I represent an educational foundation and we are looking for a building in Madrid, "
    "ideally near Moncloa or the Ciudad Universitaria, to open a residence for 45 undergraduate students, each in "
    "a single room. We care about good transport and some green space nearby. What should we look for?",
    "es_res_comunitaria", beds(s=45), [], True, known(prog(45)), f"29 + 7 x 45 = {prog(45)}", RES,
    ["student", "long-query", "location-noise"], "Long request with location and preference noise.")
add("DQ-M09", "self_correction", "municipal", "student_housing",
    "Residencia para 30 estudiantes... mejor que sean 40, todos en habitaciones individuales.",
    "Residence for 30 students... make that 40, all in single rooms.",
    "es_res_comunitaria", beds(s=40), [30], True, known(prog(40)),
    f"Corrected capacity 40: {prog(40)} (30 would give {prog(30)}, outside)", RES,
    ["student", "self-correction"], "The corrected figure governs.")
add("DQ-M10", "dimensioning", "municipal", "student_housing",
    "Una residencia de estudiantes pequeña con 4 habitaciones individuales.",
    "A small student residence with 4 single rooms.",
    "es_res_comunitaria", beds(s=4), [], True, known(prog(4)),
    f"29 + 7 x 4 = {prog(4)} (above the 40 m2 floor)", RES, ["student", "small-capacity"],
    "Smallest scale: the programme is above the 40 m2 minimum dwelling.")

# ------------------------------------------- residencia compartida (co-living)
add("DQ-M11", "dimensioning", "municipal", "general",
    "Busco un edificio completo para una residencia compartida (coliving) con 20 unidades de alojamiento individuales.",
    "I'm looking for a whole building for a shared residence (co-living) with 20 single accommodation units.",
    "es_res_compartida", {"persons": 20, "single_units": 20, "double_units": 0}, [], True, known(shared(20)),
    f"Art. 7.3.11.3: 20 x 15 + common 25 + 5 x 19 = {shared(20)}; range [C, 1.25 C]", [C7311],
    ["co-living", "single-units"], "Private units plus common areas that grow with the number of units.")
add("DQ-M12", "room_mix", "municipal", "general",
    "Coliving en edificio exclusivo con 10 unidades individuales y 5 unidades dobles.",
    "Co-living in a dedicated building with 10 single units and 5 double units.",
    "es_res_compartida", {"persons": 20, "single_units": 10, "double_units": 5}, [], True, known(shared(10, 5)),
    f"10 x 15 + 5 x 20 (10 m2 per person) + common 25 + 5 x 14 = {shared(10, 5)}", [C7311],
    ["co-living", "mixed-units", "per-person-floor"], "A double unit needs 20 m2 (10 per person), not 15.")
add("DQ-M13", "dimensioning", "municipal", "general",
    "Residencia compartida para 30 personas en un edificio exclusivo.",
    "A shared residence for 30 people in a dedicated building.",
    "es_res_compartida", {"persons": 30}, [], True, shared_unknown(30),
    f"Units not stated: all doubles {shared(0, 15)} .. all singles x 1.25 = {shared_unknown(30)}", [C7311],
    ["co-living", "mix-unknown"], "")
add("DQ-M14", "echo_trap", "municipal", "general",
    "Coliving para 12 personas en unidades individuales de 30 m2 cada una.",
    "Co-living for 12 people in single units of 30 square metres each.",
    "es_res_compartida", {"persons": 12, "single_units": 12, "double_units": 0}, [30, 360], True,
    known(shared(12)), f"{shared(12)}; the user's 30 m2 per unit (360) is a preference", [C7311],
    ["co-living", "echo-guard"], "Neither 30 nor 12 x 30 = 360 is the regulatory value.")
add("DQ-M15", "code_switching", "municipal", "general",
    "Busco un building para un coliving con 15 private units individuales en Madrid.",
    "Looking for an edificio for a coliving with 15 unidades privadas individuales in Madrid.",
    "es_res_compartida", {"persons": 15, "single_units": 15, "double_units": 0}, [], True, known(shared(15)),
    f"{shared(15)}", [C7311], ["co-living", "code-switching"], "Spanish-English mix in both versions.")

# ------------------------------------------- dwelling (art. 7.3.4)
add("DQ-M16", "general_dwelling", "municipal", "general",
    "Quiero convertir un local comercial en una vivienda de un dormitorio para una persona.",
    "I want to convert a shop premises into a one-bedroom home for one person.",
    "es_dwelling", {"persons": 1, "bedrooms": 1}, [], True, known(prog(1)),
    f"Art. 7.3.14 -> 7.3.4 (FAQ 21.a): minimum dwelling {prog(1)}", [FAQ21, C734],
    ["change-of-use", "minimum-dwelling"], "A converted premises meets the same 40 m2 minimum dwelling.")
add("DQ-M17", "general_dwelling", "municipal", "general",
    "Piso de tres dormitorios para una familia de cuatro personas.",
    "A three-bedroom flat for a family of four.",
    "es_dwelling", {"persons": 4, "bedrooms": 3}, [], True, known(prog(3)),
    f"14 + 7 + 3 + 12 + 7 x 2 = {prog(3)}", [C734], ["dwelling", "family"],
    "The norm sizes rooms, not persons: three bedrooms set the programme.")
add("DQ-M18", "general_dwelling", "municipal", "general",
    "Vivienda de uso turístico con dos dormitorios para cuatro huéspedes.",
    "A tourist-use dwelling with two bedrooms for four guests.",
    "es_dwelling", {"persons": 4, "bedrooms": 2}, [], True, known(prog(2)),
    f"Art. 7.6.3.2 -> chapter 7.3 section one (FAQ 25.a): {prog(2)}", [FAQ25, C734],
    ["tourist-dwelling", "dwelling"], "Tourist dwellings meet the ordinary dwelling programme.")
add("DQ-M19", "studio_minimum", "municipal", "general",
    "Un estudio para una persona.", "A studio for one person.",
    "es_dwelling", {"persons": 1, "bedrooms": 1}, [], True, known(prog(1)),
    f"Open-plan dwelling allowed if it reaches the minimum (art. 7.3.4.1): {prog(1)}", [C734],
    ["studio", "open-plan"], "Diáfana allowed, but still 40 m2.")
add("DQ-M20", "echo_trap", "municipal", "general",
    "Vivienda de dos dormitorios para una pareja con un hijo, de al menos 90 m2.",
    "A two-bedroom home for a couple with a child, of at least 90 square metres.",
    "es_dwelling", {"persons": 3, "bedrooms": 2}, [90], True, known(prog(2)),
    f"{prog(2)}; the user's 90 m2 is a preference", [C734], ["dwelling", "echo-guard"], "")
add("DQ-M21", "general_dwelling", "municipal", "general",
    "Vivienda de cuatro dormitorios para una familia de seis.",
    "A four-bedroom dwelling for a family of six.",
    "es_dwelling", {"persons": 6, "bedrooms": 4}, [], True, known(prog(4)),
    f"14 + 7 + 3 + 12 + 7 x 3 = {prog(4)}", [C734], ["dwelling", "large-family"],
    "Below 10 m2 per person: the room programme, not the head count, governs.")
add("DQ-M22", "negation", "municipal", "general",
    "Una vivienda para una pareja, no una residencia, con un solo dormitorio.",
    "A home for a couple, not a residence, with just one bedroom.",
    "es_dwelling", {"persons": 2, "bedrooms": 1}, [], True, known(prog(1)), f"{prog(1)}", [C734],
    ["dwelling", "negation"], "Negated residence: the dwelling rule applies.")
add("DQ-M23", "dimensioning", "municipal", "student_housing",
    "Piso compartido para 4 estudiantes universitarios.", "A shared flat for 4 university students.",
    "es_dwelling", {"persons": 4}, [], True, prog_unknown(4),
    f"A shared flat is a dwelling; bedrooms not stated (1-2 per room): [P(2), 1.25 P(4)] = {prog_unknown(4)}",
    [C734], ["student", "shared-flat", "dwelling"], "Not a residence: the dwelling programme applies.")

# ------------------------------------------- either
add("DQ-M24", "ambiguous_use", "municipal", "general",
    "Residencia de mayores para 20 personas.", "A residence for 20 elderly people.",
    "es_res_comunitaria", {"beds": 20}, [], "either", prog_unknown(20),
    f"Care residences may be classified as a social-services facility rather than a residencia comunitaria; "
    f"if art. 7.3.11.2 is applied: {prog_unknown(20)}", RES, ["senior", "classification"],
    "Declining is acceptable.")
add("DQ-M25", "ambiguous_use", "municipal", "general",
    "Edificio para alojar a 15 trabajadores temporales en habitaciones compartidas.",
    "A building to house 15 seasonal workers in shared rooms.",
    "es_res_comunitaria", {"beds": 15}, [], "either", prog_unknown(15),
    f"Worker housing: residencia comunitaria (art. 7.3.1 groups with shared ties) is defensible; if applied: "
    f"{prog_unknown(15)}", [C731, C7311, C734], ["worker-housing", "classification"], "")
add("DQ-M26", "ambiguous_use", "municipal", "general",
    "Residencia compartida para 8 personas en dos plantas de un edificio de viviendas.",
    "A shared residence for 8 people on two floors of a residential building.",
    "es_res_compartida", {"persons": 8}, [], "either", shared_unknown(8),
    f"Art. 7.3.11.5: residencias compartidas only in an exclusive building, so the use is not permitted here "
    f"(declining is the better answer); a grounded surface is accepted: {shared_unknown(8)}", [C7311, FAQ20],
    ["co-living", "exclusive-building"], "")

# ------------------------------------------- decline
add("DQ-M27", "no_capacity", None, "student_housing",
    "¿Qué superficie mínima debe tener una residencia de estudiantes en Madrid?",
    "What minimum floor area must a student residence in Madrid have?",
    None, None, [], False, None, "No number of rooms or beds", RES, ["no-capacity", "question-form"],
    "Retrieval should find 7.3.11 / FAQ 20; extraction should decline.")
add("DQ-M28", "no_capacity", None, "student_housing",
    "Quiero transformar un edificio de oficinas en residencia de estudiantes.",
    "I want to turn an office building into a student residence.",
    None, None, [], False, None, "No capacity", [C7311], ["no-capacity", "change-of-use"], "")
add("DQ-M29", "negative", None, "general",
    "Oficina de al menos 250 m2 en el barrio de Salamanca.",
    "An office of at least 250 square metres in the Salamanca district.",
    None, None, [250], False, None, "Office use: no regulatory surface threshold", [], ["office", "echo-guard"], "")
add("DQ-M30", "negative", None, "general",
    "Piso con certificado energético A o B en Chamberí.", "A flat with an A or B energy certificate in Chamberí.",
    None, None, [], False, None, "Energy preference only", [], ["energy", "negative"], "")
add("DQ-M31", "non_surface_rule", "municipal", "student_housing",
    "Residencia de estudiantes con habitaciones en planta sótano.",
    "Student residence with bedrooms on the basement floor.",
    None, None, [], False, None, "Art. 7.3.11.4 forbids units below the ground floor: a prohibition, not an area",
    [C7311], ["basement", "non-surface"], "")
add("DQ-M32", "non_surface_rule", "municipal", "student_housing",
    "¿Cuántas plazas de aparcamiento necesita una residencia de estudiantes de 100 plazas?",
    "How many parking spaces does a 100-place student residence need?",
    None, {"beds": 100}, [], False, None, "Parking standard (1 per 100 m2, FAQ 24 / art. 7.5.35), not a surface",
    [FAQ24, C7535], ["parking", "non-surface"], "A capacity is present but the rule is about parking.")
add("DQ-M33", "procedural", "regional", "general",
    "¿Necesito licencia urbanística para cambiar el uso de un local a vivienda en la Comunidad de Madrid?",
    "Do I need a planning licence to change a shop premises into a dwelling in the Community of Madrid?",
    None, None, [], False, None, "Ley 9/2001 arts. 152 / 155: planning titles, procedural", [L152, L155],
    ["licence", "procedural", "regional"], "")
add("DQ-M34", "procedural", "regional", "general",
    "¿Desde cuándo puedo empezar las obras con una declaración responsable urbanística?",
    "From when can I start works under a declaración responsable urbanística?",
    None, None, [], False, None, "Ley 9/2001 art. 157: procedural", [L157], ["procedural", "regional"], "")
add("DQ-M35", "non_surface_rule", "municipal", "general",
    "Vivienda en una buhardilla con techos bajos.", "A home in an attic with low ceilings.",
    None, None, [], False, None, "Art. 7.3.5 sets clear heights (250 / 220 cm), not areas", [C735],
    ["height-rule", "attic"], "")
add("DQ-M36", "non_surface_rule", "national", "general",
    "¿Qué caudal de ventilación necesitan los dormitorios de una vivienda?",
    "What ventilation flow do the bedrooms of a dwelling need?",
    None, None, [], False, None, "CTE DB-HS 3: flow rates, not areas", [HS], ["ventilation", "non-surface"], "")
add("DQ-M37", "non_surface_rule", "national", "general",
    "Ocupación de cálculo para la evacuación de una residencia de 80 plazas.",
    "Design occupancy for evacuating an 80-place residence.",
    None, {"beds": 80}, [], False, None, "CTE DB-SI 3 occupancy densities serve evacuation sizing, not a minimum area",
    [SI], ["fire", "non-surface"], "")
add("DQ-M38", "out_of_corpus", None, "general",
    "Guardería para 40 niños en Tetuán.", "A nursery for 40 children in Tetuán.",
    None, {"persons": 40}, [], False, None, "No nursery standard in the corpus", [], ["childcare", "out-of-corpus"], "")
add("DQ-M39", "negative", None, "general",
    "Nave industrial de unos 1500 m2 en Villaverde.",
    "An industrial warehouse of about 1500 square metres in Villaverde.",
    None, None, [1500], False, None, "Industrial use, no regulatory area", [], ["industrial", "echo-guard"], "")
add("DQ-M40", "procedural", "municipal", "student_housing",
    "¿Cómo se clasifica el alojamiento colectivo de estudiantes en las normas urbanísticas de Madrid?",
    "How is collective student accommodation classified in Madrid's planning norms?",
    None, None, [], False, None, "Art. 7.3.1: classification (residencia comunitaria), no area", [C731],
    ["classification", "question-form"], "")

meta = {
    "description": "Downstream evaluation of the regulatory agent with RAG retrieval (Madrid). Same design as the "
                   "Turin set: each item is one production-shaped request; the agent must either produce a "
                   "document-grounded building-level surface threshold (surface_area >= X) or decline (found: false).",
    "schema": "downstream-v2 (see downstream_queries_turin.json). One query per language: queries: {es, en}. "
              "rule_family in {es_res_comunitaria, es_res_compartida, es_dwelling} names the value_basis formula "
              "(null when no rule applies). heuristic_stratum = whether value_min is above the building agent's "
              "~10 m2/person capacity estimate ('law_above_heuristic') or not ('law_at_or_below_heuristic').",
    "country": "ES",
    "corpus": "Madrid slice of the normative store: state (CTE DB-SUA, DB-HS, DB-SI), regional (Ley 9/2001 del Suelo "
              "de la Comunidad de Madrid), municipal (PGOUM Compendio de las Normas Urbanísticas, MPG NNUU sept. "
              "2025, and the Ayuntamiento's 'Preguntas de interés' FAQ).",
    "value_basis": {
        "programme": "PGOUM art. 7.3.4: minimum dwelling 40 m2 useful with living-dining 14, kitchen 7, bedroom 12 "
                     "(additional bedrooms 7) and bathroom 3. P(R) = max(40, 29 + 7 R) for R bedrooms.",
        "res_comunitaria": "Art. 7.3.11.2 (+ FAQ 20.b): residencias comunitarias (incl. student residences, art. "
                           "7.3.1) meet 7.3.3 / 7.3.4 / 7.3.5 for the establishment AS A WHOLE, so the rooms are "
                           "bedrooms of one programme: rooms known [P(R), 1.25 P(R)]; only beds known (1-2 per "
                           "room) [P(ceil(N/2)), 1.25 P(N)].",
        "res_compartida": "Art. 7.3.11.3: private unit (bedroom + bathroom) >= 15 m2 and >= 10 m2 per person "
                          "(single 15, double 20); common areas >= 25 m2 for one unit + 5 per additional unit. "
                          "C = 20 s + 25 d + 20; known [C, 1.25 C]; persons only [most doubles, 1.25 x all singles]. "
                          "Only in an exclusive building (7.3.11.5).",
        "dwelling": "Art. 7.3.4 (conversions 7.3.14 / FAQ 21, tourist dwellings 7.6.3.2 / FAQ 25): [P(R), 1.25 P(R)]; "
                    "bedrooms unknown with N persons (1-2 per room): [P(ceil(N/2)), 1.25 P(N)].",
    },
    "metrics": "As the Turin set (regulatory_scoring). The lex-specialis item (DQ-M04) is scored by value: both "
               "readings live in the same document, so winner_cited is not used. Items with activate 'either' are "
               "scored on grounding only.",
    "caveats": [
        "Author-constructed, not validated by a Spanish planning lawyer: state as a limitation.",
        "The norm does not set persons per bedroom; where only beds or persons are given, 1-2 per bedroom is "
        "assumed (most residence items state the rooms).",
        "Elderly care residences may be a social-services facility rather than a residencia comunitaria (DQ-M24 is "
        "'either').",
        "The 1.25 upper tolerance follows the Turin set.",
    ],
    "known_retrieval_issues": [
        "Retrieval check 2026-10-12 (rag arm, country ES, use-case router on, no LLM): routing 40/40 in both "
        "languages, expected source in context es 25/36, en 28/36. To be decided later, with generic fixes only:",
        "Co-living items (art. 7.3.11.3: DQ-M12, M14, M15) and dwelling / studio items (art. 7.3.4: DQ-M19, M22, "
        "M23) often retrieve FAQ sections (4, 17, 19) or CTE chunks instead of the article.",
        "Procedural items (Ley 9/2001 arts. 152 / 155 / 157: DQ-M33, M34) retrieve PGOUM chunks instead of the "
        "regional law; the height item (art. 7.3.5: DQ-M35) misses its article.",
        "Spanish queries retrieve slightly worse than English ones (25 vs 28 of 36).",
    ],
}

OUT.write_text(json.dumps({"meta": meta, "queries": items}, indent=2, ensure_ascii=False) + "\n")
print("items:", len(items), "| activate:", dict(Counter(str(i["expect"]["activate"]) for i in items)))
print("strata:", dict(Counter(str(i["heuristic_stratum"]) for i in items)))
print("types:", dict(Counter(i["type"] for i in items)))
