"""
Data loading functions for datasets, GeoJSON, and static files.

This module handles:
- Loading and merging CSV datasets
- Fetching coordinates from Nominatim API
- Loading GeoJSON data for map overlays
- Loading static data (metro stations)
- Reading APE XML files safely
"""

from functools import lru_cache
import json
import os
import re
import threading

import pandas as pd
import requests

from app.utils.decorators import retry_with_backoff
from app.utils.logger import logger

# Global cache and lock
# Use absolute path based on this file's directory
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
GEOCODE_CACHE_FILE = os.path.join(DATA_DIR, "geocode_cache.json")
GEOCODE_CACHE = {}
CACHE_LOCK = threading.Lock()

# Load cache on module load
if os.path.exists(GEOCODE_CACHE_FILE):
    try:
        with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
            GEOCODE_CACHE = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load geocode cache: {e}")

# Geocoding context (city name, country, Nominatim box, landmark registry) comes
# from the active city (app/core/cities.py). Turin's landmarks keep their old
# name here for the callers that import it.
from app.core.cities import TURIN_LANDMARKS as LOCAL_LANDMARKS  # noqa: E402

def _clean_place_name(name: str) -> str:
    """Pre-processes place name for better geocoding results."""
    if not name: return ""
    
    # Remove common prefixes from LLM extraction
    junk = [
        "vicino a", "vicino", "presso", "nei pressi di", "in zona", 
        "area di", "distretto di", "intorno a", "davanti a", "fronte"
    ]
    
    clean = name.lower().strip()
    for j in junk:
        if clean.startswith(j):
            clean = clean[len(j):].strip()
            # Handle Italian articles: "vicino al", "vicino alla" ...
            for article in [" l'", " lo ", " la ", " il ", " a ", " di "]:
                if clean.startswith(article.strip()):
                    clean = clean[len(article.strip()):].strip()
            break
            
    return clean.strip()


def load_and_merge_data(file_path):
    """
    Load CSV or Parquet data with error handling.
    Optimized for performance.

    Args:
        file_path: Path to CSV or Parquet file

    Returns:
        pd.DataFrame: Loaded data, or None if error
    """
    try:
        if file_path.endswith(".parquet"):
            logger.info(f"Loading Parquet file: {file_path}")
            pd_data = pd.read_parquet(file_path)
            # Ensure 'id' is string if it exists
            if "id" in pd_data.columns:
                pd_data["id"] = pd_data["id"].astype(str)
        else:
            logger.info(f"Loading CSV file: {file_path}")
            # Use engine='c' for speed, low_memory=False to avoid mixed type warnings
            # Ensure 'id' is read as string to prevent issues with leading zeros or large numbers
            pd_data = pd.read_csv(
                file_path,
                sep=";",
                encoding="utf-8",
                engine="c",
                low_memory=False,
                dtype={"id": str},
            )
    except pd.errors.ParserError as e:
        logger.error(f"CSV Parsing Error: {e}")
        try:
            pd_data = pd.read_csv(
                file_path,
                sep=";",
                encoding="utf-8",
                quoting=1,
                on_bad_lines="warn",
                dtype={"id": str},
            )
        except Exception as e2:
            logger.critical(f"CRITICAL CSV Load Error: {e2}")
            return None
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Generic load error: {e}")
        return None

    return pd_data


def get_coordinates(place_name):
    """
    Fetch coordinates for a place using Nominatim API (cached), in the context
    of the active city.

    Args:
        place_name: Name of the place to geocode

    Returns:
        tuple: (latitude, longitude) or (None, None) if not found
    """
    from app.core.cities import active_city
    return _get_coordinates(place_name, active_city().key)


@lru_cache(maxsize=128)
@retry_with_backoff(
    retries=3, initial_delay=1.0, exceptions=(requests.RequestException,)
)
def _get_coordinates(place_name, city_key):
    from app.core.cities import CITIES
    city = CITIES[city_key]

    # 1. Cleaning
    place_name = _clean_place_name(place_name)
    if not place_name: return None, None

    # 2. Local landmarks registry (Turin only)
    clean_name = place_name.lower().strip()
    for landmark, coords in city.landmarks.items():
        if landmark in clean_name or (len(clean_name) > 3 and clean_name in landmark):
            return coords

    # 3. Global file-based cache (Turin keeps its original plain keys)
    cache_key = place_name if city_key == "turin" else f"{city_key}|{place_name}"
    if cache_key in GEOCODE_CACHE:
        return GEOCODE_CACHE[cache_key]

    # Geocoding attempts hierarchy
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "DashApp/1.0"}
    
    # Config descriptions
    # 1. Bounded search (strict)
    # 2. Unbounded search with local context
    queries = [
        { "q": f"{place_name}, {city.name}, {city.country_name}", "bounded": 1 },
        { "q": f"{place_name}, {city.name}, {city.country_name}", "bounded": 0 },
        { "q": f"{place_name}, {city.country_name}", "bounded": 0 }
    ]

    for config in queries:
        params = {
            "q": config["q"],
            "format": "json",
            "limit": 1,
            "viewbox": city.viewbox,
            "bounded": config["bounded"],
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                
                # Success! Update cache and return
                with CACHE_LOCK:
                    GEOCODE_CACHE[cache_key] = (lat, lon)
                    try:
                        os.makedirs(os.path.dirname(GEOCODE_CACHE_FILE), exist_ok=True)
                        with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(GEOCODE_CACHE, f, indent=2)
                    except Exception as e:
                        logger.warning(f"Failed to write geocode cache: {e}")
                return lat, lon
        except Exception as e:
            logger.debug(f"Geocode attempt failed for {config['q']}: {e}")
            continue

    return None, None



def load_geojson_data(path):
    """
    Carica dati GeoJSON da un file, gestendo eventuali errori.

    Args:
        path: Path to GeoJSON file

    Returns:
        dict: GeoJSON data, or None if error
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"GeoJSON file not found: {path}. Overlay unavailable.")
        return None
    except json.JSONDecodeError:
        logger.error(f"JSON Decode Error for file: {path}.")
        return None


def load_static_data():
    """
    Carica i dati che non dipendono dalla query (es. stazioni metro).

    Returns:
        pd.DataFrame: Station data
    """
    try:
        from app.core.config import settings
        return pd.read_csv(settings.STATIONS_CSV)
    except FileNotFoundError:
        logger.warning("Station file not found.")
        return pd.DataFrame(columns=["Lat", "Lon", "Linea", "Nome"])


def read_xml_text_safe(full_path: str) -> str:
    """
    Read XML file with proper encoding detection and BOM handling.

    Args:
        full_path: Path to XML file

    Returns:
        str: XML content as string
    """
    with open(full_path, "rb") as f:
        raw = f.read()

    if not raw:
        return ""

    # Handle BOM (Byte Order Mark)
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", errors="ignore")
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16", errors="ignore")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="ignore")

    # Clean any garbage before XML declaration
    text = re.sub(r"^.*?<\?xml", "<?xml", text, flags=re.DOTALL)
    return text


def load_ape_detailed_data(file_path):
    """
    Load detailed APE data from CSV or Parquet and calculate scores.

    Args:
        file_path: Path to CSV or Parquet file

    Returns:
        pd.DataFrame: Loaded data with scores, or None if error
    """
    if not os.path.exists(file_path):
        logger.warning(f"APE detailed file not found: {file_path}")
        return None

    try:
        if file_path.endswith(".parquet"):
            pd_data = pd.read_parquet(file_path)
        else:
            # Try comma first as suggested by the header format
            pd_data = pd.read_csv(
                file_path, sep=",", encoding="utf-8", engine="c", low_memory=False
            )

            # If only one column, it might be semicolon separated
            if len(pd_data.columns) <= 1:
                pd_data = pd.read_csv(
                    file_path, sep=";", encoding="utf-8", engine="c", low_memory=False
                )

        # Calculate scores
        from app.data.processors import calculate_ape_score

        pd_data = calculate_ape_score(pd_data)

        return pd_data
    except Exception as e:
        logger.error(f"Error loading APE detailed data: {e}")
        return None
