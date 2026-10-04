from typing import Dict, List, Optional, Set

# Score legend (1-5) for energy and proximity
SCORE_LEGEND = """SCORE LEGEND (all on 1-5 scale, where 5=excellent):

Energy (energy efficiency):
- energy_score_class: Energy class (5=A1-A4, 3=B-E, 1=F-G)
- energy_score_plant: Thermal plant quality (5=Heat pump/District heating, 3=Condensation/Biomass, 1=Traditional)
- energy_score_envelope: Building insulation (5=Excellent, 3=Medium, 1=Poor/None)
- energy_score_renewables: Renewable energy sources present (5=Yes, 1=No)
- energy_score_total: Overall average of energy scores

Proximity (proximity services):
- healthcare: Proximity to healthcare services (Hospitals, pharmacies)
- mobility: Public transport accessibility (Metro, bus, stations)
- green: Presence of green areas (Parks, gardens)
- sport: Proximity to sports facilities (Gyms, pools)
- commercial: Commercial services (Shops, supermarkets)
- education: Schools and education (Schools, universities)
"""

# Specific legend for Energy agent
ENERGY_SCORE_LEGEND = """ENERGY SCORE LEGEND (1-5 scale, where 5=excellent):

- energy_score_class: Energy class (5=A1-A4, 3=B-E, 1=F-G)
- energy_score_plant: Thermal plant quality (5=Heat pump/District heating, 3=Condensation/Biomass, 1=Traditional)
- energy_score_envelope: Building insulation (5=Excellent, 3=Medium, 1=Poor/None)
- energy_score_renewables: Renewable energy sources present (5=Yes, 1=No)
- energy_score_total: Overall average of energy scores
"""

# Columns for Location agent
LOCATION_AGENT_COLUMNS: List[str] = [
    "address",
    "house_number",
    "latitude",
    "longitude",
    "omi_zone",
]

# Columns for Energy agent
ENERGY_AGENT_COLUMNS: List[str] = [
    "energy_class",
    "epglnren_ape",
    "classe_target_ape",
    "energy_score_class",
    "energy_score_plant",
    "energy_score_envelope",
    "energy_score_renewables",
    "energy_score_total",
]

# Columns for Building agent
BUILDING_AGENT_COLUMNS: List[str] = [
    "property_type",
    "construction_period",
    "id",
    "codice_comune",
    "cadastral_sheet",
    "cadastral_parcel",
    "cadastral_subaltern",
    "cadastral_units_count",
    "surface_area",
]

# Columns for Regulatory agent
REGULATORY_AGENT_COLUMNS: List[str] = [
    "surface_area",
    "property_type",
    "legal_nature",
    "cultural_constraint",
]

# Columns for Proximity agent
PROXIMITY_AGENT_COLUMNS: List[str] = [
    "healthcare",
    "mobility",
    "green",
    "sport",
    "commercial",
    "education",
]


# Union of all columns visible to agents and thus filterable via SQL
ALL_AGENT_COLUMNS_SET = set(
    LOCATION_AGENT_COLUMNS +
    ENERGY_AGENT_COLUMNS +
    BUILDING_AGENT_COLUMNS +
    REGULATORY_AGENT_COLUMNS +
    PROXIMITY_AGENT_COLUMNS
)

SQL_FILTERABLE_COLUMNS: List[str] = sorted(list(ALL_AGENT_COLUMNS_SET))
ALL_AGENT_COLUMNS: List[str] = SQL_FILTERABLE_COLUMNS

# Proximity categories for documentation
PROXIMITY_CATEGORIES: Dict[str, str] = {
    "healthcare": "Hospitals, pharmacies, clinics",
    "mobility": "Metro, bus, stations",
    "green": "Parks, gardens",
    "sport": "Gyms, pools, courts",
    "commercial": "Shops, supermarkets",
    "education": "Schools, universities",
}

# Agent column vocabulary -> dataset column names.
#
# The agents speak canonical English column names (the *_AGENT_COLUMNS lists
# above), while the estates dataset in use has Italian columns
# (superficie_di_riferimento_mq, mobilita, ...): the March refactor translated the
# agent vocabulary and renamed the dataset at load time, the April switch to
# create_estate_dataset.py (English output) removed that rename, and the parquet
# in use was never regenerated. Each canonical name maps to the dataset/legacy
# names it may appear under; ``resolve_column`` picks whichever one the live
# schema has, so the same code works with the Italian and an English parquet.
#
# Single source of truth: TriSQL's SQL repair / validation / renderer use this
# same object (constraint_validation re-exports it); the rankers and the column
# statistics resolve through ``resolve_column``.
#
# Rules: a name may appear under ONE canonical key only. Never alias computed
# columns (energy_score, building_score, ...) and never map classe_target_ape to
# energy_class (achievable vs current class). No dataset column at all:
# legal_nature, cultural_constraint, purpose, description,
# third_party_tenure_type, annual_rent.
COLUMN_ALIASES: Dict[str, Set[str]] = {
    # Building / cadastral
    "surface_area": {"superficie_di_riferimento_mq"},
    "property_type": {"tipologia_bene_immobile"},
    "construction_period": {"epoca_costruzione"},          # VARCHAR years in the parquet
    "cadastral_sheet": {"foglio"},
    "cadastral_parcel": {"particella"},
    "cadastral_subaltern": {"subalterno"},
    "cadastral_units_count": {"numero_immobili_per_catasto", "number_immobili_per_catasto"},
    # Energy
    "energy_class": {"classe_energetica_ape"},
    "energy_score_class": {"ape_score_classe"},
    "energy_score_plant": {"ape_score_impianto"},
    "energy_score_envelope": {"ape_score_involucro"},
    "energy_score_renewables": {"ape_score_rinnovabili"},
    "energy_score_total": {"ape_score_total"},              # 4-20 sum of the four sub-scores
    # Proximity (0-100 percentile scores)
    "healthcare": {"sanita", "sanità"},
    "mobility": {"mobilita", "mobilità"},
    "green": {"greenery", "verde"},
    "commercial": {"commerciale", "commerce"},
    "education": {"educazione"},
    # Location
    "address": {"indirizzo"},
    "house_number": {"numero_civico"},
    "latitude": {"latitudine"},
    "longitude": {"longitudine"},
    "omi_zone": {"zona_omi"},
    # Record metadata
    "effective_date": {"data_decorrenza"},
    "is_meta_estate": {"meta_immobile"},
}

_ALIAS_TO_CANONICAL: Dict[str, str] = {
    variant: canonical for canonical, variants in COLUMN_ALIASES.items() for variant in variants
}


def resolve_column(name: Optional[str], available) -> Optional[str]:
    """Return the column of ``available`` that ``name`` refers to, or None.

    ``name`` may be canonical (``surface_area``) or already a dataset name
    (``superficie_di_riferimento_mq``). A literal match wins; otherwise the
    alias group is searched in a fixed order, so the pick is deterministic.
    """
    if not name or not isinstance(name, str):
        return None  # e.g. malformed LLM output with a list of columns
    if not isinstance(available, (set, frozenset, dict)):
        available = set(available)
    if name in available:
        return name
    canonical = name if name in COLUMN_ALIASES else _ALIAS_TO_CANONICAL.get(name)
    if canonical is None:
        return None
    for candidate in [canonical, *sorted(COLUMN_ALIASES[canonical])]:
        if candidate in available:
            return candidate
    return None

# Global object containing updated runtime metadata
DB_METADATA = {
    "_metadata_version": "2.0",
    "score_legends": {
        "energy_scores": ENERGY_SCORE_LEGEND,
        "proximity_scores": SCORE_LEGEND
    },
    "filterable_columns": SQL_FILTERABLE_COLUMNS,
    "fields": {}  # Populated with statistics and categorical values at runtime
}


def update_runtime_metadata(df):
    """
    Populates DB_METADATA with real statistics from the loaded dataframe.
    Replaces the need for external JSON files.
    """
    try:
        from datetime import datetime
        import pandas as pd
        
        DB_METADATA["_last_updated"] = datetime.now().strftime("%Y-%m-%d")
        df_columns = set(df.columns)
        
        # Synchronize filterable columns. Names stay in the agents' vocabulary
        # (what the requirements use); they count as present when they resolve
        # to a dataset column (COLUMN_ALIASES), not only on a literal match.
        DB_METADATA["filterable_columns"] = [c for c in SQL_FILTERABLE_COLUMNS if resolve_column(c, df_columns)]

        # Lists of columns to analyze
        categorical = ["codice_comune", "property_type", "construction_period", "energy_class"]
        numerical = [
            "surface_area", "energy_score_total",
            "healthcare", "mobility", "green", "sport", "commercial", "education"
        ]
        max_values = 100  # keep the SQL prompt bounded (construction years are high-cardinality)

        # Reset fields
        DB_METADATA["fields"] = {}

        for col in categorical + numerical:
            data_col = resolve_column(col, df_columns)
            if data_col:
                meta = {}
                if col in categorical:
                    # Extract unique values and sort
                    unique_vals = sorted([str(v) for v in df[data_col].dropna().unique()])
                    meta["values"] = unique_vals[:max_values]
                    meta["is_truncated"] = len(unique_vals) > max_values
                else:
                    # Calculate numerical statistics
                    series = pd.to_numeric(df[data_col], errors='coerce').dropna()
                    if not series.empty:
                        meta.update({
                            "min": round(float(series.min()), 2),
                            "max": round(float(series.max()), 2),
                            "mean": round(float(series.mean()), 2),
                            "median": round(float(series.median()), 2),
                            "percentiles": {
                                "25%": round(float(series.quantile(0.25)), 2),
                                "75%": round(float(series.quantile(0.75)), 2)
                            }
                        })
                DB_METADATA["fields"][col] = meta
                
    except Exception as e:
        from app.utils.logger import logger
        logger.error(f"Failed to update runtime metadata: {e}")
