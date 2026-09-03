"""
Configuration for the RAG normative pipeline.

Everything tunable lives here as a single source of truth. The embedding
model in particular is a one-line swap (env ``RAG_EMBED_MODEL``): changing it
only requires re-running ingestion, since Chroma stores whatever vector
dimension the model emits — no code change needed.
"""

import os
from pathlib import Path

# backend/app/services/llm/rag/config.py -> parents[4] == backend/
BACKEND_DIR = Path(__file__).resolve().parents[4]

# --------------------------------------------------------------------------
# Local store + corpus locations (all gitignored under backend/data/rag/)
# --------------------------------------------------------------------------
RAG_DATA_DIR = BACKEND_DIR / "data" / "rag"
CHROMA_PATH = RAG_DATA_DIR / "chroma"          # persistent vector store
RAW_CACHE_DIR = RAG_DATA_DIR / "raw"           # fetched article texts, cached once
CORPUS_DIR = BACKEND_DIR / "docs" / "knowledge" / "corpus"  # the xlsx inventories

COLLECTION_NAME = "normative"

# --------------------------------------------------------------------------
# Embedding backend (Ollama, OpenAI-compatible local server)
# --------------------------------------------------------------------------
# NOTE: swappable. bge-m3 is the default (multilingual IT/ES/EN, 8k context,
# dense + sparse). To try another model: `export RAG_EMBED_MODEL=<name>` and
# re-run ingestion. Do NOT hardcode the vector dimension anywhere.
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "bge-m3")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_TIMEOUT_S = float(os.getenv("RAG_EMBED_TIMEOUT", "120"))
EMBED_BATCH_SIZE = int(os.getenv("RAG_EMBED_BATCH", "16"))

# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "8"))

# Feature flag for the before/after evaluation (see rag eval runner):
#   "rag"  -> router + cascade + semantic retrieval (the new path)
#   "dump" -> concatenate the whole in-scope corpus (reproduces the old baseline)
RETRIEVAL_MODE = os.getenv("RAG_RETRIEVAL_MODE", "rag")

# --------------------------------------------------------------------------
# Turin vertical-slice defaults (single-city dataset; threaded from graph
# state later when the pipeline goes multi-city)
# --------------------------------------------------------------------------
DEFAULT_COUNTRY = "IT"
DEFAULT_CITY = "Torino"
DEFAULT_USE_CASE = "student_housing"
