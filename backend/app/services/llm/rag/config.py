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
# Chunking strategy
# --------------------------------------------------------------------------
# "deterministic" -> regex/structure-aware splitter (chunker.py); fully offline.
# "llm"           -> a local model detects the document's section pattern from a
#                    sample and we slice deterministically at those offsets
#                    (llm_chunker.py). Robust across languages/formats without a
#                    per-country regex zoo. The model NEVER returns the legal
#                    text — only a boundary regex — so slicing stays lossless and
#                    reproducible. Falls back to "deterministic" if detection fails.
# The detected regex is cached per document, so the model runs once at ingestion
# and re-runs are reproducible regardless of the model chosen.
CHUNK_STRATEGY = os.getenv("RAG_CHUNK_STRATEGY", "llm")

# Detector backend: "openai" (hosted, most capable — the detected regex is cached
# so this runs once per document, ever) or "ollama" (local model, e.g. for a
# local-vs-hosted ablation). NOTE: gpt-5.4 must hit the real OpenAI endpoint, not
# the institutional vLLM base used for the gemma models — hence a separate base.
CHUNK_BACKEND = os.getenv("RAG_CHUNK_BACKEND", "openai")
CHUNK_MODEL = os.getenv("RAG_CHUNK_MODEL", "gpt-5.4")
CHUNK_OPENAI_BASE = os.getenv("RAG_CHUNK_OPENAI_BASE", "https://api.openai.com/v1")
CHUNK_SAMPLE_CHARS = int(os.getenv("RAG_CHUNK_SAMPLE_CHARS", "6000"))
# Hard ceiling on a single chunk. Detected sections are usually well-formed, but
# trailing material after the last matched heading can collect into one huge
# chunk (the London Plan produced a 96k-char one, ~24k tokens) that would swamp
# the whole top-k budget. Oversized chunks are sub-split, keeping their semantic
# ref and numbering the parts. Set above the largest legitimate article
# (~7.8k chars in the Italian corpus) so real articles stay intact.
CHUNK_MAX_CHARS = int(os.getenv("RAG_CHUNK_MAX_CHARS", "10000"))
CHUNK_DETECT_TIMEOUT = float(os.getenv("RAG_CHUNK_TIMEOUT", "180"))

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
