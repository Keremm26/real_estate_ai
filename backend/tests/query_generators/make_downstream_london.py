"""Generate downstream_queries_london.json (the London downstream set of the
RAG study). Every expected range is computed from the value_basis formulas
below, never typed by hand; tests/validate_query_files.py recomputes them
independently. Edit the items here and regenerate:
    python tests/query_generators/make_downstream_london.py
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "app" / "services" / "llm" / "rag" / "eval" / "downstream_queries_london.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_rules import (hmo_nat, hmo_nat_unknown, lp_range, stratum, th_kitchen,  # noqa: E402,F401
                        th_range, th_rooms, th_unknown)

HMO = {"doc_key": "hmo_mandatory_conditions_2018", "article_ref": "1A"}
TH = {"doc_key": "th_prs_property_standards", "article_ref": "*"}
LP = {"doc_key": "london_plan_2021", "article_ref": "*"}
THLP = {"doc_key": "th_local_plan_2031", "article_ref": "*"}
GPDO_MA = {"doc_key": "gpdo_2015_part3", "article_ref": "Class MA"}
ART4 = {"doc_key": "th_article4_class_e", "article_ref": "*"}
ADM = {"doc_key": "approved_doc_m_vol1", "article_ref": "*"}
UCO = {"doc_key": "use_classes_1987", "article_ref": "*"}
TH_WINS = {"winner": "th_prs_property_standards", "loser": "hmo_mandatory_conditions_2018",
           "rule": "higher_local_standard",
           "winner_aliases": ["Private Rented Housing Property Standards", "Property Standards"]}

items = []


def add(id_, type_, tier, use_case, en, capacity, user_values, activate, rng, basis, sources, covers, note,
        precedence=None):
    persons = (capacity or {}).get("persons") or (capacity or {}).get("beds")
    vmin, vmax = rng if rng else (None, None)
    item = {
        "id": id_, "type": type_, "tier": tier, "use_case": use_case, "queries": {"en": en},
        "capacity": capacity, "user_values": user_values,
        "expect": {"activate": activate,
                   "target_column": "surface_area" if activate is not False else None,
                   "operator": ">=" if activate is not False else None,
                   "value_min": vmin, "value_max": vmax, "basis": basis},
        "heuristic_stratum": stratum(vmin, persons) if activate is True else None,
        "sources": sources,
    }
    if precedence:
        item["precedence"] = precedence
    item.update({"covers": covers, "note": note})
    items.append(item)


# ---------------------------------------------------------------- HMO, national
r = hmo_nat_unknown(5)
add("DQ-L01", "dimensioning", "national", "general",
    "I'm looking for a house in London to let as a licensed HMO for 5 tenants.",
    {"persons": 5}, [], True, r, f"N=5 adults, room mix not stated: [5.11 N, 1.25 x 6.51 N] = {r}",
    [HMO], ["hmo", "mix-unknown"], "Baseline HMO sizing: SI 2018/616 para 1A sleeping-room minima.")
r = hmo_nat_unknown(8)
add("DQ-L02", "dimensioning", "national", "general",
    "We need a property for a shared house in multiple occupation with 8 occupiers.",
    {"persons": 8}, [], True, r, f"N=8, room mix not stated: [5.11 N, 1.25 x 6.51 N] = {r}",
    [HMO], ["hmo", "mid-capacity", "occupiers-wording"], "Same rule, larger capacity and statutory wording.")
h = hmo_nat(s=6)
add("DQ-L03", "room_mix", "national", "general",
    "An HMO with six single bedrooms, one person per room.",
    {"persons": 6, "single_rooms": 6, "double_rooms": 0}, [], True, (h, round(1.25 * h, 2)),
    f"6 single rooms x 6.51 = {h}; range [H, 1.25 H]", [HMO], ["hmo", "single-room-rate", "capacity-in-rooms"],
    "Capacity given as rooms: the one-person minimum applies to every room.")
h = hmo_nat(s=3, d=2)
add("DQ-L04", "room_mix", "national", "general",
    "House share for 7 people: 3 single rooms and 2 double rooms shared by couples.",
    {"persons": 7, "single_rooms": 3, "double_rooms": 2}, [], True, (h, round(1.25 * h, 2)),
    f"3 x 6.51 + 2 x 10.22 = {h}; range [H, 1.25 H]", [HMO], ["hmo", "mixed-rooms", "double-room-rate"],
    "Sum of applicable parts: single and double rates together.")
h = hmo_nat(s=6)
add("DQ-L05", "dimensioning", "national", "student_housing",
    "A shared house in London for 6 university students, each with their own bedroom.",
    {"persons": 6, "single_rooms": 6, "double_rooms": 0}, [], True, (h, round(1.25 * h, 2)),
    f"Student shared house = HMO: 6 x 6.51 = {h}; range [H, 1.25 H]", [HMO],
    ["student", "hmo", "single-room-rate"],
    "The English student room-size rule lives in HMO licensing, not in a student-housing standard (gold uk01).")
h = hmo_nat(d=3)
add("DQ-L06", "room_mix", "national", "student_housing",
    "Student house for 6 students sharing three double bedrooms.",
    {"persons": 6, "single_rooms": 0, "double_rooms": 3}, [], True, (h, round(1.25 * h, 2)),
    f"3 double rooms x 10.22 = {h}; range [H, 1.25 H]", [HMO], ["student", "hmo", "double-room-rate"],
    "Two persons per room: the 10.22 m2 rate per room, not 2 x 6.51. Six occupiers keep it a licensable HMO.")
r = hmo_nat_unknown(6)
add("DQ-L07", "long_query", "national", "student_housing",
    "Hi! My brother and I are starting a small letting business. We'd like to buy a terraced house near "
    "UCL in Camden and rent it to 6 students. "
    "Budget is tight, we care about transport links and a garden would be nice. What should we look for?",
    {"persons": 6}, [], True, r, f"N=6, mix not stated, national rule (Camden: no borough standard in the corpus): {r}",
    [HMO], ["student", "hmo", "long-query", "location-noise"],
    "Long chatty request with location and budget noise; no borough standard for Camden in the corpus, so the national minima apply.")
r = hmo_nat_unknown(5)
add("DQ-L08", "typos", "national", "general",
    "Lisenced HMO for 5 tenents somwhere in north London.",
    {"persons": 5}, [], True, r, f"N=5, mix not stated: {r}", [HMO], ["hmo", "typos"],
    "Spelling errors must not break routing or retrieval.")
h = hmo_nat(s=12)
add("DQ-L09", "number_in_words", "national", "general",
    "An HMO for twelve tenants, all in single rooms.",
    {"persons": 12, "single_rooms": 12, "double_rooms": 0}, [], True, (h, round(1.25 * h, 2)),
    f"12 x 6.51 = {h}; range [H, 1.25 H]", [HMO], ["hmo", "number-in-words"], "Capacity written in words.")
h = hmo_nat(s=8)
add("DQ-L10", "self_correction", "national", "general",
    "HMO for 6 tenants... actually make that 8, all in single rooms.",
    {"persons": 8, "single_rooms": 8, "double_rooms": 0}, [6], True, (h, round(1.25 * h, 2)),
    f"Corrected capacity 8 x 6.51 = {h}; range [H, 1.25 H]", [HMO], ["hmo", "self-correction"],
    "The corrected figure (8) governs; 6 x 6.51 = 39.06 is outside the range.")
h = hmo_nat(s=5)
add("DQ-L11", "negation", "national", "general",
    "A house for 5 working tenants, not students, each in a single room.",
    {"persons": 5, "single_rooms": 5, "double_rooms": 0}, [], True, (h, round(1.25 * h, 2)),
    f"5 x 6.51 = {h}; range [H, 1.25 H]", [HMO], ["hmo", "negation", "not-student"],
    "Negated student use: still an HMO, same national minima.")
h = hmo_nat(s=6)
add("DQ-L12", "echo_trap", "national", "general",
    "HMO for 6 tenants in single bedrooms; I'd like every bedroom to be at least 15 square metres.",
    {"persons": 6, "single_rooms": 6, "double_rooms": 0}, [15, 90], True, (h, round(1.25 * h, 2)),
    f"6 x 6.51 = {h}; the user's 15 m2 per room (90 m2) is a preference, not a regulation", [HMO],
    ["hmo", "echo-with-capacity", "user-bound-above-regulation"],
    "The value must come from SI 2018/616, not from the user's 15 (or 6 x 15 = 90).")

# ------------------------------------------------- Tower Hamlets (precedence)
r = th_range(s=6)
add("DQ-L13", "precedence", "municipal", "general",
    "Licensed HMO in Whitechapel, Tower Hamlets, for 6 tenants in single bedrooms with a shared kitchen.",
    {"persons": 6, "single_rooms": 6, "double_rooms": 0}, [], True, r,
    f"TH standard: 6 x 8.5 = {th_rooms(s=6)} (+ shared kitchen 9.5 for 6-7 sharers), range [T, 1.25 (T + K)] = {r}. "
    f"The national rule would give {hmo_nat(s=6)} (outside the range).",
    [TH, HMO], ["precedence", "borough-standard", "shared-kitchen"],
    "The borough's property standard (8.5 m2) is higher than the national licence minimum (6.51 m2) and applies "
    "in Tower Hamlets.", TH_WINS)
r = th_range(d=2)
add("DQ-L14", "precedence", "municipal", "general",
    "HMO in Bethnal Green for 4 people living as two couples in two double bedrooms.",
    {"persons": 4, "single_rooms": 0, "double_rooms": 2}, [], True, r,
    f"TH: 2 x 13 = {th_rooms(d=2)} (+ kitchen 7.5 for 4-5 sharers), range {r}. The national licence conditions "
    f"cover licensed HMOs of 5+ persons (and would give only {hmo_nat(d=2)}); the borough standard covers this HMO.",
    [TH, HMO], ["precedence", "borough-standard", "double-room-rate"],
    "Bethnal Green is in Tower Hamlets: the location alone triggers the borough standard.", TH_WINS)
t = th_rooms(s=5, bedsit=True)
add("DQ-L15", "precedence", "municipal", "general",
    "Five bedsits in Tower Hamlets, each with its own kitchen facilities inside the room, one person per bedsit.",
    {"persons": 5, "single_rooms": 5, "double_rooms": 0}, [], True, (t, round(1.25 * t, 2)),
    f"TH bedsits with kitchen within the room: 5 x 13 = {t}; range [T, 1.25 T]. National: {hmo_nat(s=5)}.",
    [TH, HMO], ["precedence", "bedsit", "kitchen-in-room"],
    "Bedsit rate (13 m2) instead of the bedroom rate (8.5 m2).", TH_WINS)
r = th_unknown(10)
add("DQ-L16", "dimensioning", "municipal", "general",
    "We want to open a house in multiple occupation for 10 tenants in Poplar, Tower Hamlets.",
    {"persons": 10}, [], True, r,
    f"TH, mix not stated: [6.5 N, 1.25 (8.5 N + kitchen 11.5)] = {r}. National: {hmo_nat_unknown(10)}.",
    [TH, HMO], ["borough-standard", "mix-unknown", "large-hmo"],
    "Large HMO in the borough; at most 2 per room and one kitchen set per 5 persons.", TH_WINS)

# ------------------------------------------------- London Plan Table 3.1
r = lp_range(2, 4)
add("DQ-L17", "general_dwelling", "regional", "general",
    "I want to convert a shop into a two-bedroom flat for four people.",
    {"persons": 4, "bedrooms": 2}, [], True, r, f"Table 3.1 2b4p: 70 (1 storey) .. 79 (2 storeys) = {r}",
    [LP], ["dwelling", "change-of-use", "space-standard"],
    "Table 3.1 applies to new dwellings including conversions and changes of use.")
r = lp_range(1, 2)
add("DQ-L18", "general_dwelling", "regional", "general",
    "Looking for a property to turn into a one-bedroom flat for a couple.",
    {"persons": 2, "bedrooms": 1}, [], True, r, f"Table 3.1 1b2p: 50 .. 58 = {r}", [LP],
    ["dwelling", "couple"], "Persons inferred from 'a couple'.")
r = lp_range(1, 1, shower_studio=True)
add("DQ-L19", "studio_minimum", "regional", "general",
    "A studio flat for one person with a shower room instead of a bathroom.",
    {"persons": 1, "bedrooms": 1, "shower_room": True}, [], True, r,
    f"Table 3.1 1b1p: 39, reduced to 37 with a shower room; range [37, 1.25 x 39] = {r}", [LP],
    ["studio", "shower-room-reduction"], "The footnoted 37 m2 reduction applies.")
r = lp_range(3, 5)
add("DQ-L20", "general_dwelling", "regional", "general",
    "A three-bedroom family home for five people in Lewisham.",
    {"persons": 5, "bedrooms": 3}, [], True, r, f"Table 3.1 3b5p: 86 / 93 / 99 by storeys = {r}", [LP],
    ["dwelling", "family", "storeys-unknown"], "Storeys not stated: any storey count is legal.")
r = lp_range(4, 6, storeys=2)
add("DQ-L21", "general_dwelling", "regional", "general",
    "A two-storey house with four bedrooms for six people.",
    {"persons": 6, "bedrooms": 4, "storeys": 2}, [], True, r, f"Table 3.1 4b6p, 2 storeys: 106; range [G, 1.25 G] = {r}", [LP],
    ["dwelling", "storeys-stated"], "Storeys stated: the 2-storey column applies.")
r = lp_range(2, 3)
add("DQ-L22", "general_dwelling", "regional", "general",
    "We are converting an office building into flats; I need units with two bedrooms for three people.",
    {"persons": 3, "bedrooms": 2}, [], True, r, f"Table 3.1 2b3p: 61 .. 70 = {r}", [LP, GPDO_MA],
    ["dwelling", "office-to-residential", "per-unit"],
    "Per-unit standard for each new flat; Class MA is procedural and sets no area.")
r = lp_range(2, 4)
add("DQ-L23", "echo_trap", "regional", "general",
    "Two-bedroom flat for four people, at least 100 square metres.",
    {"persons": 4, "bedrooms": 2}, [100], True, r,
    f"Table 3.1 2b4p = {r}; the user's 100 m2 is a preference", [LP], ["dwelling", "echo-guard"],
    "The regulatory value is 70-79, not the user's 100.")
h = hmo_nat(s=5)
add("DQ-L24", "code_switching", "national", "student_housing",
    "Cerco una casa a Londra per 5 studenti, each one in a single room, da affittare come HMO.",
    {"persons": 5, "single_rooms": 5, "double_rooms": 0}, [], True, (h, round(1.25 * h, 2)),
    f"5 x 6.51 = {h}; range [H, 1.25 H]", [HMO], ["student", "hmo", "code-switching"],
    "Italian-English mix: the language must not change the jurisdiction (London, England).")

# --------------------------------------------------------------- either
r = hmo_nat_unknown(200)
add("DQ-L25", "ambiguous_use", "regional", "student_housing",
    "Purpose-built student accommodation block for 200 students.",
    {"persons": 200}, [], "either", r,
    "No room-size standard for PBSA in the corpus (London Plan H15 covers affordability and nominations); if the "
    f"HMO minima are applied: {r}", [LP, HMO], ["pbsa", "no-space-standard"],
    "Declining is acceptable; if the agent activates, the value must be grounded.")
r = hmo_nat_unknown(4)
add("DQ-L26", "ambiguous_use", "national", "general",
    "A house share for 4 young professionals.",
    {"persons": 4}, [], "either", r,
    "Mandatory licensing (and its conditions) covers HMOs of 5 or more persons, a threshold not in the corpus; "
    f"if the SI 2018/616 minima are applied: {r}", [HMO], ["hmo", "licensing-threshold"],
    "Below the mandatory-licensing size: activation and decline are both defensible.")
r = hmo_nat_unknown(6)
add("DQ-L27", "ambiguous_use", "national", "general",
    "Supported shared housing for 6 elderly residents.",
    {"persons": 6}, [], "either", r,
    f"No senior-housing standard in the corpus; if treated as an HMO: {r}", [HMO], ["senior", "care"],
    "Care use: declining is acceptable.")

# --------------------------------------------------------------- decline
NONE = (None, None)
add("DQ-L28", "no_capacity", None, "general", "What room sizes does a licensed HMO need?",
    None, [], False, None, "Per-room standards; without a capacity there is no building-level threshold", [HMO],
    ["no-capacity", "question-form"], "Retrieval should still find para 1A, but extraction should decline.")
add("DQ-L29", "no_capacity", None, "general", "I want to turn a house into an HMO.",
    None, [], False, None, "No number of occupiers or rooms", [HMO], ["no-capacity"], "")
add("DQ-L30", "out_of_corpus", None, "general", "A hotel with 40 rooms near King's Cross.",
    {"rooms": 40}, [], False, None, "No hotel standard in the corpus", [], ["hotel", "out-of-corpus"],
    "A room count is present but no rule covers hotels.")
add("DQ-L31", "negative", None, "general", "Office of at least 300 square metres in Shoreditch.",
    None, [300], False, None, "Office use: no regulatory surface threshold", [], ["office", "echo-guard"],
    "Negative control with a user surface that must not be echoed.")
add("DQ-L32", "negative", None, "general", "A flat with an EPC rating of B or better near Canary Wharf.",
    None, [], False, None, "Energy preference, no regulation involved", [], ["energy", "negative"], "")
add("DQ-L33", "procedural", "national", "general",
    "Can I convert a commercial building into flats without a full planning application?",
    None, [], False, None, "GPDO Class MA is a prior-approval route: procedural, no area threshold", [GPDO_MA],
    ["permitted-development", "procedural", "temporal"],
    "The current right is Class MA (the superseded Class O text is also in the store).")
add("DQ-L34", "procedural", "municipal", "general",
    "Converting a shop to residential in Tower Hamlets under permitted development.",
    None, [], False, None, "The Article 4 direction removes Class MA in parts of the borough: procedural",
    [ART4, GPDO_MA], ["article-4", "procedural", "borough"], "")
add("DQ-L35", "non_surface_rule", "municipal", "student_housing",
    "Student accommodation in Tower Hamlets with wheelchair-accessible rooms.",
    None, [], False, None, "TH Local Plan: 5% of student rooms wheelchair accessible; no surface threshold",
    [THLP], ["student", "accessibility-share", "non-surface"], "A share of rooms, not an area.")
add("DQ-L36", "non_surface_rule", "national", "general",
    "A wheelchair-user flat to category M4(3): what door widths does it need?",
    None, [], False, None, "Approved Document M sets widths and turning spaces, not floor areas", [ADM],
    ["accessibility", "widths", "non-surface"], "")
add("DQ-L37", "non_surface_rule", "municipal", "general",
    "An HMO in a loft conversion in Tower Hamlets with sloping ceilings.",
    None, [], False, None, "TH sets a 2.14 m ceiling height over 75% of the floor; no capacity, no surface threshold",
    [TH], ["height-rule", "borough"], "Height rule exists but the dataset has no height column.")
add("DQ-L38", "out_of_corpus", None, "general", "A day nursery for 30 children in Hackney.",
    {"persons": 30}, [], False, None, "No nursery standard in the corpus", [], ["childcare", "out-of-corpus"], "")
add("DQ-L39", "negative", None, "general", "Warehouse for logistics of about 2000 square metres in Park Royal.",
    None, [2000], False, None, "Industrial use, no regulatory area", [], ["industrial", "echo-guard"], "")
add("DQ-L40", "procedural", "national", "general",
    "Which use class is a small house in multiple occupation?",
    None, [], False, None, "Use Classes Order C4: a classification, no area threshold", [UCO],
    ["use-class", "question-form"], "")

FAMILY = {**{f"DQ-L{i:02d}": "hmo_national" for i in list(range(1, 13)) + [24, 25, 26, 27]},
          "DQ-L13": "hmo_th", "DQ-L14": "hmo_th", "DQ-L16": "hmo_th", "DQ-L15": "hmo_th_bedsit",
          **{f"DQ-L{i:02d}": "lp_dwelling" for i in range(17, 24)}}
for it in items:
    fam = FAMILY.get(it["id"])
    rebuilt = {}
    for k, v in it.items():
        rebuilt[k] = v
        if k == "expect":
            rebuilt["rule_family"] = fam
    it.clear(); it.update(rebuilt)

meta = {
    "description": "Downstream evaluation of the regulatory agent with RAG retrieval (London). Same design as the "
                   "Turin set: each item is one production-shaped request; the agent must either produce a "
                   "document-grounded building-level surface threshold (surface_area >= X) or decline (found: false).",
    "schema": "downstream-v2 (see downstream_queries_turin.json). One query per language: queries: {en}. "
              "precedence.rule in {lex_specialis, delegated_derogation, higher_local_standard}; precedence."
              "winner_aliases = names that identify the winner in a citation when its doc_name carries no number. "
              "rule_family in {hmo_national, hmo_th, hmo_th_bedsit, lp_dwelling} names the value_basis formula the "
              "range is computed from (null when no rule applies). "
              "heuristic_stratum = whether value_min is above the building agent's ~10 m2/person capacity "
              "estimate ('law_above_heuristic') or not ('law_at_or_below_heuristic'); null when no rule applies.",
    "country": "UK",
    "corpus": "London slice of the normative store: national (Use Classes Order 1987, GPDO 2015 Sch. 2 Part 3 current "
              "+ as at 1 Jan 2021, HMO Mandatory Conditions 2018 SI 2018/616, Approved Document M vol. 1), regional "
              "(London Plan 2021), municipal (Tower Hamlets Local Plan 2031, Article 4 Direction Class E->C3, Private "
              "Rented Housing Property Standards).",
    "value_basis": {
        "hmo_national": "SI 2018/616 para 1A(2): sleeping room for one person aged over 10 >= 6.51 m2, for two "
                        ">= 10.22 m2 (5.11 m2 per person). Building level = sum of sleeping rooms: H = 6.51 s + "
                        "10.22 d. Known room mix: [H, 1.25 H]; mix not stated: [5.11 N, 1.25 x 6.51 N].",
        "hmo_tower_hamlets": "TH Private Rented Housing Property Standards (HMOs in the borough): sleeping room with "
                             "kitchen facilities in a separate room 8.5 m2 (one person) / 13 m2 (two); with kitchen "
                             "facilities within the room 13 / 18 m2; at most two per room. Shared kitchen by sharers: "
                             "<=3 5.5, 4-5 7.5, 6-7 9.5, 8-10 11.5 m2. T = 8.5 s + 13 d; range [T, 1.25 (T + K)]; "
                             "bedsits [13 s + 18 d, 1.25 x that]; mix not stated [6.5 N, 1.25 (8.5 N + K)]. Higher "
                             "than the national licence minimum and applies in Tower Hamlets (precedence "
                             "higher_local_standard).",
        "london_plan_dwelling": "London Plan 2021 Policy D6 + Table 3.1 (new dwellings incl. conversions and change "
                                "of use): GIA by bedrooms (b), persons (p) and storeys, e.g. 1b2p 50/58, 2b3p 61/70, "
                                "2b4p 70/79, 3b5p 86/93/99, 4b6p 99/106/112; 1b1p 39 (37 with a shower room). Storeys "
                                "not stated: [min, max] over the storey columns; stated: [G, 1.25 G].",
    },
    "metrics": "As the Turin set (regulatory_scoring): activation, value in range, echo rate, source_in_context, "
               "winner_cited (doc identifiers or precedence.winner_aliases), routing. Items with activate 'either' "
               "are scored on grounding only.",
    "caveats": [
        "Author-constructed, not validated by an English planning or housing lawyer: state as a limitation.",
        "Mandatory HMO licensing applies to HMOs of 5 or more persons (Licensing of HMOs (Prescribed Description) "
        "Order 2018, not in the corpus); 4-person items are 'either'.",
        "Purpose-built student accommodation has no room-size standard in the corpus; such items are 'either'.",
        "The 1.25 upper tolerance follows the Turin set (the agent may add shared or circulation space).",
    ],
    "known_retrieval_issues": [
        "Retrieval check 2026-10-12 (rag arm, country UK, use-case router on, no LLM): routing 40/40, expected "
        "source in context 25/35. To be decided later, with generic fixes only (not tuned to these items):",
        "Borough scope: the national-HMO items without a location or in another borough (DQ-L02, L04, L05, L07, "
        "L11, L26, L27) retrieve the Tower Hamlets documents instead of SI 2018/616. The store marks them "
        "'municipal' but not which borough they cover, so London's local tier (33 boroughs, one in the corpus) can "
        "leak to queries outside Tower Hamlets. Turin never had this, being a single municipality.",
        "Dwelling items (DQ-L21, L22, L23) retrieve Approved Document M (accessibility) instead of London Plan "
        "Policy D6 / Table 3.1. The London Plan chunks also carry wrong article refs ('Policy SD10 > ...' on "
        "chapter 3 text): a chunking flaw.",
    ],
}

OUT.write_text(json.dumps({"meta": meta, "queries": items}, indent=2, ensure_ascii=False) + "\n")
from collections import Counter
print("items:", len(items), "| activate:", dict(Counter(str(i["expect"]["activate"]) for i in items)))
print("strata:", dict(Counter(str(i["heuristic_stratum"]) for i in items)))
print("types:", dict(Counter(i["type"] for i in items)))
