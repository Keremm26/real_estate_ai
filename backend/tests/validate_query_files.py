"""
Validate the curated query files of the RAG study.

    python tests/validate_query_files.py        (from backend/, needs the normative store)

downstream_queries_turin.json (regulatory agent alone)
  - unique ids
  - expected ranges recomputed from each item's capacity:
      DM MUR 1256/2021 student housing: single 12.5 / double 9.5 m2 per bed +
      services 5.0 (nuclei integrati 3.0); unknown room mix -> [9.5 N, 17.5 N];
      disability share +10 % on the upper bound; existing buildings -15 % services
      DM Sanita 1975 Art. 2 dwellings: H(N) = 14 min(N,4) + 10 max(N-4,0), [H, 1.25 H]
      studio (Art. 3): 28 m2 one person / 38 m2 two, [m, 1.25 m]
  - every source label matches at least one chunk in the store
benchmark_queries_turin.json (full pipeline)
  - id / lang format, unique ids
  - regulatory expectation consistent with expected / optional agents; slots
    imply their agents (landmark -> location, energy -> energy, services -> proximity)
  - expected ranges recomputed as above from slots.capacity / intended_use
  - feasibility: the slots select at least one dataset row (3 km default radius,
    service index >= 60), except items that test a user-vs-regulation conflict
Exit code 1 when a problem is found.
"""

import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.data.loaders import LOCAL_LANDMARKS  # noqa: E402
from app.services.llm.rag import store  # noqa: E402
from app.services.llm.rag.eval.run import _doc_key_to_name, _hit_matches, _label  # noqa: E402

DOWNSTREAM = backend_dir / "app" / "services" / "llm" / "rag" / "eval" / "downstream_queries_turin.json"
DOWNSTREAM_CITIES = [backend_dir / "app" / "services" / "llm" / "rag" / "eval" / f"downstream_queries_{c}.json"
                     for c in ("london", "madrid")]
HEURISTIC_M2_PER_PERSON = 10.0   # building agent rule 5 capacity estimate
PIPELINE = backend_dir / "tests" / "benchmark_queries_turin.json"
PIPELINE_CITIES = [backend_dir / "tests" / f"benchmark_queries_{c}.json" for c in ("london", "madrid")]
CITY_OF_COUNTRY = {"UK": "london", "ES": "madrid"}
SERVICES = {"education": "educazione", "mobility": "mobilita", "healthcare": "sanita", "sport": "sport",
            "green": "verde", "commercial": "commerciale"}
DEFAULT_RADIUS_KM = 3.0
SERVICE_MIN = 60


def dwelling_h(n: int) -> int:
    """DM Sanita 1975 Art. 2 minimum dwelling surface for n persons."""
    return 14 * min(n, 4) + 10 * max(n - 4, 0)


def student_range(cap, text):
    b, s, d = cap.get("beds"), cap.get("single_beds"), cap.get("double_beds")
    if "nuclei" in text:
        r = (9.5 * b, 15.5 * b)
    elif s is not None and d is not None:
        r = (12.5 * s + 9.5 * d, 17.5 * s + 14.5 * d)
    else:
        r = (9.5 * b, 17.5 * b)
    if "disabilit" in text:
        r = (r[0], math.ceil(r[1] * 1.005 / 10) * 10)
    if ("esistente" in text or "existing" in text) and s:
        r = (12.5 * s, 17.5 * s)
    return r


def _range_problem(qid, lo, hi, exp):
    if exp and (abs(exp[0] - lo) > .5 or abs(exp[1] - hi) > .5):
        return f"{qid} range {lo}-{hi} != {exp}"
    return None


def check_downstream(problems):
    dq = json.loads(DOWNSTREAM.read_text(encoding="utf-8"))["queries"]
    r = store.get_collection().get(include=["metadatas", "documents"])
    hits = [dict(m, document=doc) for m, doc in zip(r["metadatas"], r["documents"])]
    k2n = _doc_key_to_name()
    if len({q["id"] for q in dq}) != len(dq):
        problems.append("downstream: duplicate ids")
    for q in dq:
        e, cap, t = q["expect"], q.get("capacity") or {}, q["type"]
        text = q["queries"]["it"].lower() + " " + q["queries"]["en"].lower()
        exp = None
        if e["activate"] in (True, "either") and e["value_min"] is not None:
            if q["use_case"] == "student_housing" and cap.get("beds"):
                exp = student_range(cap, text)
            elif t in ("general_dwelling", "ambiguous_use") and cap.get("persons"):
                exp = (dwelling_h(cap["persons"]), math.ceil(dwelling_h(cap["persons"]) * 1.25))
            elif t == "studio_minimum":
                m = 28 if cap.get("persons") == 1 else 38
                exp = (m, math.ceil(m * 1.25))
        if exp and (p := _range_problem(q["id"], e["value_min"], e["value_max"], exp)):
            problems.append(p)
        n = cap.get("beds") or cap.get("persons")
        want = None
        if e["activate"] is True and n and e["value_min"] is not None:
            want = ("law_above_heuristic" if e["value_min"] > HEURISTIC_M2_PER_PERSON * n
                    else "law_at_or_below_heuristic")
        if q.get("heuristic_stratum") != want:
            problems.append(f"{q['id']} heuristic_stratum {q.get('heuristic_stratum')} != {want}")
        for src in q["sources"]:
            if not any(_hit_matches(h, _label(src, k2n)) for h in hits):
                problems.append(f"{q['id']} source {src} matches no chunk")
    print("DOWNSTREAM:", len(dq), "items | activate", dict(Counter(str(q["expect"]["activate"]) for q in dq)),
          "| longest it query:", max(len(q["queries"]["it"].split()) for q in dq), "words")


# --- London / Madrid rule families (recomputed independently of the generator) ---
_TH_KITCHEN = ((3, 5.5), (5, 7.5), (7, 9.5), (10, 11.5))
_T31 = {(1, 1): (39,), (1, 2): (50, 58), (2, 3): (61, 70), (2, 4): (70, 79), (3, 4): (74, 84, 90),
        (3, 5): (86, 93, 99), (3, 6): (95, 102, 108), (4, 5): (90, 97, 103), (4, 6): (99, 106, 112),
        (4, 7): (108, 115, 121)}


def city_range(family, cap):
    """Expected [value_min, value_max] for a London / Madrid item (see each file's meta.value_basis)."""
    n, s, d = cap.get("persons"), cap.get("single_rooms"), cap.get("double_rooms")
    known = s is not None and d is not None
    if family == "hmo_national":
        if known:
            h = 6.51 * s + 10.22 * d
            return h, 1.25 * h
        return 5.11 * n, 1.25 * 6.51 * n
    if family in ("hmo_th", "hmo_th_bedsit"):
        k = next(v for lim, v in _TH_KITCHEN if (n or s + 2 * d) <= lim)
        if family == "hmo_th_bedsit":
            t = 13 * s + 18 * d
            return t, 1.25 * t
        if known:
            t = 8.5 * s + 13 * d
            return t, 1.25 * (t + k)
        return 6.5 * n, 1.25 * (8.5 * n + k)
    if family == "lp_dwelling":
        g = _T31[(cap["bedrooms"], cap["persons"])]
        if cap.get("storeys"):
            return g[cap["storeys"] - 1], 1.25 * g[cap["storeys"] - 1]
        if len(g) == 1:
            return (37 if cap.get("shower_room") else 39), 1.25 * 39
        return min(g), max(g)
    if family in ("es_res_comunitaria", "es_dwelling"):     # PGOUM 7.3.4 programme, P(R) = max(40, 29 + 7 R)
        prog = lambda r: max(40, 29 + 7 * r)  # noqa: E731
        rooms = (s + d) if known else cap.get("bedrooms")
        if rooms:
            return prog(rooms), 1.25 * prog(rooms)
        n = cap.get("beds") or n
        if not n:                                            # no capacity: the 7.3.4.1 minimum, whatever the occupancy
            return 40, 1.25 * 40
        return prog(math.ceil(n / 2)), 1.25 * prog(n)
    if family == "es_res_compartida":                       # 7.3.11.3: 20 s + 25 d + 20
        su, du = cap.get("single_units"), cap.get("double_units")
        c = lambda a, b: 20 * a + 25 * b + 20  # noqa: E731
        if su is not None and du is not None:
            return c(su, du), 1.25 * c(su, du)
        return c(n % 2, n // 2), 1.25 * c(n, 0)
    return None


def check_downstream_city(problems, path):
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    dq, country = data["queries"], data["meta"]["country"]
    r = store.get_collection().get(where={"country": country}, include=["metadatas"])
    hits = r["metadatas"]
    k2n = _doc_key_to_name()
    if len({q["id"] for q in dq}) != len(dq):
        problems.append(f"{path.name}: duplicate ids")
    for q in dq:
        e, cap, fam = q["expect"], q.get("capacity") or {}, q.get("rule_family")
        if e["activate"] in (True, "either"):
            exp = city_range(fam, cap) if fam else None
            if exp is None:
                problems.append(f"{q['id']} activates but has no recomputable rule_family")
            elif abs(exp[0] - e["value_min"]) > .01 or abs(exp[1] - e["value_max"]) > .01:
                problems.append(f"{q['id']} range {e['value_min']}-{e['value_max']} != {round(exp[0], 2)}-{round(exp[1], 2)}")
        persons = cap.get("persons") or cap.get("beds")
        want = None
        if e["activate"] is True and persons:
            want = ("law_above_heuristic" if e["value_min"] > HEURISTIC_M2_PER_PERSON * persons
                    else "law_at_or_below_heuristic")
        if q.get("heuristic_stratum") != want:
            problems.append(f"{q['id']} heuristic_stratum {q.get('heuristic_stratum')} != {want}")
        for src in q["sources"]:
            if not any(_hit_matches(h, _label(src, k2n)) for h in hits):
                problems.append(f"{q['id']} source {src} matches no chunk")
        prec = q.get("precedence")
        if prec:
            from app.services.llm.rag.eval.regulatory_scoring import doc_ids
            for role in ("winner", "loser"):
                if prec[role] not in k2n:
                    problems.append(f"{q['id']} precedence {role} {prec[role]} not in the manifest")
            if not doc_ids(k2n.get(prec["winner"], "")) and not prec.get("winner_aliases"):
                problems.append(f"{q['id']} winner has no identifier in its name and no winner_aliases")
    print(f"DOWNSTREAM {path.name}:", len(dq), "items | activate",
          dict(Counter(str(q["expect"]["activate"]) for q in dq)),
          "| strata", dict(Counter(str(q.get("heuristic_stratum")) for q in dq if q["expect"]["activate"] is True)))


def check_pipeline(problems):
    pq = json.loads(PIPELINE.read_text(encoding="utf-8"))["queries"]
    path = settings.DATASET_FULL if os.path.isabs(settings.DATASET_FULL) else str(backend_dir / settings.DATASET_FULL)
    df = pd.read_parquet(path, columns=["tipologia_bene_immobile", "superficie_di_riferimento_mq", "classe_energetica_ape",
                                        "latitudine", "longitudine", *SERVICES.values()])

    def km_from(lat, lon):
        p1, p2 = np.radians(df.latitudine), np.radians(lat)
        dl = np.radians(lon - df.longitudine)
        return 2 * 6371.0 * np.arcsin(np.sqrt(np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2))

    if len({q["id"] for q in pq}) != len(pq):
        problems.append("pipeline: duplicate ids")
    rows = {}
    for q in pq:
        s, rg, text = q["slots"], q["regulatory"], q["query"].lower()
        if not re.fullmatch(r"PQ-\d{2}", q["id"]) or q["lang"] not in ("it", "en", "mixed"):
            problems.append(f"{q['id']} id/lang")
        if rg["activate"] is True and "regulatory" not in q["expected_agents"]:
            problems.append(f"{q['id']} regulatory not expected")
        if rg["activate"] == "either" and "regulatory" not in q["optional_agents"]:
            problems.append(f"{q['id']} either not optional")
        if rg["activate"] is False and "regulatory" in q["expected_agents"] + q["optional_agents"]:
            problems.append(f"{q['id']} regulatory listed")
        for slot, agent in (("landmark", "location"), ("energy_classes", "energy"), ("services", "proximity")):
            if s.get(slot) and agent not in q["expected_agents"]:
                problems.append(f"{q['id']} {slot} without {agent}")
        cap, use, exp = s.get("capacity") or {}, s.get("intended_use"), None
        if rg["activate"] in (True, "either") and rg["value_min"] is not None:
            if use in ("student_residence", "student_flat") and cap.get("beds"):
                exp = student_range(cap, text)
            elif use in ("co_housing", "dwelling", "senior_co_housing") and cap.get("persons"):
                exp = (dwelling_h(cap["persons"]), math.ceil(dwelling_h(cap["persons"]) * 1.25))
            elif use == "studio":
                exp = (28, 38) if ("existing" in text or "esistente" in text) else (38, math.ceil(38 * 1.25))
        if exp and (p := _range_problem(q["id"], rg["value_min"], rg["value_max"], exp)):
            problems.append(p)
        n = cap.get("beds") or cap.get("persons")
        want = None
        if rg["activate"] is True and n and rg["value_min"] is not None:
            want = "law_above_heuristic" if rg["value_min"] > HEURISTIC_M2_PER_PERSON * n else "law_at_or_below_heuristic"
        if q.get("heuristic_stratum") != want:
            problems.append(f"{q['id']} heuristic_stratum {q.get('heuristic_stratum')} != {want}")
        m = pd.Series(True, index=df.index)
        if s.get("typology"):
            m &= df.tipologia_bene_immobile.eq(s["typology"])
        if s.get("landmark"):
            m &= km_from(*LOCAL_LANDMARKS[s["landmark"]]) <= (s.get("radius_km") or DEFAULT_RADIUS_KM)
        sf = s.get("surface") or {}
        lo = max([v for v in (sf.get("min"), rg["value_min"] if rg["activate"] is True else None) if v is not None],
                 default=None)
        if lo is not None:
            m &= df.superficie_di_riferimento_mq >= lo
        if sf.get("max") is not None:
            m &= df.superficie_di_riferimento_mq <= sf["max"]
        if s.get("energy_classes"):
            m &= df.classe_energetica_ape.isin(s["energy_classes"])
        for sv in s.get("services") or []:
            m &= df[SERVICES[sv]] >= SERVICE_MIN
        rows[q["id"]] = int(m.sum())
        if rows[q["id"]] == 0 and "conflict-user-vs-regulation" not in q["covers"]:
            problems.append(f"{q['id']} returns 0 rows")
    print("PIPELINE:", len(pq), "queries | lang", dict(Counter(q["lang"] for q in pq)),
          "| sensitivity", sum("sensitivity" in q["suites"] for q in pq),
          "| regulatory", dict(Counter(str(q["regulatory"]["activate"]) for q in pq)),
          "| landmarks", len({q["slots"].get("landmark") for q in pq} - {None}), f"/ {len(LOCAL_LANDMARKS)}",
          "| smallest candidate pool:", min((v, k) for k, v in rows.items() if v))


def check_pipeline_city(problems, path):
    """London / Madrid pipeline sets: agent consistency, recomputed ranges,
    heuristic_stratum, and feasibility on the city's own dataset."""
    if not path.exists():
        return
    from app.core.cities import CITIES
    data = json.loads(path.read_text(encoding="utf-8"))
    pq, meta = data["queries"], data["meta"]
    city = CITY_OF_COUNTRY[meta["country"]]
    ds = CITIES[city].dataset
    df = pd.read_parquet(backend_dir / ds, columns=["tipologia_bene_immobile", "superficie_di_riferimento_mq",
                                                     "classe_energetica_ape", "latitudine", "longitudine",
                                                     *SERVICES.values()])
    landmarks = meta.get("landmarks") or {}

    def km_from(lat, lon):
        p1, p2 = np.radians(df.latitudine), np.radians(lat)
        dl = np.radians(lon - df.longitudine)
        return 2 * 6371.0 * np.arcsin(np.sqrt(np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2))

    if len({q["id"] for q in pq}) != len(pq):
        problems.append(f"{path.name}: duplicate ids")
    rows = {}
    for q in pq:
        s, rg, cap = q["slots"], q["regulatory"], q["slots"].get("capacity") or {}
        if not re.fullmatch(r"PQ-[LM]\d{2}", q["id"]) or q["lang"] not in ("en", "es", "mixed"):
            problems.append(f"{q['id']} id/lang")
        if rg["activate"] is True and "regulatory" not in q["expected_agents"]:
            problems.append(f"{q['id']} regulatory not expected")
        if rg["activate"] == "either" and "regulatory" not in q["optional_agents"]:
            problems.append(f"{q['id']} either not optional")
        if rg["activate"] is False and "regulatory" in q["expected_agents"] + q["optional_agents"]:
            problems.append(f"{q['id']} regulatory listed")
        for slot, agent in (("landmark", "location"), ("energy_classes", "energy"), ("services", "proximity")):
            if s.get(slot) and agent not in q["expected_agents"]:
                problems.append(f"{q['id']} {slot} without {agent}")
        if rg["activate"] in (True, "either"):
            exp = city_range(q.get("rule_family"), cap) if q.get("rule_family") else None
            if exp is None:
                problems.append(f"{q['id']} activates but has no recomputable rule_family")
            elif abs(exp[0] - rg["value_min"]) > .01 or abs(exp[1] - rg["value_max"]) > .01:
                problems.append(f"{q['id']} range {rg['value_min']}-{rg['value_max']} != {round(exp[0], 2)}-{round(exp[1], 2)}")
        n = cap.get("persons") or cap.get("beds")
        want = None
        if rg["activate"] is True and n:
            want = "law_above_heuristic" if rg["value_min"] > HEURISTIC_M2_PER_PERSON * n else "law_at_or_below_heuristic"
        if q.get("heuristic_stratum") != want:
            problems.append(f"{q['id']} heuristic_stratum {q.get('heuristic_stratum')} != {want}")
        m = pd.Series(True, index=df.index)
        if s.get("typology"):
            m &= df.tipologia_bene_immobile.eq(s["typology"])
        if s.get("landmark"):
            if s["landmark"] not in landmarks:
                problems.append(f"{q['id']} landmark {s['landmark']} not in meta.landmarks")
            else:
                m &= km_from(*landmarks[s["landmark"]]) <= (s.get("radius_km") or DEFAULT_RADIUS_KM)
        sf = s.get("surface") or {}
        lo = max([v for v in (sf.get("min"), rg["value_min"] if rg["activate"] is True else None) if v is not None],
                 default=None)
        if lo is not None:
            m &= df.superficie_di_riferimento_mq >= lo
        if sf.get("max") is not None:
            m &= df.superficie_di_riferimento_mq <= sf["max"]
        if s.get("energy_classes"):
            m &= df.classe_energetica_ape.isin(s["energy_classes"])
        for sv in s.get("services") or []:
            m &= df[SERVICES[sv]] >= SERVICE_MIN
        rows[q["id"]] = int(m.sum())
        if rows[q["id"]] == 0 and "conflict-user-vs-regulation" not in q["covers"]:
            problems.append(f"{q['id']} returns 0 rows")
    small = sorted((v, k) for k, v in rows.items() if v)[:3]
    print(f"PIPELINE {path.name}:", len(pq), "queries | lang", dict(Counter(q["lang"] for q in pq)),
          "| regulatory", dict(Counter(str(q["regulatory"]["activate"]) for q in pq)),
          "| strata", dict(Counter(q.get("heuristic_stratum") for q in pq if q.get("heuristic_stratum"))),
          "| landmarks used", len({q["slots"].get("landmark") for q in pq} - {None}), f"/ {len(landmarks)}",
          "| smallest candidate pools:", small)


def main() -> int:
    problems = []
    check_downstream(problems)
    for path in DOWNSTREAM_CITIES:
        check_downstream_city(problems, path)
    check_pipeline(problems)
    for path in PIPELINE_CITIES:
        check_pipeline_city(problems, path)
    print("PROBLEMS:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
