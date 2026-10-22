"""
Query router — maps a pipeline query's intended use onto the use-case tags.

The jurisdiction cascade filters chunks on ``use_case IN [<routed>, general]``.
Pipeline queries ("Cerca un immobile ... finalizzato a co-housing") carry the
intended use only as free text, so before retrieval one short LLM call reads it
and picks a tag from ``metadata.USE_CASE_DESCRIPTIONS``.

The fallback is deliberately conservative: anything not clearly matching a
specific tag — an unlisted use, no use stated, an unparseable answer, an
unreachable model — routes to "general", which scopes retrieval to rules that
apply across uses. Use-specific standards (e.g. student-residence room sizes)
therefore only reach the prompt when the query is actually about that use.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Tuple

from app.services.llm.rag import config
from app.services.llm.rag.metadata import USE_CASE_DESCRIPTIONS

logger = logging.getLogger(__name__)

_SYSTEM = """You classify a real-estate search query by the INTENDED USE of the property (what the buyer plans to make of it), into exactly one of these categories:

{categories}

Rules:
- Decide only from the intended use stated in the query; location, size, energy class and nearby services are irrelevant.
- Pick a specific category only when the query clearly states that use. If the use is different, unlisted, or not stated, pick "{fallback}".
- Answer with ONLY a JSON object: {{"use_case": "<category>", "reason": "<the words in the query that decided it>"}}"""


def _system_prompt() -> str:
    cats = "\n".join(f'- "{tag}": {desc}' for tag, desc in USE_CASE_DESCRIPTIONS.items())
    return _SYSTEM.format(categories=cats, fallback=config.ROUTER_FALLBACK_USE_CASE)


def _parse(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=512)
def _route_cached(query: str, model: str) -> Tuple[str, str, str]:
    """(use_case, reason, error) — cached per (query, model): the multi-agent and
    baseline paths both retrieve for the same query, so they route once."""
    from app.services.llm.langchain_client import get_llm  # lazy: avoid import cycle

    try:
        reply = get_llm(model_name=model, temperature=0.0).invoke(
            [("system", _system_prompt()), ("user", query)]
        )
        answer = _parse(getattr(reply, "content", "") or "")
    except Exception as e:  # noqa: BLE001 — model unreachable: degrade, don't crash
        logger.warning(f"Use-case routing failed, falling back to general: {e}")
        return config.ROUTER_FALLBACK_USE_CASE, "", str(e)

    tag = str(answer.get("use_case", "")).strip()
    if tag not in USE_CASE_DESCRIPTIONS:
        return config.ROUTER_FALLBACK_USE_CASE, "", f"answer outside vocabulary: {answer!r}"
    return tag, str(answer.get("reason", ""))[:200], ""


def route_use_case(query: str) -> Tuple[str, Dict[str, Any]]:
    """Return ``(use_case, routing_trace)`` for a query, on the agents' model."""
    from app.core.config import settings  # lazy: rag must import without app settings

    model = settings.AGENT_LLM_MODEL
    use_case, reason, error = _route_cached(query, model)
    trace = {"use_case": use_case, "model": model, "reason": reason}
    if error:
        trace["error"] = error
    return use_case, trace


# Area routing: some documents apply to only part of a city slice (a borough's
# own standards). One short call places the request in one of those areas or in
# none; anything unclear, unplaced or failed counts as none, so area-scoped rules
# reach the prompt only for requests located in their area.
_AREA_SYSTEM = """You decide where the property in a real-estate search request is located, relative to these areas:

{areas}

Rules:
- Answer an area only when the request places the property inside it: it names the area itself, or a neighbourhood, street, station or landmark that lies inside it.
- If the request places the property elsewhere, or does not say where it is, answer "none".
- Answer with ONLY a JSON object: {{"area": "<area or none>", "reason": "<the words in the request that decided it>"}}"""


@lru_cache(maxsize=512)
def _route_area_cached(query: str, model: str, areas: Tuple[str, ...]) -> Tuple[str, str, str]:
    """(area or "", reason, error) — cached per (query, model, areas)."""
    from app.services.llm.langchain_client import get_llm  # lazy: avoid import cycle

    system = _AREA_SYSTEM.format(areas="\n".join(f'- "{a}"' for a in areas))
    try:
        reply = get_llm(model_name=model, temperature=0.0).invoke([("system", system), ("user", query)])
        answer = _parse(getattr(reply, "content", "") or "")
    except Exception as e:  # noqa: BLE001 — model unreachable: degrade, don't crash
        logger.warning(f"Area routing failed, treating the request as outside the scoped areas: {e}")
        return "", "", str(e)
    area = str(answer.get("area", "")).strip()
    if area in areas:
        return area, str(answer.get("reason", ""))[:200], ""
    if area.lower() == "none":
        return "", str(answer.get("reason", ""))[:200], ""
    return "", "", f"answer outside vocabulary: {answer!r}"


def route_area(query: str, areas: List[str]) -> Tuple[str, Dict[str, Any]]:
    """Return ``(area, routing_trace)``: one of ``areas``, or "" when the request
    is located in none of them (or the answer is unusable)."""
    from app.core.config import settings  # lazy: rag must import without app settings

    model = settings.AGENT_LLM_MODEL
    area, reason, error = _route_area_cached(query, model, tuple(areas))
    trace = {"area": area or "none", "areas": list(areas), "model": model, "reason": reason}
    if error:
        trace["error"] = error
    return area, trace
