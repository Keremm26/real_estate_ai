"""
Application configuration management using Pydantic Settings.
Merged constants from original app/config.py
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict, Optional
import os
from dotenv import load_dotenv

# Load environment variables from .env file explicitly to ensure they are available in os.environ
env_path = os.path.join(os.path.dirname(__file__), "../../.env")
load_dotenv(env_path)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ==========================================================================
    # App Info
    # ==========================================================================
    APP_NAME: str = "MURENA-API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    LOG_LEVEL: str = "ERROR"

    # ==========================================================================
    # Server
    # ==========================================================================
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4

    # ==========================================================================
    # Database
    # ==========================================================================
    DATABASE_URL: str
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_PATH: str = "data/database/users.db"  # SQLite fallback
    CACHE_DIR: str = "data/database/cache"

    # ==========================================================================
    # Redis
    # ==========================================================================
    REDIS_URL: str = "redis://localhost:6379/0"

    # ==========================================================================
    # Security
    # ==========================================================================
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ==========================================================================
    # CORS
    # ==========================================================================
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost",
        "http://localhost:80",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
    ]

    # ==========================================================================
    # LLM APIs
    # ==========================================================================
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    HF_TOKEN: str = ""

    # API key for the institutional LLM endpoint (anonymized for review)
    INSTITUTIONAL_LLM_API_KEY: str = ""  

    # LLM Provider: "openai" or "gemini" - controls which provider to use by default
    DEFAULT_LLM_PROVIDER: str = "openai"

    # LLM Models
    LLM_MODEL: str = "gpt-oss-120b"
    OPENAI_API_BASE: Optional[str] = None

    LLMODEL_CONCURRENCY_LIMITS: Dict[str, int] = {
        "gpt-5.4": 2,
        "gpt-oss-120b": 48,
        "gemma3-27b": 48,
        "qwen3-8b": 48,
        "gemma4-local": 1,  # single local GPU (Ollama) — keep requests serial
        "gemma4-31b": 8,    # institutional vLLM endpoint — shared, moderate concurrency
    }

    def set_llm_model(self, model_type: str):
        """Sets the LLM model configuration.
        
        Args:
            model_type: The identifier for the LLM flavor to use.
        """
        # Ensure latest env vars are loaded (avoids issues with subprocesses/caching)
        base_url = os.environ.get("OPENAI_API_BASE")
        
        if model_type == "gpt-oss-120b":
            self.LLM_MODEL = "gemma-4"
            self.OPENAI_API_BASE = base_url
            self.OPENAI_API_KEY = self.INSTITUTIONAL_LLM_API_KEY or "vllm"
        elif model_type == "gemma3-27b":
            self.LLM_MODEL = "gemma-4"
            self.OPENAI_API_BASE = base_url
            self.OPENAI_API_KEY = "vllm"
        elif model_type == "qwen3-8b":
            self.LLM_MODEL = "gemma-4"
            self.OPENAI_API_BASE = base_url
            self.OPENAI_API_KEY = "vllm"
        elif model_type == "gpt-5.4":
            self.LLM_MODEL = "gpt-5.4"
            self.OPENAI_API_BASE = None # Base OpenAI
        elif model_type == "gemma4-local":
            # Local Gemma 4 (E4B) served by Ollama's OpenAI-compatible endpoint.
            # No real API key needed; Ollama ignores it. Override the host with
            # OLLAMA_API_BASE if the server runs somewhere other than localhost.
            self.LLM_MODEL = "gemma4:e4b"
            self.OPENAI_API_BASE = os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434/v1"
            self.OPENAI_API_KEY = "ollama"
        elif model_type == "gemma4-31b":
            # Institutional Gemma 4 31B (FP8) served by vLLM (OpenAI-compatible).
            # Override the host with GEMMA4_31B_API_BASE if the endpoint moves.
            self.LLM_MODEL = "RedHatAI/gemma-4-31B-it-FP8-block"
            self.OPENAI_API_BASE = os.environ.get("GEMMA4_31B_API_BASE") or "http://130.192.163.76:8000/v1"
            self.OPENAI_API_KEY = self.INSTITUTIONAL_LLM_API_KEY or "vllm"


    # Agent Temperature - 0.0 for fully deterministic outputs (consistency)
    # Set to 0.0 to eliminate non-determinism, higher values allow creativity
    AGENT_TEMPERATURE: float = 0.0

    # Enable JSON export of run results for LLM analysis/debugging
    # Set to False in production to avoid unnecessary file writes
    ENABLE_RUN_JSON_EXPORT: bool = False

    # ==========================================================================
    # External APIs
    # ==========================================================================
    NOMINATIM_USER_AGENT: str = "MURENA/1.0"

    # ==========================================================================
    # File Paths (relative to backend/ directory, MUST be defined in .env)
    DATA_DIR: str
    STATIC_DIR: str
    ENERGY_DIR: str
    META_DIR: str
    RUNS_DIR: str
    AGENT_LOGS_DIR: str

    # Energy specific paths
    ENERGY_MATCH_DIR: str
    PLOT_DIR: str

    # Dataset paths
    DATASET_FULL: str
    IMMOBILI_MAPPING_PATH: str
    QIP_VALORI_PATH: str
    QIP_MAPPING_PATH: str
    ZONE_OMI_PROVINCIA_TORINO_GEOJSON: str
    IMMOBILI_QUOTAZIONE_PATH: str
    ZONE_GRUPPO_QUOTAZIONI_PATH: str
    MISSING_QUOTAZIONI_IDS_PATH: str

    # Static data
    STATIONS_CSV: str
    ZONE_OMI_GEOJSON: str
    ZONE_URBANISTICHE_GEOJSON: str
    MUNICIPI_GEOJSON: str

    TORINO_LAT: float = 45.116177
    TORINO_LON: float = 7.742615

    # ==========================================================================
    # Map Configuration
    # ==========================================================================
    DEFAULT_MAP_CENTER_LAT: float = 45.070860
    DEFAULT_MAP_CENTER_LON: float = 7.685588
    DEFAULT_MAP_ZOOM: int = 13
    SELECTED_ITEM_ZOOM: int = 15

    # ==========================================================================
    # Limits and Caps
    # ==========================================================================
    MAX_ITEMS_FOR_MAP: int = 1000
    MAX_ITEMS_FOR_LLM: int = 25
    MAX_LLM_CAP: int = 10  # Hard cap for LLM evaluation

    # ==========================================================================
    # Calculation Constants
    # ==========================================================================
    MINUTES_PER_KM_WALKING: int = 12  # 5 km/h
    MINUTES_PER_METRO_STATION_SEGMENT: int = 2

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "../../.env"), case_sensitive=True, extra="ignore"
    )

    # ==========================================================================
    # Computed Properties
    # ==========================================================================
    @property
    def dataset_options(self) -> Dict[str, str]:
        """Return dataset options mapping.

        Note: Only 'full' is actively used.
        """
        return {
            "full": self.DATASET_FULL,
        }

    @property
    def agent_models(self) -> Dict[str, str]:
        """Return agent-specific model configuration."""
        return {
            "default": self.LLM_MODEL,
        }


# Global settings instance
try:
    settings = Settings()
except Exception as e:
    import sys
    print("\n" + "="*80)
    print("ERRORE DI CONFIGURAZIONE (Environment Variables)")
    print("="*80)
    print("Una o più variabili d'ambiente richieste non sono state trovate nel file .env")
    print("o nell'ambiente di sistema.")
    print("\nDettagli dell'errore:")
    print(str(e))
    print("="*80 + "\n")
    sys.exit(1)

# Export agent-specific settings
class DynamicAgentModels(dict):
    """Proxy dictionary that always returns current values from settings.agent_models."""
    def get(self, key, default=None):
        return settings.agent_models.get(key, default)
    def __getitem__(self, key):
        return settings.agent_models[key]
    def __contains__(self, key):
        return key in settings.agent_models
    def __iter__(self):
        return iter(settings.agent_models)
    def __len__(self):
        return len(settings.agent_models)
    def __repr__(self):
        return repr(settings.agent_models)
    def __str__(self):
        return str(settings.agent_models)
    def items(self):
        return settings.agent_models.items()
    def keys(self):
        return settings.agent_models.keys()
    def values(self):
        return settings.agent_models.values()

AGENT_MODELS = DynamicAgentModels()

# Legacy constants for backward compatibility
QIP_VALORI_PATH = settings.QIP_VALORI_PATH
QIP_MAPPING_PATH = settings.QIP_MAPPING_PATH
ZONE_OMI_PROVINCIA_TORINO_GEOJSON = settings.ZONE_OMI_PROVINCIA_TORINO_GEOJSON
IMMOBILI_QUOTAZIONE_PATH = settings.IMMOBILI_QUOTAZIONE_PATH
ZONE_GRUPPO_QUOTAZIONI_PATH = settings.ZONE_GRUPPO_QUOTAZIONI_PATH
MISSING_QUOTAZIONI_IDS_PATH = settings.MISSING_QUOTAZIONI_IDS_PATH
DATASET_FULL = settings.DATASET_FULL
ZONE_OMI_GEOJSON = settings.ZONE_OMI_GEOJSON
TORINO_LAT = settings.TORINO_LAT
TORINO_LON = settings.TORINO_LON
