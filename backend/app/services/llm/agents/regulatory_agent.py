import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple, Union
import pandas as pd
import numpy as np

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage

from app.core.config import AGENT_MODELS
from app.services.llm.agents.base import BaseAgent
from app.services.llm.agents.schema import RegulatoryAgentResult, PromptRecord, RegulatoryResponse
from app.core.constants import REGULATORY_AGENT_COLUMNS
from app.services.llm.langchain_client import get_llm, invoke_with_langfuse
from app.services.llm.prompt_loader import get_system_prompt, get_user_template
from app.services.llm.rag import config as rag_config
from app.services.llm.rag.retriever import retrieve_with_trace
from app.utils.decorators import handle_agent_error, log_llm_usage
from app.utils.json_parser import safe_extract_json
from app.utils.scoring import (
    CONTINUOUS_OPERATORS,
    calculate_continuous_score,
    calculate_discrete_score,
    calculate_equality_score,
    calculate_range_score,
    is_numeric_column,
    normalize_operator,
    requirement_is_scorable,
    to_number,
    to_range,
)

logger = logging.getLogger(__name__)


def load_regulatory_context(
    query: str,
    *,
    retrieval_mode: Optional[str] = None,
    country: Optional[str] = None,
    use_case: Optional[str] = None,
) -> Tuple[str, List[str], List[Dict[str, Any]], Dict[str, Any]]:
    """Single entry point for the regulatory context that goes into a prompt.

    Dispatches on the retrieval mode (``rag_config.RETRIEVAL_MODE`` unless
    overridden — the override is what lets the downstream evaluation run the
    three arms in one process):

      none -> legacy folder loader ``load_regulatory_documents()`` (the
              pre-RAG production path; the folder is empty today)
      dump -> every in-scope chunk from the normative store, unranked
      rag  -> jurisdiction cascade + semantic top-k over the normative store

    Returns ``(documents_text, sources, images, trace)``. Retrieval failures
    (store missing, embedding backend down) degrade to "no documents" with the
    error recorded in the trace, rather than taking the whole agent down.
    """
    mode = (retrieval_mode or rag_config.RETRIEVAL_MODE).lower()
    country = country or rag_config.DEFAULT_COUNTRY

    if mode == "none":
        use_case = use_case or rag_config.DEFAULT_USE_CASE
        docs, sources, images = load_regulatory_documents()
        trace = {"mode": "none", "country": country, "use_case": use_case, "top_k": None,
                 "n_chunks": 0, "context_chars": len(docs) if sources else 0, "hits": []}
        return docs, sources, images, trace

    # No use case from the caller (the pipeline paths): route the query's
    # intended use onto the use-case vocabulary. Explicit callers (evals) skip it.
    routing = None
    if not use_case:
        if rag_config.USE_CASE_ROUTING:
            from app.services.llm.rag.router import route_use_case
            use_case, routing = route_use_case(query)
        else:
            use_case = rag_config.DEFAULT_USE_CASE

    try:
        docs, sources, trace = retrieve_with_trace(
            query, country=country, use_case=use_case, mode=mode,
            # extraction-oriented knobs (rag mode only; see rag config)
            frame=rag_config.QUERY_FRAMING, quantitative_only=rag_config.QUANT_ONLY,
        )
    except Exception as e:  # store/embedder unavailable — degrade, don't crash the agent
        logger.warning(f"Regulatory retrieval failed (mode={mode}): {e}")
        docs, sources = "No regulatory documents available.", []
        trace = {"mode": mode, "country": country, "use_case": use_case, "top_k": None,
                 "n_chunks": 0, "context_chars": 0, "hits": [], "error": str(e)}
    if routing is not None:
        trace["use_case_routing"] = routing
    return docs, sources, [], trace


def load_regulatory_documents() -> tuple[str, list[str], List[Dict[str, Any]]]:
    """
    Legacy loader (retrieval mode ``none``): read every file under
    docs/knowledge/normativa/ and concatenate it. Kept as the "before" arm of
    the RAG evaluation; prefer ``load_regulatory_context()``.
    Returns a tuple: (concatenated_text, file_paths_list, base64_images_list)
    """
    backend_dir = Path(__file__).parent.parent.parent.parent.parent
    normative_dir = backend_dir / "docs" / "knowledge" / "normativa"
    
    if not normative_dir.exists():
        return "No regulatory documents available.", [], []
    
    documents = []
    sources = []
    images = []
    
    for file_path in normative_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in [".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"]:
            try:
                if file_path.suffix.lower() in [".txt", ".md"]:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        documents.append(f"--- Document: {file_path.name} ---\n{content}\n")
                        sources.append(str(file_path.relative_to(normative_dir.parent.parent.parent)))
                elif file_path.suffix.lower() == ".json":
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = json.load(f) # Load JSON content
                        content_str = json.dumps(content, indent=2, ensure_ascii=False)
                        documents.append(f"--- Document: {file_path.name} (JSON) ---\n{content_str}\n")
                        sources.append(str(file_path.relative_to(normative_dir.parent.parent.parent)))
                elif file_path.suffix.lower() in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"]:
                    with open(file_path, "rb") as f:
                        image_data = base64.b64encode(f.read()).decode('utf-8')
                        mime_type = f"image/{file_path.suffix.lower()[1:]}"
                        if file_path.suffix.lower() in [".jpg", ".jpeg"]:
                            mime_type = "image/jpeg"
                        images.append({
                            "name": file_path.name,
                            "data": image_data,
                            "mime_type": mime_type
                        })
                        documents.append(f"--- Image: {file_path.name} (included for visual analysis) ---\n")
                        sources.append(str(file_path.relative_to(normative_dir.parent.parent.parent)))
                elif file_path.suffix.lower() in [".pdf", ".doc", ".docx"]:
                    documents.append(f"--- Document: {file_path.name} (binary file) ---\n")
                    sources.append(str(file_path.relative_to(normative_dir.parent.parent.parent)))
            except Exception:
                continue
    
    if not documents:
        return "No regulatory documents available.", [], []
    
    return "\n\n".join(documents), sources, images


def _rank_position(mask: pd.Series) -> pd.Series:
    """1 where the requirement matches, "N/A" elsewhere (object dtype).

    np.where(mask, 1, "N/A") mixes int and str, which numpy >= 2 rejects
    (DTypePromotionError) — that silently zeroed the whole regulatory score."""
    pos = pd.Series("N/A", index=mask.index, dtype=object)
    pos[mask.astype(bool)] = 1
    return pos


class RegulatoryAgent(BaseAgent):
    name = "regulatory-agent"

    def __init__(self, model_name: str = None):
        resolved_model = (
            model_name or AGENT_MODELS.get("regulatory_agent") or AGENT_MODELS.get("default")
        )
        self.llm = get_llm(model_name=resolved_model)
        
        self.system_prompt = get_system_prompt("regulatory_agent")
        self.user_template = get_user_template("regulatory_agent")

        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{system_content}"),
                ("user", "{user_content}"),
            ]
        )
        self.parser = StrOutputParser()
        self.chain = self.prompt | self.llm | self.parser

    @log_llm_usage
    @handle_agent_error(
        fallback_value=RegulatoryAgentResult(raw_text="{}", sources=[], prompt=None)
    )
    def run(self, *, query: str = None, mode: str = "filtering", **kwargs) -> Union[RegulatoryAgentResult, pd.DataFrame]:
        """
        Executes the agent in two modes:
        - filtering: Extracts regulatory requirements from documents.
        - ranking: Calculates a deterministic score (0-100) based on compliance requirements.
        """
        if mode == "filtering":
            return self._run_filtering(
                query=query,
                available_columns=kwargs.get("available_columns"),
                statistics=kwargs.get("statistics"),
                retrieval_mode=kwargs.get("retrieval_mode"),
                country=kwargs.get("country"),
                use_case=kwargs.get("use_case"),
            )
        elif mode == "ranking":
            return self._run_ranking(
                df=kwargs.get("df"),
                requirements=kwargs.get("requirements"),
                available_columns=kwargs.get("available_columns"),
                global_stats=kwargs.get("global_stats")
            )
        else:
            raise ValueError(f"Mode '{mode}' not supported by RegulatoryAgent.")

    def _run_filtering(
        self,
        query: str,
        available_columns: List[str] = None,
        statistics: dict = None,
        retrieval_mode: Optional[str] = None,
        country: Optional[str] = None,
        use_case: Optional[str] = None,
    ) -> RegulatoryAgentResult:
        # Regulatory context: RAG (default), full dump, or legacy folder — see
        # load_regulatory_context(). The user query is the retrieval query.
        regulatory_docs, sources, images, retrieval_trace = load_regulatory_context(
            query, retrieval_mode=retrieval_mode, country=country, use_case=use_case
        )

        # Insert available columns into the prompt
        # Priority to specifically passed available_columns, fallback to constants
        cols_list = available_columns or REGULATORY_AGENT_COLUMNS
        columns_str = "\n".join([f"- `{col}`" for col in cols_list]) if cols_list else "N/D"
        
        # Handling statistics (Data Distribution)
        stats_str = "No statistics available."
        if statistics:
            stats_str = json.dumps(statistics, indent=2, ensure_ascii=False)

        # Prepare inputs for templates. The user template in prompt_config.md
        # uses {normative_documents}; older revisions used {regulatory_documents}.
        # Fill both so the documents are never left as a literal placeholder.
        prompt_inputs = {
            "query": query,
            "regulatory_documents": regulatory_docs,
            "normative_documents": regulatory_docs,
            "statistics": stats_str,
            "reference_columns": columns_str
        }
        
        user_text = self.render_template(self.user_template, **prompt_inputs).strip()
        system_text = self.render_template(self.system_prompt, **prompt_inputs).strip()
        full_text = f"[SYSTEM]\n{system_text}\n\n[USER]\n{user_text}"

        
        if images:
            content = [{"type": "text", "text": full_text}]
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime_type']};base64,{img['data']}"}
                })
            message = HumanMessage(content=content)
            chain = self.llm | StrOutputParser()
            raw = chain.invoke([message])
        else:
            model_inputs = {
                "system_content": system_text,
                "user_content": user_text
            }
            raw = invoke_with_langfuse(self.chain, model_inputs)
        
        norm_data = safe_extract_json(raw, schema=RegulatoryResponse)
        has_requirements = norm_data.found if norm_data else False

        return RegulatoryAgentResult(
            raw_text=raw,
            sources=sources,
            has_requirements=has_requirements,
            retrieval=retrieval_trace,
            prompt=PromptRecord(
                system=system_text,
                user=user_text,
                full_text=full_text,
            ),
        )

    def _run_ranking(self, *, df: pd.DataFrame, requirements: List[Dict[str, Any]], available_columns: List[str] = None, global_stats: Dict[str, Any] = None) -> pd.DataFrame:
        """Ranking mode: 0-100 deterministic score calculation based on regulatory requirements."""
        if df is None or df.empty or not requirements:
            if df is not None:
                if "regulatory_score" not in df.columns:
                    df["regulatory_score"] = 0
            return df

        df_ranked = df.copy()
        
        # Identify all columns for which a requirement was expressed (that exist in the DF)
        all_req_columns = set()
        for req in requirements:
            col = req.get("target_column")
            if col and col in df_ranked.columns:
                all_req_columns.add(col)

        total_scores = pd.Series(0.0, index=df_ranked.index)
        valid_req_count = 0
        used_columns = set() # tracked for consistency
        transparency_cols = []

        for req in requirements:
            col = req.get("target_column")
            target_val = req.get("value")
            op = normalize_operator(req.get("operator", ">="))

            # Strict filter for score CALCULATION: allowed column, a value, and a
            # value shape the column can be scored against (same rule as the
            # orchestrator's "found" predicate, so no constant-0 requirement).
            if not requirement_is_scorable(req, df_ranked, allowed_columns=available_columns or None):
                continue

            valid_req_count += 1
            used_columns.add(col)
            numeric_col = is_numeric_column(df_ranked[col])
            target_num = to_number(target_val)
            is_numeric = numeric_col and (
                target_num is not None or (op == "BETWEEN" and to_range(target_val) is not None)
            )

            if is_numeric and op in CONTINUOUS_OPERATORS | {"==", "BETWEEN"}:
                vals = pd.to_numeric(df_ranked[col], errors="coerce").fillna(0)

                # Use centralized utility for continuous variables
                if op in CONTINUOUS_OPERATORS:
                    exclusive = req.get("exclusive", False)
                    req_score = calculate_continuous_score(vals, target_num, op, exclusive)
                elif op == "BETWEEN":
                    low, high = to_range(target_val)
                    req_score = calculate_range_score(df_ranked[col], low, high)
                else:  # ==  (robust spread from global stats, see calculate_equality_score)
                    req_score = calculate_equality_score(vals, target_num, (global_stats or {}).get(col))

                col_name = f"regulatory_partial_score_{col}"
                # Handle duplicate names if multiple requirements exist for the same column
                if col_name in df_ranked.columns:
                    idx = 1
                    while f"{col_name}_{idx}" in df_ranked.columns:
                        idx += 1
                    col_name = f"{col_name}_{idx}"
                
                # Store partial score for this numeric requirement
                df_ranked[col_name] = req_score.round(1)
                transparency_cols.append(col_name)
            
            else:
                # Categorical / string handling
                vals = df_ranked[col].astype(str).str.lower().str.strip()
                target_str = str(target_val).lower().strip()
                
                if col == "tipologia_bene_immobile":
                    # Score based on ranking position (similar to BuildingAgent)
                    if isinstance(target_val, str):
                        target_list = [v.lower().strip().strip("'\"") for v in target_val.split(",")]
                    elif isinstance(target_val, list):
                        target_list = [str(v).lower().strip().strip("'\"") for v in target_val]
                    else:
                        target_list = [target_str.strip("'\"")]

                    # Use centralized utility for discrete variables
                    req_score = calculate_discrete_score(df_ranked[col], target_list)
                    rank_pos = _rank_position(req_score > 0)  # Placeholder for positionality
                else:
                    # Determine match (boolean series)
                    if op == "==":
                        # Use centralized utility as exact match (single choice = 100)
                        req_score = calculate_discrete_score(df_ranked[col], [target_str])
                    elif op == "LIKE" or op == "IN":
                        if isinstance(target_val, str):
                            target_list = [v.lower().strip() for v in target_val.split(",")]
                        elif isinstance(target_val, list):
                            target_list = [str(v).lower().strip() for v in target_val]
                        else:
                            target_list = [target_str]
                        
                        # Use centralized utility for sets of values
                        req_score = calculate_discrete_score(df_ranked[col], target_list)
                    else:
                        req_score = (vals == target_str).astype(float) * 100.0
                    
                    is_match = req_score > 0
                    rank_pos = _rank_position(is_match)
                
                # Transparency Metadata for Categorical
                
                pos_col = f"regulatory_rank_position_{col}"
                if pos_col in df_ranked.columns:
                    idx = 1
                    while f"{pos_col}_{idx}" in df_ranked.columns:
                        idx += 1
                    pos_col = f"{pos_col}_{idx}"
                df_ranked[pos_col] = rank_pos
                transparency_cols.append(pos_col)
                
                # Store partial score for this categorical requirement
                col_name = f"regulatory_partial_score_{col}"
                if col_name in df_ranked.columns:
                    idx = 1
                    while f"{col_name}_{idx}" in df_ranked.columns:
                        idx += 1
                    col_name = f"{col_name}_{idx}"
                
                df_ranked[col_name] = req_score
                transparency_cols.append(col_name)
                
                # Special handling for property_technical: also show the original property_technical rank if available
                # Special handling for building: also show the original building rank if available
                if col == "tipologia_bene_immobile" and "building_rank_position" in df_ranked.columns:
                    transparency_cols.append("building_rank_position")
                
            total_scores += req_score

        if valid_req_count > 0:
            df_ranked["regulatory_score"] = (total_scores / valid_req_count).round(1)
        else:
            df_ranked["regulatory_score"] = 0.0

        weight_cols = [f"regulatory_weight_{c}" for c in used_columns]
        
        # Ensure weight columns exist (safety check)
        for wc in weight_cols:
            if wc not in df_ranked.columns:
                df_ranked[wc] = 0.0

        # Combine all requested columns and deduplicate while preserving order
        all_requested_cols = ["id", "regulatory_score"] + list(all_req_columns) + weight_cols + transparency_cols
        unique_cols = []
        for c in all_requested_cols:
            if c not in unique_cols and c in df_ranked.columns:
                unique_cols.append(c)
                
        return df_ranked[unique_cols]