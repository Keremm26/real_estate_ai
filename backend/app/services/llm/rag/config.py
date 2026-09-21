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

# Feature flag for the three-arm downstream evaluation (see eval/downstream.py).
# Read by RegulatoryAgent and by the baseline planner in graph_agent:
#   "none" -> legacy loader (docs/knowledge/normativa/ folder, now empty). This
#             is the production state BEFORE this work: the regulatory agent
#             runs with no documents and its ranking dimension is inert.
#   "dump" -> every in-scope chunk, unranked (the old "dump everything" strategy
#             applied to the new corpus — isolates the semantic-retrieval delta).
#   "rag"  -> router + jurisdiction cascade + semantic top-k (the new path).
RETRIEVAL_MODE = os.getenv("RAG_RETRIEVAL_MODE", "rag")
RETRIEVAL_MODES = ("none", "dump", "rag")

# Extraction-oriented retrieval — applied on the REGULATORY-AGENT path only
# (the gold-set evaluation embeds the raw queries). The agent's input is a
# property search ("immobile per residenza studenti, 60 posti letto"), not a
# legal question, so the raw embedding lands on descriptive sections rather
# than the dimensioning ones. Two cheap, ablatable fixes:
#   QUERY_FRAMING -> append a fixed English "regulatory framing" suffix before
#                    embedding. bge-m3 is cross-lingual, so one suffix serves
#                    every country (no per-language phrase list).
#   QUANT_ONLY    -> restrict the cascade scope to chunks flagged
#                    has_quantitative (the extractor only emits numeric
#                    requirements, so non-numeric chunks cannot help it).
# Measured with `eval.downstream --ablate` on 8 production-shaped queries (rank
# of the first DM 1256 dimensioning chunk, top-8):
#   raw 88% hit / MRR .370 · framing 62% / .124 · quant 100% / .688 · both 100% / .312
# Framing is net NEGATIVE — it matches the decrees' heading meta-language
# ("standard minimi dimensionali", Art. 2) instead of the numeric content — so
# it ships off; quantitative routing ships on. Both stay ablatable.
QUERY_FRAMING = os.getenv("RAG_QUERY_FRAMING", "0") == "1"
QUERY_FRAMING_TEXT = (
    "Regulatory requirements: minimum dimensional standards, minimum floor area "
    "per bed or occupant, sizing parameters and numeric thresholds."
)
QUANT_ONLY = os.getenv("RAG_QUANT_ONLY", "1") == "1"

# --------------------------------------------------------------------------
# Turin vertical-slice defaults (single-city dataset; threaded from graph
# state later when the pipeline goes multi-city)
# --------------------------------------------------------------------------
DEFAULT_COUNTRY = "IT"
DEFAULT_CITY = "Torino"
DEFAULT_USE_CASE = "student_housing"
