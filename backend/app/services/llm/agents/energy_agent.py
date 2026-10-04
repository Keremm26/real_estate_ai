import json
from typing import Any, Dict, List, Optional, Union
import pandas as pd
import numpy as np

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.core.config import settings, AGENT_MODELS
from app.services.llm.agents.base import BaseAgent
from app.services.llm.agents.schema import EnergyAgentResult, PromptRecord, EnergyResponse
from app.core.constants import ENERGY_AGENT_COLUMNS, ENERGY_SCORE_LEGEND
from app.services.llm.langchain_client import get_llm, invoke_with_langfuse, is_oss_model
from app.services.llm.prompt_loader import get_system_prompt, get_user_template
from app.utils.decorators import log_llm_usage
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


class EnergyAgentOutput(EnergyResponse):
    """Structured output schema for the Energy Agent (inherits from EnergyResponse)."""
    pass


class EnergyAgent(BaseAgent):
    name = "energy-agent"

    def __init__(self, model_name: str = None):
        resolved_model = (
            model_name or AGENT_MODELS.get("ape_agent") or AGENT_MODELS.get("default")
        )
        self.llm = get_llm(model_name=resolved_model)

        self.system_prompt = get_system_prompt("energy_agent")
        self.user_template = get_user_template("energy_agent")

        self.prompt_template = ChatPromptTemplate.from_messages(
            [
                ("system", "{system_content}"),
                ("user", "{user_content}"),
            ]
        )
        # Detect if we should use structured output (avoid for OSS models)
        if hasattr(self.llm, "with_structured_output") and not is_oss_model(resolved_model):
            self.structured_llm = self.llm.with_structured_output(EnergyAgentOutput, method="json_mode")
            self.chain = self.prompt_template | self.structured_llm
        else:
            from langchain_core.output_parsers import StrOutputParser
            self.chain = self.prompt_template | self.llm | StrOutputParser()

    @log_llm_usage
    def run(
        self,
        *,
        query: str = None,
        mode: str = "filtering",
        **kwargs
    ) -> Union[EnergyAgentResult, pd.DataFrame]:
        """
        Executes the agent in two modes:
        - filtering: Suggests SQL filters based on the query (LLM).
        - ranking: Calculates a 0-100 score based on EPC columns (Deterministic).
        """
        if mode == "filtering":
            return self._run_filtering(
                query=query,
                statistics=kwargs.get("statistics"),
                score_legend=kwargs.get("score_legend")
            )
        elif mode == "ranking":
            return self._run_ranking(
                df=kwargs.get("df"),
                requirements=kwargs.get("requirements"),
                global_stats=kwargs.get("global_stats")
            )
        else:
            raise ValueError(f"Mode '{mode}' not supported by EnergyAgent.")

    def _run_filtering(
        self,
        query: str,
        statistics: dict = None,
        score_legend: str = "",
    ) -> EnergyAgentResult:
        if not statistics and not query:
            return EnergyAgentResult(raw_text="EPC data not available.", prompt=None)

        stats_str = json.dumps(statistics, indent=2, ensure_ascii=False) if statistics else "N/D"
        
        # Format specific columns list
        columns_str = "\n".join([f"- `{col}`" for col in ENERGY_AGENT_COLUMNS])

        prompt_inputs = {
            "query": query,
            "statistics": stats_str,
            "score_legend": score_legend or ENERGY_SCORE_LEGEND,
            "reference_columns": columns_str
        }

        # Format prompts with variables
        rendered_system_prompt = self.render_template(self.system_prompt, **prompt_inputs).strip()
        user_text = self.render_template(self.user_template, **prompt_inputs).strip()
        full_text = f"[SYSTEM]\n{rendered_system_prompt}\n\n[USER]\n{user_text}"

        try:
            # We explicitly pass the system/user split as expected by Langchain call or prompt_template
            model_inputs = {
                "system_content": rendered_system_prompt,
                "user_content": user_text
            }
            structured_response = invoke_with_langfuse(self.chain, model_inputs)
            
            # If output is string, extract JSON manually
            if isinstance(structured_response, str) or hasattr(structured_response, 'content'):
                text_to_parse = structured_response.content if hasattr(structured_response, 'content') else structured_response
                structured_response = safe_extract_json(text_to_parse, schema=EnergyAgentOutput)
            
            if not structured_response:
                raise ValueError("Could not parse Energy requirements from LLM response")
                
            has_filters = structured_response.found

            return EnergyAgentResult(
                raw_text=json.dumps(structured_response.model_dump(), ensure_ascii=False),
                has_filters=has_filters,
                prompt=PromptRecord(
                    system=rendered_system_prompt,
                    user=user_text,
                    full_text=full_text,
                ),
            )
        except Exception as e:
            return EnergyAgentResult(
                raw_text=json.dumps({"error": str(e), "found": False, "requirements": []}),
                prompt=None,
            )

    def _run_ranking(self, *, df: pd.DataFrame, requirements: List[Dict[str, Any]] = None, global_stats: Dict[str, Any] = None) -> pd.DataFrame:
        """Ranking mode: 0-100 score calculation based on LLM requirements or deterministic logic."""
        if df is None or df.empty:
            if df is not None:
                df["energy_score"] = 0
            return df

        df_ranked = df.copy()
        
        # 1. If we have dynamic requirements from the LLM (filtering), use them to calculate the score
        if requirements:
            total_scores = pd.Series(0.0, index=df_ranked.index)
            valid_req_count = 0
            used_columns = set()
            transparency_cols = []

            for req in requirements:
                col = req.get("target_column")
                target_val = req.get("value")
                op = normalize_operator(req.get("operator", "=="))

                # Same rule as the orchestrator's "found" predicate.
                if not requirement_is_scorable(req, df_ranked):
                    continue

                valid_req_count += 1
                used_columns.add(col)
                
                # Special handling for energy class (categorical)
                if col in ["energy_class", "classe_target_ape", "classe_energetica_ape"]:
                    # Definitive ranking order: A4 is best (Rank 1), G is worst
                    classes_order = ["A4", "A3", "A2", "A1", "B", "C", "D", "E", "F", "G"]
                    
                    # Determine target set of classes based on operator and value
                    target_set = []
                    if isinstance(target_val, list):
                        target_set = [c for c in classes_order if c in [str(v).upper() for v in target_val]]
                    else:
                        target_val_str = str(target_val).upper().strip()
                        if op in [">=", ">"]:
                            try:
                                idx = classes_order.index(target_val_str)
                                target_set = classes_order[:idx+1]
                            except ValueError:
                                target_set = [target_val_str] if target_val_str in classes_order else []
                        elif op in ["<=", "<"]:
                            try:
                                idx = classes_order.index(target_val_str)
                                target_set = classes_order[idx:]
                            except ValueError:
                                target_set = [target_val_str] if target_val_str in classes_order else []
                        elif op in ["IN"]:
                             candidates = [v.strip().upper() for v in str(target_val).split(",")]
                             target_set = [c for c in classes_order if c in candidates]
                        else: # ==
                            target_set = [target_val_str] if target_val_str in classes_order else []

                    # If no target set could be determined but we have a value, use it as fallback
                    if not target_set and target_val:
                         target_set = [str(target_val).upper().strip()]

                    # Use centralized utility for discrete variables
                    req_score = calculate_discrete_score(df_ranked[col], target_set)
                    
                    # Store transparency metadata
                    df_ranked[f"energy_partial_score_{col}"] = req_score
                    transparency_cols.append(f"energy_partial_score_{col}")
                    
                else:
                    # Generic numeric handling
                    vals_raw = pd.to_numeric(df_ranked[col], errors="coerce")
                    is_missing = vals_raw.isna()
                    
                    # Use centralized utility for continuous variables
                    if op in CONTINUOUS_OPERATORS:
                        exclusive = req.get("exclusive", False)
                        req_score = calculate_continuous_score(vals_raw, to_number(target_val), op, exclusive)
                    elif op == "BETWEEN" and to_range(target_val) is not None:
                        low, high = to_range(target_val)
                        req_score = calculate_range_score(vals_raw, low, high)
                    else: # == or IN (fallback) logic using distance
                        vals = vals_raw.fillna(0)
                        target_num = to_number(target_val[0] if isinstance(target_val, list) and target_val else target_val)
                        col_stats = global_stats.get(col) if global_stats else None
                        req_score = calculate_equality_score(vals, target_num if target_num is not None else 0.0, col_stats)
                    
                    # Ensure 0 for missing values
                    req_score = req_score.where(~is_missing, 0)
                
                col_name = f"energy_partial_score_{col}"
                # Handle duplicate names if multiple requirements exist for the same column
                if col_name in df_ranked.columns:
                    idx = 1
                    while f"{col_name}_{idx}" in df_ranked.columns:
                        idx += 1
                    col_name = f"{col_name}_{idx}"

                # Store partial score for this requirement
                df_ranked[col_name] = req_score.round(1)
                transparency_cols.append(col_name)
                
                total_scores += req_score

            if valid_req_count > 0:
                df_ranked["energy_score"] = (total_scores / valid_req_count).round(1)
                
                # Add transparency: weight per column
                weight = round(1.0 / valid_req_count, 3)
                for col in used_columns:
                    df_ranked[f"energy_weight_{col}"] = weight
            else:
                df_ranked["energy_score"] = 0.0
                
            cols_to_return = ["id", "energy_score"] + list(used_columns)
            weight_cols = [f"energy_weight_{c}" for c in used_columns]
            
            # Combine all requested columns and deduplicate while preserving order
            all_requested_cols = cols_to_return + weight_cols + transparency_cols
            unique_cols = []
            for c in all_requested_cols:
                if c not in unique_cols and c in df_ranked.columns:
                    unique_cols.append(c)
            
            return df_ranked[unique_cols]

        # 2. Fallback Logic (Standard Deterministic)
        # If there are no specific requirements, assign 0 instead of average scores
        # to avoid polluting the ranking if the user didn't explicitly ask for energy data.
        used_col = None
        if "ape_total_points" in df_ranked.columns and not df_ranked["ape_total_points"].isna().all():
            used_col = "ape_total_points"
        elif "ape_score_total" in df_ranked.columns and not df_ranked["ape_score_total"].isna().all():
            used_col = "ape_score_total"

        if used_col:
            points_raw = pd.to_numeric(df_ranked[used_col], errors="coerce")
            is_missing = points_raw.isna()
            points = points_raw.fillna(0)
            
            col_stats = global_stats.get(used_col) if global_stats else None
            if col_stats and isinstance(col_stats, dict) and "min" in col_stats and "max" in col_stats:
                p_min = float(col_stats["min"])
                p_max = float(col_stats["max"])
            else:
                p_min = points.min()
                p_max = points.max()
            
            if p_max == p_min:
                partial_score = pd.Series(100.0, index=df_ranked.index)
            else:
                partial_score = ((points - p_min) / (p_max - p_min) * 100).clip(0, 100)
            
            # Set score to 0 for rows with missing values
            partial_score = partial_score.where(~is_missing, 0)
                
            df_ranked["energy_score"] = partial_score
            df_ranked[f"energy_partial_score_{used_col}"] = df_ranked["energy_score"]
            df_ranked["energy_score"] = df_ranked["energy_score"].round(1)
            
            cols_to_return = ["id", "energy_score", used_col]
            df_ranked[f"energy_weight_{used_col}"] = 1.0
            cols_to_return.append(f"energy_weight_{used_col}")
            cols_to_return.append(f"energy_partial_score_{used_col}")
            return df_ranked[cols_to_return]
        else:
            df_ranked["energy_score"] = 0.0
            return df_ranked[["id", "energy_score"]]

