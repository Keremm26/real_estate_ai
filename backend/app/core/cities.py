"""
Cities the pipeline can run on. One process runs one city: ``settings.CITY``
(env ``CITY``, or ``settings.set_city()``) selects the dataset, the regulatory
jurisdiction the RAG retrieves from, the geocoding context and the map centre.

Turin is the original MURENA city and keeps its exact behaviour (dataset from
``DATASET_FULL``, Nominatim box and landmark registry as before). Madrid and
London use the datasets built from the common schema
(app/services/schema_alignment/build_city_dataset.py, docs/city-dataset-schema.md).
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class CityConfig:
    key: str
    name: str                          # display name and geocoding context
    country_name: str                  # geocoding context ("Italia", ...)
    rag_country: str                   # `country` metadata of the city's chunks in the normative store
    dataset: Optional[str]             # None -> settings.DATASET_FULL (Turin)
    map_center: Tuple[float, float]    # (lat, lon)
    viewbox: str                       # Nominatim "lon_min,lat_max,lon_max,lat_min"
    landmarks: Dict[str, Tuple[float, float]] = field(default_factory=dict)


# Turin landmarks resolved without Nominatim (moved from app/data/loaders.py)
TURIN_LANDMARKS = {
    "palazzo nuovo": (45.068846, 7.691295),
    "porta susa": (45.0732, 7.6663),
    "porta nuova": (45.0622, 7.6785),
    "politecnico": (45.0624, 7.6607),
    "università degli studi di torino": (45.0688, 7.6912),
    "mole antonelliana": (45.0677, 7.6930),
    "piazza castello": (45.0710, 7.6856),
    "campus einaudi": (45.0768, 7.7013),
    "parco valentino": (45.0544, 7.6852),
    "biblioteca civica": (45.0664, 7.6781),
    "ospedale molinette": (45.0415, 7.6755),
    "san salvario": (45.057, 7.681),
    "crocetta": (45.060, 7.665),
    "centro": (45.070, 7.686),
}

CITIES: Dict[str, CityConfig] = {
    "turin": CityConfig(
        key="turin", name="Torino", country_name="Italia", rag_country="IT", dataset=None,
        map_center=(45.070860, 7.685588), viewbox="7.5,45.2,7.8,44.9", landmarks=TURIN_LANDMARKS,
    ),
    "madrid": CityConfig(
        key="madrid", name="Madrid", country_name="España", rag_country="ES",
        dataset="data/metadata/estates_madrid.parquet",
        map_center=(40.416775, -3.703790), viewbox="-3.89,40.65,-3.51,40.31",
    ),
    "london": CityConfig(
        key="london", name="London", country_name="United Kingdom", rag_country="UK",
        dataset="data/metadata/estates_london.parquet",
        map_center=(51.507351, -0.127758), viewbox="-0.52,51.70,0.34,51.28",
    ),
}


def active_city() -> CityConfig:
    """The city this process runs on (``settings.CITY``)."""
    from app.core.config import settings
    return CITIES[settings.CITY]
