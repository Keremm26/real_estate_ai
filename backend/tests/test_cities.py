"""
Offline tests for the city switch (app/core/cities.py): Turin keeps its exact
dataset, geocoding request, RAG jurisdiction and map centre; Madrid and London
switch all of them. No network: Nominatim calls are intercepted and the
geocode cache is redirected to a temporary file.

Run:
    PYTHONPATH=. python tests/test_cities.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.cities import CITIES, TURIN_LANDMARKS, active_city  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.data import loaders  # noqa: E402
from app.services.llm.agents.regulatory_agent import load_regulatory_context  # noqa: E402
from app.services.map_service import MapService  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _geocode(city: str, place: str):
    """Run get_coordinates for ``place`` in ``city`` against a fake Nominatim;
    returns (result, Nominatim params sent, cache keys written). Place names are
    lower-cased by loaders._clean_place_name before the request."""
    sent = []

    def fake_get(url, params=None, headers=None, timeout=None):
        sent.append(params)
        return _Resp([{"lat": "1.5", "lon": "2.5"}])

    saved = (loaders.requests.get, loaders.GEOCODE_CACHE, loaders.GEOCODE_CACHE_FILE)
    loaders.requests.get = fake_get
    loaders.GEOCODE_CACHE = {}
    loaders.GEOCODE_CACHE_FILE = os.path.join(tempfile.mkdtemp(), "geocode_cache.json")
    loaders._get_coordinates.cache_clear()
    try:
        settings.set_city(city)
        result = loaders.get_coordinates(place)
        return result, sent, list(loaders.GEOCODE_CACHE)
    finally:
        loaders.requests.get, loaders.GEOCODE_CACHE, loaders.GEOCODE_CACHE_FILE = saved
        loaders._get_coordinates.cache_clear()
        settings.set_city("turin")


def test_turin_is_unchanged():
    settings.set_city("turin")
    assert settings.CITY == "turin" and active_city().rag_country == "IT"
    assert settings.dataset_options["full"] == settings.DATASET_FULL
    result, sent, keys = _geocode("turin", "Via Prova")
    assert result == (1.5, 2.5)
    assert sent[0]["q"] == "via prova, Torino, Italia" and sent[0]["viewbox"] == "7.5,45.2,7.8,44.9"
    assert sent[0]["bounded"] == 1 and keys == ["via prova"]          # plain cache key, as before
    result, sent, _ = _geocode("turin", "Mole Antonelliana")
    assert result == TURIN_LANDMARKS["mole antonelliana"] and sent == []   # landmark, no request


def test_london_and_madrid_switch():
    settings.set_city("london")
    try:
        assert settings.dataset_options["full"] == "data/metadata/estates_london.parquet"
    finally:
        settings.set_city("turin")
    result, sent, keys = _geocode("london", "King's Cross")
    assert sent[0]["q"] == "king's cross, London, United Kingdom"
    assert sent[0]["viewbox"] == CITIES["london"].viewbox and keys == ["london|king's cross"]
    _, sent, _ = _geocode("london", "centro")                 # Turin's landmark must not leak
    assert sent and sent[0]["q"].startswith("centro, London")
    _, sent, _ = _geocode("madrid", "Puerta del Sol")
    assert sent[0]["q"] == "puerta del sol, Madrid, España" and sent[0]["viewbox"] == CITIES["madrid"].viewbox


def test_rag_jurisdiction_follows_the_city():
    for city, country in (("turin", "IT"), ("madrid", "ES"), ("london", "UK")):
        settings.set_city(city)
        try:
            _, _, _, trace = load_regulatory_context("student housing", retrieval_mode="none")
            assert trace["country"] == country, (city, trace["country"])
            # explicit callers (the evaluations) keep their own country
            _, _, _, trace = load_regulatory_context("x", retrieval_mode="none", country="IT")
            assert trace["country"] == "IT"
        finally:
            settings.set_city("turin")


def test_map_centre_and_overlays():
    svc = MapService()
    cfg = asyncio.run(svc.get_config())
    assert (cfg["center"]["lat"], cfg["center"]["lng"]) == (settings.DEFAULT_MAP_CENTER_LAT, settings.DEFAULT_MAP_CENTER_LON)
    settings.set_city("london")
    try:
        cfg = asyncio.run(svc.get_config())
        assert (cfg["center"]["lat"], cfg["center"]["lng"]) == CITIES["london"].map_center
        assert asyncio.run(svc.get_overlay("omi")) is None        # Turin's OMI file is not London's
    finally:
        settings.set_city("turin")


def test_unknown_city_is_rejected():
    try:
        settings.set_city("paris")
    except ValueError:
        return
    raise AssertionError("unknown city accepted")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
