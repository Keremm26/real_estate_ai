"""
Real Estate Service - Handles building data loading, filtering, and retrieval.

This service provides:
- Loading real estate data from parquet files
- Filtering by multiple criteria (surface area, energy class, city, etc.)
- Pagination support
- Individual building lookup by ID
- Dataset caching for performance
"""

import asyncio
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from app.core.config import settings
from app.data.loaders import load_and_merge_data
from app.models.requests import BuildingFilters
from app.models.responses import (
    EnergyScores,
    BuildingResponse,
    Coordinates,
    ProximityScores,
    SubProperty,
)
from app.utils.logger import logger
logger = logger.opt(colors=False)


class RealEstateService:
    """Service for managing real estate building data."""

    # Class-level cache to share dataset across request-scoped instances
    _dataset_cache: Dict[str, pd.DataFrame] = {}
    _dataset_indexed_cache: Dict[str, pd.DataFrame] = {}

    def __init__(self):
        """Initialize the service."""
        pass

    async def get_buildings(
        self,
        filters: BuildingFilters,
        limit: int = 100,
        offset: int = 0,
        dataset_key: str = "full",
        db: Optional["Session"] = None,
    ) -> Tuple[List[BuildingResponse], int]:
        """
        Get filtered and paginated buildings.

        Args:
            filters: BuildingFilters object with filter criteria
            limit: Maximum number of results to return
            offset: Number of results to skip
            dataset_key: Dataset to use ('full', 'meta', 'ape')
            db: Optional database session for run_id lookup

        Returns:
            Tuple of (list of buildings, total count before pagination)
        """
        # Load dataset (async wrapper for sync operation)
        df = await asyncio.to_thread(self._load_dataset, dataset_key)

        if df is None or df.empty:
            logger.warning(f"Dataset '{dataset_key}' is empty or failed to load")
            return [], 0

        # Apply filters
        filtered_df = self._apply_filters(df, filters, db=db)
        total_count = len(filtered_df)

        # Apply pagination
        paginated_df = filtered_df.iloc[offset : offset + limit]

        # Convert to response models
        buildings = []
        for _, row in paginated_df.iterrows():
            try:
                building = self._df_row_to_building(row)
                buildings.append(building)
            except Exception as e:
                logger.error(f"Error converting row to building: {e}")
                continue

        return buildings, total_count

    async def get_markers_lite(
        self,
        limit: int = 1000,
        dataset_key: str = "full",
        filters: Optional[BuildingFilters] = None,
    ) -> List[Dict]:
        """
        Get lightweight markers using vectorized Pandas operations.
        Returns list of dicts directly for maximum performance.
        """
        # Load dataset (async wrapper)
        df = await asyncio.to_thread(self._load_dataset, dataset_key)

        if df is None or df.empty:
            return []

        # Apply filters if provided
        if filters:
            df = self._apply_filters(df, filters)

        # Apply limit
        if limit > 0:
            df = df.head(limit)

        # Vectorized mapping for performance
        # Select only needed columns and rename them to match MapMarkerLite schema
        # We use a mapping dict: {new_name: [list of possible old names]}
        
        column_mapping = {
            "id": ["id"],
            "lat": ["latitude", "lat", "latitudine"],
            "lng": ["longitude", "lon", "longitudine"],
            "price": ["price", "annual_rent"],
            "surface": ["surface_area", "surface"],
            "energy_class": ["energy_class"],
            "year": ["construction_year"],
            "type": ["property_type"],
            "is_meta": ["is_meta"]
        }

        # Create a new DataFrame with mapped columns
        lite_df = pd.DataFrame(index=df.index)
        
        for new_col, candidates in column_mapping.items():
            # Find first available candidate column
            source_col = next((c for c in candidates if c in df.columns), None)
            if source_col:
                lite_df[new_col] = df[source_col]
            else:
                # Fill with None/NaN if not found
                lite_df[new_col] = None

        # Handle type conversions safely
        # Ensure lat/lng are floats
        lite_df["lat"] = pd.to_numeric(lite_df["lat"], errors="coerce")
        lite_df["lng"] = pd.to_numeric(lite_df["lng"], errors="coerce")
        
        # Drop rows with invalid coordinates
        lite_df = lite_df.dropna(subset=["lat", "lng"])

        # Numeric fields
        lite_df["price"] = pd.to_numeric(lite_df["price"], errors="coerce")
        lite_df["surface"] = pd.to_numeric(lite_df["surface"], errors="coerce")
        
        # Boolean fields (handle string 'True'/'False' if necessary)
        # Using a vectorized approach for string->bool if column is object/string
        if "is_meta" in lite_df.columns and lite_df["is_meta"].dtype == "object":
             lite_df["is_meta"] = lite_df["is_meta"].astype(str).str.lower() == "true"
        elif "is_meta" in lite_df.columns:
             lite_df["is_meta"] = lite_df["is_meta"].fillna(False).astype(bool)

        # Add static tier
        lite_df["tier"] = 1

        # Convert to list of dicts - fastest serialization path
        # replace NaN with None for valid JSON
        return lite_df.replace({np.nan: None}).to_dict(orient="records")

    async def get_building_by_id(
        self, building_id: str, dataset_key: str = "full"
    ) -> Optional[BuildingResponse]:
        """
        Get a single building by ID.

        Args:
            building_id: Building ID to retrieve
            dataset_key: Dataset to search in

        Returns:
            BuildingResponse or None if not found
        """
        df = await asyncio.to_thread(self._load_dataset, dataset_key)

        if df is None or df.empty:
            return None

        # Filter by ID (ensure string comparison)
        building_df = df[df["id"].astype(str) == str(building_id)]

        if building_df.empty:
            return None

        # Convert first row to building
        try:
            return self._df_row_to_building(building_df.iloc[0])
        except Exception as e:
            logger.error(f"Error converting building {building_id}: {e}")
            return None

    def _str_to_bool(self, value) -> bool:
        """Convert string 'True'/'False' or bool to boolean."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def _load_dataset(self, dataset_key: str) -> Optional[pd.DataFrame]:
        """
        Load dataset with caching.

        Args:
            dataset_key: Dataset identifier ('full', 'meta', 'ape')

        Returns:
            DataFrame or None if load fails
        """
        # Log cache state for debugging
        logger.info(
            f"_load_dataset called for '{dataset_key}', cache keys: {list(RealEstateService._dataset_cache.keys())}"
        )

        # Check class-level cache first (use class name explicitly)
        if dataset_key in RealEstateService._dataset_cache:
            logger.info(f"Using cached dataset: {dataset_key}")
            return RealEstateService._dataset_cache[dataset_key]

        # Get file path from settings
        file_path = self._get_dataset_path(dataset_key)

        if not file_path:
            logger.error(f"Unknown dataset key: {dataset_key}")
            return None

        # Load data using existing loader
        logger.info(f"Loading dataset: {dataset_key} from {file_path}")
        df = load_and_merge_data(file_path)

        if df is not None and not df.empty:
            # Cache the dataset
            self._dataset_cache[dataset_key] = df

            # Build and cache ID index for fast lookups
            try:
                if "id" in df.columns:
                    df_indexed = df.copy()
                    df_indexed["id_str"] = df_indexed["id"].astype(str)
                    df_indexed = df_indexed.drop_duplicates(subset=["id_str"])
                    df_indexed.set_index("id_str", inplace=True)
                    self._dataset_indexed_cache[dataset_key] = df_indexed
                    logger.info(
                        f"Built ID index for {dataset_key} ({len(df_indexed)} unique IDs)"
                    )
            except Exception as e:
                logger.warning(f"Failed to build ID index: {e}")

            logger.info(f"Loaded and cached {len(df)} buildings from {dataset_key}")

        return df

    def _get_dataset_path(self, dataset_key: str) -> Optional[str]:
        """
        Get file path for dataset key.

        Args:
            dataset_key: Dataset identifier

        Returns:
            File path or None
        """
        # Note: 'meta' option removed - use 'full' as single source of truth
        dataset_paths = {
            "full": settings.DATASET_FULL,
        }
        return dataset_paths.get(dataset_key.lower())

    def _apply_filters(
        self, df: pd.DataFrame, filters: BuildingFilters, db: Optional["Session"] = None
    ) -> pd.DataFrame:
        """
        Apply filters to DataFrame.

        Args:
            df: Input DataFrame
            filters: BuildingFilters object
            db: Optional database session for run_id lookup

        Returns:
            Filtered DataFrame
        """
        result = df.copy()

        # 0. Filter by run_id (requires database lookup)
        if filters.run_id and db:
            from app.database.models import Run

            run = db.query(Run).filter(Run.run_id == filters.run_id).first()
            if run and run.results:
                # Extract building IDs from run results
                try:
                    target_ids = []
                    # Case 1: results is a list of objects with an 'id' field
                    if isinstance(run.results, list):
                        target_ids = [str(item.get("id")) for item in run.results if item.get("id")]
                    # Case 2: results is a dict with a 'ranking' list
                    elif isinstance(run.results, dict) and "ranking" in run.results:
                        target_ids = [
                            str(item.get("id")) for item in run.results["ranking"] if item.get("id")
                        ]

                    if target_ids:
                        result = result[result["id"].astype(str).isin(target_ids)]
                    else:
                        logger.warning(f"No building IDs found in run results for {filters.run_id}")
                except Exception as e:
                    logger.error(f"Error extracting IDs from run {filters.run_id}: {e}")
            elif not run:
                logger.warning(f"Run {filters.run_id} not found in database")
        elif filters.run_id and not db:
            logger.warning("run_id filter provided but no database session available")

        # Filter by city
        if filters.city:
            if "city" in result.columns:
                result = result[result["city"].str.lower() == filters.city.lower()]

        # Filter by surface area range
        if filters.min_surface is not None:
            if "surface_area" in result.columns:
                result = result[result["surface_area"] >= filters.min_surface]

        if filters.max_surface is not None:
            if "surface_area" in result.columns:
                result = result[result["surface_area"] <= filters.max_surface]

        # Filter by energy classes
        if filters.energy_classes:
            energy_col = None
            # Check for standard energy class column
            if "energy_class" in result.columns:
                energy_col = "energy_class"

            if energy_col:
                # Normalize filter classes to uppercase
                filter_classes = [c.upper() for c in filters.energy_classes]
                logger.info(
                    f"Filtering by energy classes: {filter_classes}, column: {energy_col}"
                )

                # Match either exact class (A, B, C) or class with number (A1, A2, A3, A4)
                def matches_class(val):
                    if pd.isna(val):
                        return False
                    val_upper = str(val).upper().strip()
                    for fc in filter_classes:
                        # Exact match or starts with (e.g., "A" matches "A", "A1", "A2")
                        if val_upper == fc or val_upper.startswith(fc):
                            return True
                    return False

                before_count = len(result)
                result = result[result[energy_col].apply(matches_class)]
                logger.info(f"Energy filter: {before_count} -> {len(result)} buildings")
            else:
                logger.warning(
                    f"Energy class column not found in columns: {result.columns.tolist()[:10]}"
                )

        # Filter by minimum score
        if filters.min_score is not None and "score" in result.columns:
            result = result[result["score"] >= filters.min_score]

        # Filter by maximum score
        if filters.max_score is not None and "score" in result.columns:
            result = result[result["score"] <= filters.max_score]

        # Filter by evaluation status
        if filters.is_evaluated is not None and "is_evaluated" in result.columns:
            result = result[result["is_evaluated"] == filters.is_evaluated]

        # Filter by construction eras
        if filters.construction_periods:
            epoca_col = "construction_year" if "construction_year" in result.columns else None

            if epoca_col:
                logger.info(f"Filtering by eras: {filters.construction_periods}")
                before_count = len(result)
                result = result[result[epoca_col].isin(filters.construction_periods)]
                logger.info(f"Era filter: {before_count} -> {len(result)} buildings")

        # Filter by property types
        if filters.property_types:
            type_col = "property_type" if "property_type" in result.columns else None

            if type_col:
                # Case-insensitive match
                lower_types = [t.lower() for t in filters.property_types]
                result = result[result[type_col].str.lower().isin(lower_types)]

        # Filter by asset_utilization (string matching)
        if filters.asset_utilization:
            utilizzo_col = None
            if "utilizzo_del_bene" in result.columns:
                utilizzo_col = "utilizzo_del_bene"
            elif "utilizzo" in result.columns:
                utilizzo_col = "utilizzo"

            if utilizzo_col:
                lower_vals = [v.lower() for v in filters.asset_utilization]
                result = result[result[utilizzo_col].str.lower().isin(lower_vals)]

        # Filter by cultural_constraints (string matching)
        if filters.cultural_constraints:
            vincolo_col = None
            if "vincolo_culturale_paesaggistico" in result.columns:
                vincolo_col = "vincolo_culturale_paesaggistico"
            elif "vincolo" in result.columns:
                vincolo_col = "vincolo"

            if vincolo_col:
                lower_vals = [v.lower() for v in filters.cultural_constraints]
                result = result[result[vincolo_col].str.lower().isin(lower_vals)]

        # Filter by is_meta_building
        if filters.is_meta_building is not None:
            meta_col = "is_meta_estate" if "is_meta_estate" in result.columns else "is_meta"
            if meta_col in result.columns:
                if filters.is_meta_building:
                    result = result[result[meta_col] == True]
                else:
                    result = result[(result[meta_col] == False) | (result[meta_col].isna())]


        return result

    def _df_row_to_building(self, row: pd.Series) -> BuildingResponse:
        """
        Convert DataFrame row to BuildingResponse model.

        Args:
            row: Pandas Series (DataFrame row)

        Returns:
            BuildingResponse object
        """

        # Helper to safely get values
        def safe_get(key: str, default=None, alternatives: List[str] = None):
            """Try key, then alternatives, return default if not found."""
            if key in row.index and pd.notna(row[key]):
                return row[key]
            if alternatives:
                for alt in alternatives:
                    if alt in row.index and pd.notna(row[alt]):
                        return row[alt]
            return default

        # Enrich row from indexed cache if available
        # This ensures that if 'row' comes from a partial SQL result (missing columns),
        # we fill in the gaps from the full dataset.
        df_indexed = self._dataset_indexed_cache.get("full")
        row_id = str(row.get("id", ""))

        if df_indexed is not None and row_id and row_id in df_indexed.index:
            try:
                full_row = df_indexed.loc[row_id]
                if isinstance(full_row, pd.DataFrame):
                    full_row = full_row.iloc[0]

                # combine_first fills missing values in 'row' from 'full_row'
                # It aligns on index (column names), so missing columns are added.
                row = row.combine_first(full_row)
            except Exception as e:
                # Log but continue with original row
                pass

        # Extract coordinates
        lat = safe_get("latitude", alternatives=["lat", "latitudine"])
        lon = safe_get("longitude", alternatives=["lon", "longitudine"])

        if lat is None or lon is None:
            # Try to extract from other common column names
            lat = lat or safe_get("coordinata_y")
            lon = lon or safe_get("coordinata_x")

        # Coordinates are required for mapping
        if lat is None or lon is None:
            # Log warning but use default coordinates to allow processing to continue
            logger.warning(
                f"Missing coordinates for building {row.get('id', 'unknown')}, using default (0, 0)"
            )
            lat = 0.0
            lon = 0.0

        coordinates = Coordinates(lat=float(lat), lon=float(lon))

        # Extract energy scores if available
        energy_scores = None
        energy_score_cols = {
            "total": ["energy_score_total", "energy_score", "ape_score_total"],
            "class_score": ["energy_score_class", "ape_score_classe"],
            "system_score": ["energy_score_plant", "ape_score_impianto"],
            "envelope_score": ["energy_score_envelope", "ape_score_involucro"],
            "renewables_score": ["energy_score_renewables", "ape_score_rinnovabili"],
        }
        
        energy_data = {}
        for key, cols in energy_score_cols.items():
            value = safe_get(cols[0], alternatives=cols[1:] if len(cols) > 1 else [])
            if value is not None:
                if key == "total":
                    energy_data[key] = float(value)
                else:
                    energy_data[key] = int(value) if not pd.isna(value) else 0

        if energy_data and any(k in energy_data for k in ["class_score", "system_score", "envelope_score", "renewables_score"]):
            # Ricalcola total come somma dei sotto-score se total è 0 o mancante
            sub_total = (
                energy_data.get("class_score", 0)
                + energy_data.get("system_score", 0)
                + energy_data.get("envelope_score", 0)
                + energy_data.get("renewables_score", 0)
            )
            total_val = energy_data.get("total", 0.0) or sub_total  # usa sub_total se total==0
            energy_scores = EnergyScores(
                total=float(total_val),
                class_score=energy_data.get("class_score", 0),
                system_score=energy_data.get("system_score", 0),
                envelope_score=energy_data.get("envelope_score", 0),
                renewables_score=energy_data.get("renewables_score", 0),
            )

        # Extract POI scores if available
        poi_scores = None
        poi_columns = {
            "healthcare": ["healthcare", "sanita", "sanità"],
            "mobility": ["mobility", "mobilita", "mobilità"],
            "greenery": ["green", "greenery", "verde"],
            "education": ["education", "educazione"],
            "commerce": ["commercial", "commerce", "commerciale"],
            "sport": ["sport"],
        }
        
        poi_data = {}
        for key, cols in poi_columns.items():
            value = safe_get(cols[0], alternatives=cols[1:] if len(cols) > 1 else [])
            if value is not None:
                poi_data[key] = float(value)

        if poi_data:
            poi_scores = ProximityScores(**poi_data)

        # Parse APE files list
        def parse_ape_list(val):
            """Parse APE file list from various formats."""
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None
            if isinstance(val, list):
                return [str(item) for item in val if item]
            if isinstance(val, str):
                if not val.strip() or val.lower() == "nan":
                    return None
                try:
                    import ast

                    parsed = ast.literal_eval(val)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed if item]
                except Exception:
                    # Might be comma-separated or just a single file
                    if "," in val:
                        return [v.strip() for v in val.split(",") if v.strip()]
                    return [val] if val.strip() else None
        # Parse energy files list
        ape_files = parse_ape_list(
            safe_get(
                "energy_files", alternatives=["lista_file_ape", "list_file_ape_filtered", "list_file_ape_filtered_parsed"]
            )
        )

        # Parse sub-properties for meta buildings
        sub_properties = None
        if self._str_to_bool(safe_get("meta_building", default=False)):
            id_list_raw = safe_get("id_list")
            if id_list_raw is not None:
                try:
                    import ast

                    # Parse id_list which might be a string representation of a list
                    if isinstance(id_list_raw, str):
                        id_list_parsed = ast.literal_eval(id_list_raw)
                    elif isinstance(id_list_raw, list):
                        id_list_parsed = id_list_raw
                    else:
                        id_list_parsed = []

                    if id_list_parsed and len(id_list_parsed) > 0:
                        sub_properties = []
                        # Get the cached indexed dataset for O(1) lookup
                        df_indexed = self._dataset_indexed_cache.get("full")

                        for sub_id in id_list_parsed[:20]:  # Limit to 20
                            sub_id_str = str(sub_id)
                            sub_prop = SubProperty(id=sub_id_str)

                            # Try to find details in the dataset using fast index lookup
                            if (
                                df_indexed is not None
                                and sub_id_str in df_indexed.index
                            ):
                                try:
                                    sub_row = df_indexed.loc[sub_id_str]

                                    # Handle case where index might not be unique (though we tried to dedup)
                                    if isinstance(sub_row, pd.DataFrame):
                                        sub_row = sub_row.iloc[0]

                                    if "surface_area" in sub_row.index and pd.notna(sub_row["surface_area"]):
                                        sub_prop.surface_area = float(sub_row["surface_area"])
                                    elif "superficie_di_riferimento_mq" in sub_row.index and pd.notna(sub_row["superficie_di_riferimento_mq"]):
                                        sub_prop.surface_area = float(sub_row["superficie_di_riferimento_mq"])
                                    if "property_type" in sub_row.index and pd.notna(sub_row["property_type"]):
                                        sub_prop.property_type = str(sub_row["property_type"])
                                    elif "tipologia_bene_immobile" in sub_row.index and pd.notna(sub_row["tipologia_bene_immobile"]):
                                        sub_prop.property_type = str(sub_row["tipologia_bene_immobile"])
                                except Exception as e:
                                    # Fallback or ignore error for single item
                                    pass

                            sub_properties.append(sub_prop)
                except Exception as e:
                    logger.warning(f"Error parsing sub-properties: {e}")
                    sub_properties = None

        # Format address
        addr = safe_get("address", alternatives=["indirizzo"])
        civic = safe_get("numero_civico")
        if addr and civic and pd.notna(civic):
            address_str = f"{addr}, {civic}"
        else:
            address_str = addr

        # Map city
        city_val = safe_get("city", alternatives=["codice_comune"])
        from app.core.cities import active_city
        if city_val == "L219" or (hasattr(settings, "TARGET_COMUNE_CODE") and city_val == settings.TARGET_COMUNE_CODE):
            city_str = getattr(settings, "TARGET_CITY", "Torino")
        elif active_city().key != "turin":  # one dataset = one city (codice_comune is a borough/INE code)
            city_str = active_city().name
        else:
            city_str = city_val

        # Build the response
        res_data = {
            "id": str(safe_get("id", "")),
            "address": address_str,
            "city": city_str,
            "coordinates": coordinates,
            "surface_area": safe_get("surface_area", alternatives=["superficie_di_riferimento_mq"]),
            "energy_class": safe_get("energy_class", alternatives=["classe_energetica_ape"]),
            "score": safe_get("final_ranking_score", alternatives=["score", "energy_score"]),
            "rooms": None,
            "bathrooms": None,
            "floor": None,
            "price": safe_get("price", alternatives=["annual_rent"]),
            "description": safe_get("description", alternatives=["tipologia_bene_immobile"]),

            "property_type": safe_get("property_type", alternatives=["tipologia_bene_immobile"]),
            "legal_nature": safe_get("legal_nature"),
            "cultural_constraint": None,
            "purpose": safe_get("purpose"),
            "omi_zone": safe_get("omi_zone", alternatives=["zona_omi"]),
            "is_evaluated": bool(safe_get("is_evaluated", default=False)),
            "is_match": bool(safe_get("is_match", default=True)),
            "meta_building": self._str_to_bool(safe_get("is_meta_estate", alternatives=["is_meta", "meta_immobile"], default=False)),
            "annual_rent": safe_get("annual_rent"),
            "effective_date": safe_get("effective_date", alternatives=["data_decorrenza"]),
            "cadastral_units_count": safe_get("cadastral_units_count", alternatives=["numero_immobili_per_catasto"], default=None),
            "id_list": (str(safe_get("id_list", default="")) if safe_get("id_list") else None),
            "sub_properties": sub_properties,
            "energy_scores": energy_scores,
            "proximity_scores": poi_scores,
            "energy_files": ape_files,
            "distance_km": safe_get("distance_km", alternatives=["distanza_km"]),
            "proximity_reference": safe_get("proximity_reference"),
            "greenery": safe_get("green", alternatives=["greenery", "verde"]),
            "mobility": safe_get("mobility", alternatives=["mobilita"]),
            "education": safe_get("education", alternatives=["educazione"]),
            "location_score": safe_get("location_score"),
            "regulatory_score": safe_get("regulatory_score"),
            "energy_score": safe_get("energy_score"),
            "building_score": safe_get("building_score"),
            "proximity_score": safe_get("proximity_score"),
            "ranking_weight_location": safe_get("ranking_weight_location"),
            "ranking_weight_regulatory": safe_get("ranking_weight_regulatory"),
            "ranking_weight_energy": safe_get("ranking_weight_energy"),
            "ranking_weight_building": safe_get("ranking_weight_building"),
            "ranking_weight_proximity": safe_get("ranking_weight_proximity"),
        }

        # Handle fields that need explicit string conversion or might be None
        # This prevents Pydantic validation errors when pandas returns Timestamps for string fields
        # or when we inadvertently convert None to "None"
        
        # construction_year
        val = safe_get("construction_year", alternatives=["epoca_costruzione"])
        res_data["construction_year"] = str(val) if val is not None else None
        
        # cadastral_sheet
        val = safe_get("cadastral_sheet", alternatives=["foglio"])
        res_data["cadastral_sheet"] = str(val) if val is not None else None
        
        # cadastral_parcel
        val = safe_get("cadastral_parcel", alternatives=["particella"])
        res_data["cadastral_parcel"] = str(val) if val is not None else None
        
        # effective_date
        val = safe_get("effective_date")
        res_data["effective_date"] = str(val) if val is not None else None
        
        return BuildingResponse(**res_data)
