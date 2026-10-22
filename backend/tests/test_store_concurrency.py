"""
Tests that parallel first use of the vector store opens it only once (no LLM,
temporary store — the real one is never touched).

The suite runs queries in parallel threads; before the lock, concurrent first
calls each built a chromadb PersistentClient and most failed ("Could not connect
to tenant default_tenant"), so those queries silently retrieved no documents.

Run:
    python tests/test_store_concurrency.py
"""

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.llm.rag import config, store


def _parallel_first_calls(n_threads=16):
    def one(_):
        try:
            return id(store.get_client()), store.get_collection().count(), None
        except Exception as e:  # noqa: BLE001 — the failure is what we count
            return None, None, f"{type(e).__name__}: {e}"
    with ThreadPoolExecutor(n_threads) as ex:
        return list(ex.map(one, range(n_threads)))


def test_parallel_first_calls_share_one_client():
    original = (config.CHROMA_PATH, store._client)
    try:
        for _ in range(3):                                   # the race shows on most fresh starts
            with tempfile.TemporaryDirectory() as tmp:
                config.CHROMA_PATH, store._client = Path(tmp) / "chroma", None
                results = _parallel_first_calls()
                errors = [e for _, _, e in results if e]
                assert errors == [], errors[:3]
                assert len({cid for cid, _, _ in results}) == 1      # one client for every thread
                store._client = None                                   # release before the dir goes
    finally:
        config.CHROMA_PATH, store._client = original


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed.")
