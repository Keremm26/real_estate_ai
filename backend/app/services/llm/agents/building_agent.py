import json
from typing import List, Union, Optional, Dict, Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
import pandas as pd
import numpy as np

from app.core.config import settings, AGENT_MODELS
from app.services.llm.agents.base import BaseAgent
from app.services.llm.agents.schema import PromptRecord, BuildingAgentResult, BuildingResponse
from app.core.constants import BUILDING_AGENT_COLUMNS
from app.services.llm.langchain_client import get_llm, invoke_with_langfuse
from app.services.llm.prompt_loader import get_system_prompt, get_user_template
from app.utils.decorators import handle_agent_error, log_llm_usage
from app.utils.json_parser import safe_extract_json
from app.utils.scoring import calculate_continuous_score, calculate_discrete_score


class BuildingAgent(BaseAgent):
    name = "building-agent"

    def __init__(self, model_name: str = None):
        resolved_model = (
            model_name
            or AGENT_MODELS.get("building_agent")
            or AGENT_MODELS.get("default")
        )
        self.llm = get_llm(model_name=resolved_model)

        # Load system and user prompts separately
        self.system_prompt = get_system_prompt("building_agent")
        self.user_template = get_user_template("building_agent")

        # Create ChatPromptTemplate with system/user separation
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
        fallback_value=BuildingAgentResult(raw_text="Error", prompt=None)
    )
    def run(self, *, query: str = None, mode: str = "filtering", **kwargs) -> Union[BuildingAgentResult, pd.DataFrame]:
        """
        Executes the agent in two modes:
        - filtering: Identifies relevant typologies ranked by relevance (LLM).
        - ranking: Calculates a 0-100 score based on typology rank and technical requirements.
        """
        if mode == "filtering":
            return self._run_filtering(
                query=query, 
                available_typologies=kwargs.get("available_typologies"),
                statistics=kwargs.get("statistics")
            )
        elif mode == "ranking":
            return self._run_ranking(
                df=kwargs.get("df"),
                ranked_typologies=kwargs.get("ranked_typologies"),
                requirements=kwargs.get("requirements"),
                global_stats=kwargs.get("global_stats")
            )
        else:
            raise ValueError(f"Mode '{mode}' not supported by BuildingAgent.")

    def _run_filtering(self, query: str, available_typologies: Union[str, List[str]], statistics: dict = None) -> BuildingAgentResult:
        # If available_typologies is a list, join it.
        if isinstance(available_typologies, list):
            descriptions_str = ", ".join([f'"{t}"' for t in available_typologies])
        else:
            descriptions_str = available_typologies or "N/D"
            
        stats_str = json.dumps(statistics, indent=2, ensure_ascii=False) if statistics else "N/D"

        # Format specific columns list
        columns_str = "\n".join([f"- `{col}`" for col in BUILDING_AGENT_COLUMNS])

        prompt_inputs = {
            "query": query, 
            "available_typologies": descriptions_str,
            "statistics": stats_str,
            "reference_columns": columns_str
        }

        # Format prompts with variables
        rendered_system_prompt = self.render_template(self.system_prompt, **prompt_inputs).strip()
        user_text = self.render_template(self.user_template, **prompt_inputs).strip()
        full_text = f"[SYSTEM]\n{rendered_system_prompt}\n\n[USER]\n{user_text}"

        response_text = invoke_with_langfuse(
            self.chain,
            {
                "system_content": rendered_system_prompt,
                "user_content": user_text,
            },
        )

        return BuildingAgentResult(
            raw_text=response_text,
            prompt=PromptRecord(
                system=rendered_system_prompt,
                user=user_text,
                full_text=full_text,
            ),
        )

    def _run_ranking(self, *, df: pd.DataFrame, ranked_typologies: List[str] = None, requirements: List[Dict[str, Any]] = None, global_stats: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Ranking mode: assigns a score (0-100) combining typology relevance and technical requirements (e.g., surface area).
        """
        if df is None or df.empty:
            if df is not None:
                df["building_score"] = 0
            return df

        df_ranked = df.copy()
        total_scores = pd.Series(0.0, index=df_ranked.index)
        weights_count = 0
        transparency_cols = []

        # Resolve the typology column: agents use "property_type", but the dataset
        # may name it "tipologia_bene_immobile". Pick whichever exists.
        typ_col = next(
            (c for c in ("property_type", "tipologia_bene_immobile") if c in df_ranked.columns),
            None,
        )

        # 1. Ranking by Typology (if present and the column exists)
        if ranked_typologies and typ_col:
            weights_count += 1
            # Use centralized utility for typologies
            typ_scores = calculate_discrete_score(df_ranked[typ_col], ranked_typologies)
            df_ranked["building_typology_score"] = typ_scores
            total_scores += typ_scores
            transparency_cols.append("building_typology_score")

        # 2. Ranking by Technical Requirements (e.g. superficie_di_riferimento_mq)
        if requirements:
            technical_score_sum = pd.Series(0.0, index=df_ranked.index)
            technical_req_count = 0
            
            for req in requirements:
                col = req.get("target_column")
                target_val = req.get("value")
                op = str(req.get("operator", "==")).upper()
                
                if not col or col not in df_ranked.columns or col in ("property_type", typ_col):
                    continue
                
                technical_req_count += 1
                series = pd.to_numeric(df_ranked[col], errors="coerce").fillna(0)
                
                # Use centralized utility for continuous variables
                try:
                    T = float(target_val) if target_val is not None else 1.0
                    is_numeric_req = True
                except (ValueError, TypeError):
                    # Se non è convertibile in float (es. codice_comune 'L219'), 
                    # lo trattiamo come confronto discreto
                    T = target_val
                    is_numeric_req = False

                if is_numeric_req and op in [">=", ">", "<=", "<"]:
                    exclusive = req.get("exclusive", False)
                    req_score = calculate_continuous_score(series, T, op, exclusive)
                else: # Equality or Non-numeric
                    if is_numeric_req:
                        diff = np.abs(series - T)
                        stats = global_stats.get(col) if global_stats else None
                        if stats and "max" in stats and "min" in stats:
                            range_val = max(1, stats["max"] - stats["min"])
                            req_score = (100 - (diff / range_val * 100)).clip(0, 100)
                        else:
                            req_score = (series == T).astype(float) * 100
                    else:
                        # Discrete comparison for strings (e.g., municipality code)
                        req_score = (df_ranked[col].astype(str) == str(T)).astype(float) * 100
                
                col_name = f"building_partial_score_{col}"
                # Handle duplicate names if multiple requirements exist for the same column
                if col_name in df_ranked.columns:
                    idx = 1
                    while f"{col_name}_{idx}" in df_ranked.columns:
                        idx += 1
                    col_name = f"{col_name}_{idx}"
                
                df_ranked[col_name] = req_score
                technical_score_sum += req_score
                transparency_cols.append(col_name)

            if technical_req_count > 0:
                weights_count += 1
                total_scores += (technical_score_sum / technical_req_count)

        # Final score calculation
        if weights_count > 0:
            df_ranked["building_score"] = (total_scores / weights_count).round(1).clip(0, 100)
        else:
            df_ranked["building_score"] = 100.0 if not (ranked_typologies or requirements) else 0.0

        # Combine all requested columns and deduplicate while preserving order
        all_requested_cols = ["id", "building_score"] + ([typ_col] if typ_col else []) + transparency_cols
        unique_cols = []
        for c in all_requested_cols:
            if c not in unique_cols and c in df_ranked.columns:
                unique_cols.append(c)

        return df_ranked[unique_cols]
