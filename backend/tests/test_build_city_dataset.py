"""
Offline tests for app/services/schema_alignment/build_city_dataset.py: the
vectorised proximity score equals Turin's get_amenity_scores, the Madrid
parsers and crosswalks, and Turin's unit / whole-building row rules.

Run:
    PYTHONPATH=. python tests/test_build_city_dataset.py
"""

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.schema_alignment import build_city_dataset as b  # noqa: E402

turin = b.turin


def _synthetic_pois(rng):
    """A few amenity types per category around Madrid's centre."""
    cats = {
        "commercial": ["supermarket", "clothes", "jewelry"],
        "education": ["school", "university"],
        "mobility": ["bus_stop", "station"],
        "healthcare": ["pharmacy", "clinic", "hospital"],
        "sport": ["pitch", "swimming_pool"],
        "green": ["park"],
    }
    pois = {}
    for cat, amenities in cats.items():
        pois[cat] = {a: [{"id": int(i), "lat": 40.42 + rng.normal(0, 0.02), "lon": -3.70 + rng.normal(0, 0.02)}
                         for i in range(rng.integers(3, 12))] for a in amenities}
    return pois


def test_proximity_matches_turin():
    rng = np.random.default_rng(0)
    pois = _synthetic_pois(rng)
    path = os.path.join(tempfile.mkdtemp(), "pois.json")
    with open(path, "w") as f:
        json.dump(pois, f)
    os.environ["POI_PATH"] = path
    turin.POOL_POI_TREE = None
    turin.init_amenity_globals()
    lat = np.array([40.42, 40.43, 40.40, 40.60, np.nan])
    lon = np.array([-3.70, -3.69, -3.72, -3.50, np.nan])
    ours = b.proximity_raw_scores(lat, lon, pois)
    for k in range(len(lat)):
        ref = turin.get_amenity_scores(None if np.isnan(lat[k]) else lat[k], None if np.isnan(lon[k]) else lon[k])
        for cat, col in b.POI_CATEGORY_COLUMN.items():
            assert abs(ours.loc[k, col] - ref[cat]) < 1e-9, (k, cat, ours.loc[k, col], ref[cat])
    assert (ours.iloc[4] == 0).all()          # no coordinates -> 0, as in Turin


def test_split_address():
    assert b.split_address("Calle Calamocha 2 2º D") == ("Calle Calamocha", "2")
    assert b.split_address("Avenida de la Armada Española, 14 - Viv. 119") == ("Avenida de la Armada Española", "14")
    assert b.split_address("CL GRAN VIA 48") == ("CL GRAN VIA", "48")
    assert b.split_address("Plaza Mayor") == ("Plaza Mayor", None)
    assert b.split_address("") == (None, None)


def test_madrid_plant_label_scores():
    score = lambda s, f=None: turin.map_impianto_score(b.madrid_plant_label(s, f))  # noqa: E731
    assert score("Bomba de Calor - Caudal Ref. Variable") == 5
    assert score("Equipo expansión directa aire-agua") == 5
    assert score("Sistema de generación alimentado por red de distrito RED1") == 5
    assert score("Caldera Condensación") == 3
    assert score("Caldera CondensaciC3n") == 3          # mis-encoded source spelling
    assert score("Caldera Estándar", "BiomasaPellet") == 3
    assert score("Caldera Estándar", "GasNatural") == 1
    assert score("Efecto Joule") == 1
    assert score(None, None) == 1                       # missing -> 1, as in Turin


def test_sheet_index_and_years():
    idx = b.sheet_index(pd.Series(["VK4659F", "VK2725N", None, "VK4659F"]))
    assert idx == {"VK2725N": 1, "VK4659F": 2}
    years = b.year_text(pd.Series([1956.0, np.nan, 2001.4, 28037, 46]))
    assert years[0] == "1956" and pd.isna(years[1]) and years[2] == "2001"
    assert pd.isna(years[3]) and pd.isna(years[4])        # outside the vocabulary range


def test_london_years():
    assert b.london_year("England and Wales: 1900-1929") == 1915
    assert b.london_year("England and Wales: before 1900") == 1890
    assert b.london_year("2023") == 2023
    assert b.london_year("B") == 1915                     # old letter code
    assert b.london_year("England and Wales: 2012 onwards") == 2016
    assert b.london_year("") is None


def test_split_uk_address():
    assert b.split_uk_address("Flat 3, 51 Dunsmure Road", None, None) == ("Dunsmure Road", "51")
    assert b.split_uk_address("Flat A", "87b Burns Road", None) == ("Burns Road", "87b")
    assert b.split_uk_address("28 Northampton Square", "", "") == ("Northampton Square", "28")
    assert b.split_uk_address("Flat 2", "Marlowe Court", "Lymer Avenue") == ("Marlowe Court", None)


def test_london_envelope_and_plant():
    r = pd.DataFrame({"walls_energy_eff": ["Very Good", "Poor", "Average", None],
                      "roof_energy_eff": ["Good", None, "Good", None],
                      "windows_energy_eff": ["Good", "Very Poor", "Average", None],
                      "floor_energy_eff": [None, None, None, None]})
    labels = b.london_envelope_label(r).tolist()
    assert labels[:3] == ["Good", "Poor", "Neutral"] and pd.isna(labels[3])
    score = lambda d: turin.map_impianto_score(b.london_plant_label(d))  # noqa: E731
    assert score("Air source heat pump, radiators, electric") == 5
    assert score("Community scheme") == 5
    assert score("Boiler and radiators, mains gas") == 1


def _units():
    return pd.DataFrame({
        "id": ["1", "2", "3", "4"],
        "_building": ["P1", "P1", "P2", "P2"],
        "_block": [False, False, True, False],
        "foglio": [7, 7, 8, 8], "particella": [11, 11, 12, 12], "subalterno": [1, 2, 1, 2],
        "superficie_di_riferimento_mq": [60.0, 90.0, 500.0, 70.0],
        "tipologia_bene_immobile": [b.RESIDENTIAL, b.COMMERCIAL, b.RESIDENTIAL, b.RESIDENTIAL],
        "classe_energetica_ape": ["E", "C", "D", "D"],
        "epoca_costruzione": ["1960", "1960", "1975", "1975"],
        "lista_file_ape": ["['a']", "['b']", "['c']", "['d']"],
        "list_file_ape_filtered": ["['a']", "['b']", "['c']", "['d']"],
        "list_file_ape_filtered_parsed": ["['a']", "['b']", "['c']", "['d']"],
        "ape_score_impianto": [1, 5, 3, 1], "ape_score_involucro": [1, 3, 3, 3], "ape_score_rinnovabili": [1, 1, 5, 1],
        "epglnren_ape": [200.0, 120.0, 150.0, 160.0], "epglren_ape": [np.nan] * 4, "emissioni_co2": [40.0, 25.0, 30.0, 31.0],
        "zona_omi": ["Sol", "Sol", "Goya", "Goya"],
    })


def test_building_rows_follow_turin():
    meta = b.building_rows(_units())
    assert len(meta) == 1                                 # P2 has a whole-building certificate
    m = meta.iloc[0]
    assert m["id"] == "M00001" and bool(m["meta_immobile"]) and m["numero_immobili_per_catasto"] == 2
    assert m["superficie_di_riferimento_mq"] == 150.0 and m["subalterno"] == 0
    assert m["tipologia_bene_immobile"] == b.COMMERCIAL   # largest surface wins (90 > 60)
    assert m["classe_energetica_ape"] == "C"              # class of the largest surface
    assert m["ape_score_impianto"] == 5 and m["ape_score_classe"] == 3
    assert m["ape_score_total"] == 3 + 5 + 3 + 1
    assert m["epglnren_ape"] == 120.0 and m["id_list"] == "[1, 2]" and m["lista_file_ape"] == "['a', 'b']"


def test_latest_per_unit():
    rows = pd.DataFrame({"_unit": ["A", "A", "B"],
                         "data_decorrenza": pd.to_datetime(["2024-01-01", "2025-06-01", "2023-01-01"]),
                         "v": [1, 2, 3]})
    out = b.latest_per_unit(rows)
    assert sorted(out["v"].tolist()) == [2, 3]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
