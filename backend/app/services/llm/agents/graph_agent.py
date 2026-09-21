from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypedDict, Union

from langgraph.graph import END, StateGraph
import numpy as np
import pandas as pd
import tabulate
import sqlglot
from sqlglot import exp, parse_one

from app.services.analysis.ranking import calculate_ranking_score
from app.core.config import settings
from app.core.constants import (
    SCORE_LEGEND, 
    ENERGY_SCORE_LEGEND, 
    ENERGY_AGENT_COLUMNS, 
    REGULATORY_AGENT_COLUMNS, 
    PROXIMITY_AGENT_COLUMNS, 
    BUILDING_AGENT_COLUMNS, 
    ALL_AGENT_COLUMNS
)

MAX_ITEMS_FOR_LLM = settings.MAX_ITEMS_FOR_LLM
MAX_ITEMS_FOR_MAP = settings.MAX_ITEMS_FOR_MAP
MAX_LLM_CAP = settings.MAX_LLM_CAP
from app.data.loaders import get_coordinates
from app.data.processors import calculate_travel_times_df
from app.services.llm.agents.energy_agent import EnergyAgent
from app.services.llm.agents.base import BaseAgent
from app.services.llm.agents.broker_agent import BrokerAgent
from app.services.llm.agents.evaluation_agent import EvaluationAgent
from app.services.llm.agents.location_agent import LocationAgent
from app.services.llm.agents.regulatory_agent import RegulatoryAgent
from app.services.llm.agents.proximity_agent import ProximityAgent
from app.services.llm.agents.schema import (
    AgentContext,
    EvaluationAgentResponse,
    RegulatoryAgentResult,
    BuildingAgentResult,
    RankingAgentResult,
    RankingWeights,
    RegulatoryResponse,
    BuildingResponse,
    LocationResponse,
    EnergyResponse,
    EvaluationResult,
    EvaluationList,
    EnergyAgentResult,
    ProximityAgentResult,
    Place,
)
from app.services.llm.agents.sql_agent import SQLAgent, _clean_sql
from app.services.llm.agents.building_agent import BuildingAgent
from app.services.llm.agents.ranking_agent import RankingAgent
from app.services.llm.agents.relaxation_agent import RelaxationAgent
from app.utils.logger import logger
from app.utils.json_parser import safe_extract_json
from app.utils.run_json_logger import get_run_logger
from dataclasses import dataclass, field
import sqlparse

@dataclass
class OrchestratorResult:
    """Result of the orchestration process for real estate analysis.

    Contains the final processed dataframe, location information, and
    metadata about the agentic workflow execution.
    """
    map_df: pd.DataFrame
    location: List[List[Union[str, float]]]
    status_msg: str
    gemini_responses: Dict[str, Any]
    where_clause: str
    sql_query: str
    context: AgentContext
    match_count: int = 0
    broker_summary: Optional[str] = None
    agent_trace: Optional[List[Dict[str, Any]]] = field(default_factory=list)
    relaxation_applied: bool = False
    relaxation_proposals: List[Dict[str, Any]] = field(default_factory=list)
    relaxation_count: int = 0


class GraphState(TypedDict):
    """Internal state for the LangGraph orchestration workflow.

    Maintains query context, intermediate results from specialized agents,
    and orchestration flags for managing the execution flow.
    """
    query: str
    dataset_key: str
    base_dataset: Any  # Reference to the source dataset DataFrame
    dataset_path: Optional[str]  # Path to the source file
    db_schema: Dict[str, Any]
    db_metadata: Dict[str, Any]  # Dataset and database metadata for LLM reasoning
    dataset_metadata: Dict[str, Any]  # Lightweight metadata summaries

    # Configuration flags
    analysis_mode: str
    architecture: str
    
    # Intermediate results
    location_payload: List[List[Union[str, float]]]
    use_case_str: str
    building_result: Optional[BuildingAgentResult]
    proximity_result: Optional[ProximityAgentResult]
    energy_result: Optional[EnergyAgentResult]
    regulatory_result: Optional[RegulatoryAgentResult]
    ranking_result: Optional[RankingAgentResult]
    sql_query: str
    selected_data: pd.DataFrame  # Results filtered by SQL
    execution_error: Optional[str]
    retry_count: int
    status_msg: str
    gemini_responses: Dict[str, Any]
    context: AgentContext
    where_clause: str
    broker_summary: str  # Final narrative summary from the Senior Broker agent
    sql_history: List[str]  # History of SQL queries for debugging/retry analysis

    match_count: int
    agent_trace: List[Dict[str, Any]]
    
    # Execution configuration
    llm_limit: Optional[int]
    map_limit: Optional[int]
    metro_graph: Any
    disabled_agents: List[str]  # List of agents to skip during execution

    # Workflow management
    set_progress: Optional[Callable[[Any], None]]
    step_definitions: List[Dict[str, str]]
    steps_state: List[Dict[str, Any]]
    last_retry_reason: Optional[str]
    relaxation_applied: bool
    relaxation_proposals: List[Dict[str, Any]]
    relaxation_count: int
    use_data_knowledge: bool  # Whether data statistics are passed to agents
    use_relaxation: bool  # Whether to use query relaxation if 0 results found


class GraphOrchestratorAgent(BaseAgent):
    """Main orchestrator for the property search multi-agent system.

    This agent uses a state graph to coordinate specialized sub-agents
    (SQL, POI, Energy, etc.) for complex real estate queries. It supports
    both multi-agent refinement and unified baseline planning modes.
    """

    name = "graph-orchestrator-agent"

    def __init__(
        self,
        *,
        analysis_mode: str = "agent",
        architecture: str = "multiagent",
        execute_sql_fn: Optional[
            Callable[[str, pd.DataFrame], tuple[pd.DataFrame, Optional[str]]]
        ] = None,
        location_agent: Optional[LocationAgent] = None,
        sql_agent: Optional[SQLAgent] = None,
        evaluation_agent: Optional[EvaluationAgent] = None,
        building_agent: Optional[BuildingAgent] = None,
        energy_agent: Optional[EnergyAgent] = None,
        proximity_agent: Optional[ProximityAgent] = None,
        regulatory_agent: Optional[RegulatoryAgent] = None,
    ) -> None:
        """Initialize the graph orchestrator with its specialized sub-agents.

        Args:
            analysis_mode: Determines if specialized agents ('agent') or a unified planner ('classic') is used.
            architecture: The agentic architecture pattern to follow.
            execute_sql_fn: Function to execute the generated SQL against the dataset.
            location_agent: Agent for geographic parsing and radius estimation.
            sql_agent: Agent for DuckDB SQL generation.
            evaluation_agent: Agent for qualitative property evaluation.
            building_agent: Agent for technical building attribute analysis.
            energy_agent: Agent for energy performance (EPC) analysis.
            proximity_agent: Agent for proximity analysis of services and amenities.
            regulatory_agent: Agent for regulatory and compliance analysis.
        """
        if execute_sql_fn is None:
            raise ValueError("execute_sql_fn is required.")

        self.analysis_mode: str = (analysis_mode or "agent").lower()
        self.architecture: str = (architecture or "multiagent").lower()
        self.is_agent_mode: bool = self.analysis_mode == "agent"
        self.execute_sql_fn = execute_sql_fn

        self.location_agent = location_agent or LocationAgent()
        self.sql_agent = sql_agent or SQLAgent()
        self.evaluation_agent = evaluation_agent or EvaluationAgent()
        self.broker_agent = BrokerAgent()
        self.building_agent = building_agent or BuildingAgent()
        self.energy_agent = energy_agent or EnergyAgent()
        self.proximity_agent = proximity_agent or ProximityAgent()
        self.regulatory_agent = regulatory_agent or RegulatoryAgent()
        self.ranking_agent = RankingAgent()
        self.relaxation_agent = RelaxationAgent()

        self.workflow = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Define and compile the LangGraph workflow structure.
        
        The workflow is organized into the three MURENA core phases:
        - Phase 1: Multi-Agent Extraction (Identification of requirements)
        - Phase 2: Technical Standardization & Scoring (SQL generation and ranking)
        - Phase 3: Qualitative Evaluation & Review (Final narrative and review)

        Returns:
            A compiled StateGraph representing the multi-agent workflow.
        """
        workflow = StateGraph(GraphState)

        # Register processing nodes
        # Phase 1: Contextual Extraction
        workflow.add_node("analyze_request", self._analyze_request)
        
        # Phase 2: Technical Standardization & Search
        workflow.add_node("generate_sql", self._generate_sql)
        workflow.add_node("execute_sql", self._execute_sql)
        workflow.add_node("handle_retry", self._handle_retry)
        workflow.add_node("fallback_results", self._fallback_results)
        workflow.add_node("enrich_results", self._enrich_results)
        workflow.add_node("calculate_ranking_weights", self._calculate_ranking_weights)
        workflow.add_node("rank_results", self._rank_results)
        
        # Phase 3: Qualitative Evaluation & Strategic Review
        workflow.add_node("evaluate_results", self._evaluate_results)
        workflow.add_node("broker_review", self._broker_review)
        workflow.add_node("relax_query", self._relax_query)
        workflow.add_node("finalize_results", self._finalize_results)

        # Configure workflow edges and entry point
        workflow.set_entry_point("analyze_request")
        workflow.add_edge("analyze_request", "generate_sql")
        workflow.add_edge("generate_sql", "execute_sql")
        workflow.add_edge("handle_retry", "generate_sql")

        # Routing logic for SQL execution results
        workflow.add_conditional_edges(
            "execute_sql",
            self._check_sql_execution,
            {
                "retry": "handle_retry",
                "continue": "enrich_results",
                "fallback": "fallback_results",
                "relax": "relax_query",
            },
        )
        workflow.add_edge("relax_query", "execute_sql")

        # Finalization pipeline
        workflow.add_edge("fallback_results", "enrich_results")
        workflow.add_edge("enrich_results", "calculate_ranking_weights")
        workflow.add_edge("calculate_ranking_weights", "rank_results")
        workflow.add_edge("rank_results", "evaluate_results")
        
        workflow.add_conditional_edges(
            "evaluate_results",
            self._should_broker_review,
            {
                "continue": "broker_review",
                "skip": "finalize_results",
            },
        )
        workflow.add_edge("broker_review", "finalize_results")
        workflow.add_edge("finalize_results", END)

        return workflow.compile()

    def run(
        self,
        *,
        query: str,
        dataset_key: str,
        base_dataset: pd.DataFrame,
        db_schema: Dict[str, Any],
        ape_df: pd.DataFrame = None,
        dataset_path: Optional[str] = None,
        set_progress: Optional[Callable[[Any], None]] = None,
        llm_limit: Optional[int] = None,
        map_limit: Optional[int] = None,
        metro_graph: Optional[Any] = None,
        disabled_agents: Optional[List[str]] = None,
        use_data_knowledge: bool = True,
        use_relaxation: bool = True,
        analysis_mode: str = "agent",
        architecture: str = "multiagent",
    ) -> OrchestratorResult:
        """Execute the full agentic orchestration pipeline for a user query.

        Args:
            query: The natural language search query.
            dataset_key: Identifier for the target dataset.
            base_dataset: The source DataFrame to analyze.
            db_schema: Structural information about the dataset.
            ape_df: Optional energy performance data.
            dataset_path: File system path to the source dataset.
            set_progress: Callback function for real-time progress updates.
            llm_limit: Maximum number of records to process with LLM.
            map_limit: Maximum number of records to return for spatial visualization.
            metro_graph: Graph structure for transportation analysis.
            disabled_agents: List of agents to skip during this run.
            use_data_knowledge: Whether to augment prompts with dataset statistics.
            analysis_mode: Selection of orchestration methodology.
            architecture: Architectural pattern for agent coordination.

        Returns:
            An OrchestratorResult containing processed data and agentic traces.
        """
        # Define progress steps for the orchestration workflow (in English for publication)
        step_definitions = [
            {"key": "ranking_init", "label": "Analyzing user request..."},
            {"key": "building", "label": "Evaluating technical building characteristics..."},
            {"key": "location", "label": "Identifying search geographic area..."},
            {"key": "energy", "label": "Analyzing building energy performance (EPC)..."},
            {"key": "regulatory", "label": "Verifying regulatory compliance..."},
            {"key": "proximity", "label": "Scanning nearby services and amenities..."},
            {"key": "ranking", "label": "Calculating relevance scores..."},
            {"key": "evaluation", "label": "Generating qualitative building justifications..."},
            {"key": "broker", "label": "Synthesizing executive summary..."},
        ]

        # Load centralized metadata
        from app.core.constants import DB_METADATA
        db_metadata = DB_METADATA

        # Extract lightweight metadata summaries for prompt context
        dataset_metadata = {
            "columns": list(base_dataset.columns) if base_dataset is not None else [],
            "sample_columns": (
                ", ".join(base_dataset.columns[:15]) if base_dataset is not None else ""
            ),
            "typologies": [],
        }
        
        if (
            base_dataset is not None
            and "tipologia_bene_immobile" in base_dataset.columns
        ):
            dataset_metadata["typologies"] = [
                str(x)
                for x in base_dataset["tipologia_bene_immobile"].unique()
                if pd.notna(x)
            ]

        # Suppress future warnings for downcasting during finalization
        pd.set_option('future.no_silent_downcasting', True)

        initial_state: GraphState = {
            "agent_trace": [],
            "query": query,
            "dataset_key": dataset_key,
            "base_dataset": base_dataset,
            "dataset_path": dataset_path,
            "db_schema": db_schema,
            "db_metadata": db_metadata,
            "dataset_metadata": dataset_metadata,
            "analysis_mode": analysis_mode.lower(),
            "architecture": architecture.lower(),
            "disabled_agents": disabled_agents or [],
            "use_data_knowledge": use_data_knowledge,
            "use_relaxation": use_relaxation,
            "location_payload": [],
            "use_case_str": "",
            "building_result": None,
            "proximity_result": None,
            "energy_result": None,
            "regulatory_result": None,
            "ranking_result": None,
            "sql_query": "",
            "selected_data": pd.DataFrame(),
            "execution_error": None,
            "retry_count": 0,
            "status_msg": "",
            "gemini_responses": {
                "analysis_mode": analysis_mode.lower(),
                "dataset_key": dataset_key,
            },
            "context": AgentContext(user_query=query),
            "where_clause": "",
            "match_count": 0,
            "broker_summary": "",
            "llm_limit": llm_limit,
            "map_limit": map_limit,
            "metro_graph": metro_graph,
            "set_progress": set_progress,
            "step_definitions": step_definitions,
            "steps_state": [
                {"label": s["label"], "state": "pending", "detail": ""}
                for s in step_definitions
            ],
            "last_retry_reason": None,
            "relaxation_applied": False,
            "relaxation_proposals": [],
            "relaxation_count": 0,
            "sql_history": [],
        }

        # Invoke the workflow with recursion limit to prevent infinite loops
        final_state = self.workflow.invoke(initial_state, {"recursion_limit": 30})

        # Release the large base_dataset reference for garbage collection
        final_state.pop("base_dataset", None)

        result_df = final_state["selected_data"]

        # Ensure evaluation metadata exists
        if not result_df.empty and "is_evaluated" not in result_df.columns:
            result_df["is_evaluated"] = False

        # Export trace to JSON if enabled for evaluation/debugging
        if settings.ENABLE_RUN_JSON_EXPORT:
            logger.info("JSON export enabled, saving run results...")
            try:
                run_id = final_state.get("run_id", f"run_{int(time.time()*1000)}")
                results_json = {
                    "buildings": (
                        result_df.to_dict(orient="records")
                        if not result_df.empty
                        else []
                    )
                }
                
                json_logger = get_run_logger()
                json_logger.log_run(
                    run_id=run_id,
                    query=query,
                    status="completed",
                    gemini_responses=final_state["gemini_responses"],
                    results=results_json,
                )
            except Exception as e:
                logger.error(f"Failed to export run results to JSON: {e}")

        return OrchestratorResult(
            map_df=result_df,
            location=final_state["location_payload"],
            status_msg=final_state["status_msg"],
            gemini_responses=final_state["gemini_responses"],
            where_clause=final_state["where_clause"],
            sql_query=final_state["sql_query"],
            context=final_state["context"],
            match_count=final_state["match_count"],
            broker_summary=final_state.get("broker_summary", ""),
            agent_trace=final_state.get("agent_trace", []),
            relaxation_applied=final_state.get("relaxation_applied", False),
            relaxation_proposals=final_state.get("relaxation_proposals", []),
            relaxation_count=final_state.get("relaxation_count", 0),
        )


    def _unified_analysis(self, state: GraphState) -> GraphState:
        """Execute the analysis via a single unified prompt (Baseline Planner mode).

        This serves as a baseline for the multi-agent orchestration, generating
        both the SQL query and ranking weights in a single LLM pass.
        """
        from app.services.llm.langchain_client import get_llm
        from langchain_core.messages import HumanMessage
        
        query: str = state["query"]
        self._update_progress(state, "ranking_init", "Generating unified analysis plan...")

        # 1. Metadata and prompt preparation
        db_metadata: Dict[str, Any] = state.get('db_metadata', {}).copy()
        if not state.get("use_data_knowledge", True):
            # If data knowledge is disabled, strip statistics and unique values for baseline comparison
            if "fields" in db_metadata:
                clean_fields = {}
                for col_name in db_metadata["fields"].keys():
                    clean_fields[col_name] = {}
                db_metadata["fields"] = clean_fields
        
        # Load centralized system prompt
        from app.services.llm.prompt_loader import get_system_prompt
        template = get_system_prompt("baseline_planner")
        if not template:
            logger.error("Template baseline_planner not found in prompt_config.md")
            template = "Generate JSON with 'sql' (DuckDB query) and 'layer2' (ranking weights)."

        # Fetch distribution statistics (supporting data knowledge)
        stats = {}
        if state.get("use_data_knowledge", True):
            stats = self._get_column_statistics(
                columns=["superficie_di_riferimento_mq", "tipologia_bene_immobile", "classe_energetica_ape"],
                dataset_df=state.get("base_dataset"),
                dataset_path=state.get("dataset_path"),
                db_metadata=state.get("db_metadata")
            )

        # 1.5 Load regulatory references — same retrieval path (and same
        # RETRIEVAL_MODE flag) as the regulatory agent, so baseline vs
        # multi-agent comparisons see identical regulatory context.
        from app.services.llm.agents.regulatory_agent import load_regulatory_context
        regulatory_text, _, _, regulatory_trace = load_regulatory_context(query)
        state.setdefault("gemini_responses", {})["regulatory_retrieval"] = regulatory_trace

        # 2. LLM Invocation (Unified Architect pass)
        prompt_text = (
            "========================================\nUSER QUERY (INPUT)\n"
            f"{query}\n========================================\n"
            f"DATASET METADATA:\n{json.dumps(db_metadata, ensure_ascii=False)}\n\n"
            f"DISTRIBUTION STATISTICS:\n{json.dumps(stats, ensure_ascii=False)}\n\n"
            f"REFERENCE REGULATORY:\n{regulatory_text}\n\n"
            f"REASONING INSTRUCTIONS & OUTPUT FORMAT:\n{template}"
        )

        llm = get_llm()
        start_t = time.time()
        response = llm.invoke([HumanMessage(content=prompt_text)])
        duration_ms = (time.time() - start_t) * 1000
        llm_output = response.content if hasattr(response, "content") else str(response)

        # 3. Parsing and State population
        data = safe_extract_json(llm_output) or {}
        
        # SQL Query Extraction
        sql_query = data.get("sql", {}).get("query", "")
        if sql_query:
            sql_query = _clean_sql(sql_query)
            if "SELECT" not in sql_query.upper():
                logger.warning(f"Baseline planner generated invalid SQL: {sql_query}")
        state["sql_query"] = sql_query
        
        # Ranking weights mapping
        # Maps output keys to the internal ranking schema naming conventions
        weights_data = data.get("layer2", {}).get("weights", {})
        weights = RankingWeights(
            location=weights_data.get("location", weights_data.get("localizzazione", 0.2)),
            regulatory=weights_data.get("regulatory", weights_data.get("normativa", 0.2)),
            energy=weights_data.get("energy", weights_data.get("energia", 0.2)),
            building=weights_data.get("building", weights_data.get("tipologia", 0.2)),
            proximity=weights_data.get("proximity", weights_data.get("servizi", 0.2))
        )
        state["ranking_result"] = RankingAgentResult(raw_text=llm_output, weights=weights)

        # 4. Population of technical results (layer 1) for compatibility with deterministic ranking
        # This ensures that weights are not zeroed in _rank_results if the agent didn't run.
        layer1 = data.get("layer1", {}).get("analysis", {})
        if layer1:
            # Helper to extract parameters or filters in an agnostic way
            def get_reqs(obj):
                if not isinstance(obj, dict): return {}
                return obj.get("parameters") or obj.get("filters") or obj.get("requirements") or obj.get("parametri") or {}

            # 1. Building typology -> BuildingAgentResult
            typ = layer1.get("building", layer1.get("tipologia", {}))
            if typ.get("found"):
                reqs = get_reqs(typ)
                # Attempt to retrieve typologies
                typs = reqs.get("tipologia_bene_immobile", []) if isinstance(reqs, dict) else []
                # If it's not a list but a string (common LLM error), convert it
                if isinstance(typs, str): typs = [typs]
                
                payload = {
                    "typologies": typs if typs else ["Apartment"], # Fallback to a safe value if found=True
                    "found": True,
                    "requirements": [{"column": "superficie_di_riferimento_mq"}] 
                }
                state["building_result"] = BuildingAgentResult(
                    raw_text=json.dumps(payload),
                    prompt=None
                )

            # 2. Location -> context.locations
            loc = layer1.get("location", layer1.get("localizzazione", {}))
            if loc.get("found"):
                params = get_reqs(loc)
                if isinstance(params, dict) and (params.get("latitude") or params.get("latitudine") or params.get("coordinate")):
                    lat = params.get("latitude") or params.get("latitudine")
                    lon = params.get("longitude") or params.get("longitudine")
                    if not lat and params.get("coordinate"):
                         coord = params.get("coordinate")
                         if isinstance(coord, dict):
                            lat, lon = coord.get("lat"), coord.get("lon")
                    
                    if lat and lon:
                        place = Place(
                            name="Planer coordinates",
                            lat=lat,
                            lon=lon,
                            radius_km=params.get("radius_km", params.get("raggio_km", 3.0))
                        )
                        state["context"].locations = [place]

            # 3. Energy -> EnergyAgentResult
            en = layer1.get("energy", layer1.get("energia", {}))
            if en.get("found"):
                payload = {"found": True, "requirements": [{"column": "classe_energetica_ape"}]}
                state["energy_result"] = EnergyAgentResult(raw_text=json.dumps(payload))

            # 4. Proximity -> ProximityAgentResult
            ser = layer1.get("proximity", layer1.get("servizi", {}))
            if ser.get("found"):
                r_list = get_reqs(ser).get("amenity_proximity") or ["healthcare"] if isinstance(get_reqs(ser), dict) else ["healthcare"]
                payload = {"found": True, "requirements": [{"amenity": s} for s in r_list]}
                state["proximity_result"] = ProximityAgentResult(raw_text=json.dumps(payload))

            # 5. Regulatory -> RegulatoryAgentResult
            reg = layer1.get("regulatory", layer1.get("normativa", {}))
            if reg.get("found"):
                dest = get_reqs(reg).get("use_destination") if isinstance(get_reqs(reg), dict) else "Other"
                payload = {"found": True, "requirements": [{"notes": dest or "Regulatory analysis"}]}
                state["regulatory_result"] = RegulatoryAgentResult(raw_text=json.dumps(payload))

        # Trace and Gemini responses for UI compatibility
        state["gemini_responses"]["baseline_planner"] = data
        self._log_execution(state, "baseline-planner", llm_output, duration_ms)
        
        # Technical results mocking to avoid failure in downstream nodes
        # (Optional: layer1 could be mapped here if useful)
        self._update_progress(state, "ranking_init", "Plan generated successfully.", status="done")
        
        return state

    # ------------------------------------------------------------------
    # Shared Pipeline Nodes
    # ------------------------------------------------------------------

    def _log_execution(
        self,
        state: GraphState,
        agent_name: str,
        result: Any,
        duration_ms: float,
        mode: str = "extraction",
        global_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Logs agent execution to the trace with structured input/output extraction."""
        if "agent_trace" not in state:
            state["agent_trace"] = []
            
        # Extract input from prompt record
        input_data = None
        if hasattr(result, 'prompt') and result.prompt:
            prompt = result.prompt
            if hasattr(prompt, 'model_dump'):
                prompt_dict = prompt.model_dump()
                # Prefer full_text if available, otherwise system/user
                if prompt_dict.get('full_text'):
                    input_data = prompt_dict['full_text']
                elif prompt_dict.get('system') or prompt_dict.get('user'):
                    input_data = prompt_dict
                else:
                    input_data = prompt_dict
            else:
                input_data = str(prompt)
        
        # Extract output based
        output_data = None
        
        # Handle DataFrame results from ranking mode or filtering mode
        if isinstance(result, pd.DataFrame):
            # Per agent di ranking, vogliamo mostrare le colonne che hanno influenzato il punteggio
            agent_type = agent_name.replace("-agent", "").replace("-extractor", "").replace("-extraction", "").replace("_", "-")
            
            # Mappa prefissi colonne per ogni agente
            prefix_map = {
                "property_technical": "property_technical_",
                "location": "location_",
                "ape": "ape_",
                "normative": "normative_",
                "poi": "poi_"
            }
            prefix = None
            for k, v in prefix_map.items():
                if k in agent_type:
                    prefix = v
                    break
            
            if mode == "ranking" and prefix:
                # Colonne "Involved": score, rank_position, multiplier, weight e colonne sorgente
                involved_cols = ["id"]
                score_col = f"{prefix}score"
                
                # We look for all columns that start with the agent's prefix (transparency cols)
                transparency_cols = [c for c in result.columns if c.startswith(prefix) or c.startswith(f"{prefix}rank_") or c.startswith(f"{prefix}weight_")]
                involved_cols.extend([c for c in transparency_cols if c not in involved_cols])
                
                # Add relevant input columns defined in constants
                from app.core.constants import ENERGY_AGENT_COLUMNS, BUILDING_AGENT_COLUMNS, REGULATORY_AGENT_COLUMNS, PROXIMITY_AGENT_COLUMNS
                source_cols_map = {
                    "building": BUILDING_AGENT_COLUMNS,
                    "location": ["distanza_km", "poi_riferimento"],
                    "energy": ENERGY_AGENT_COLUMNS,
                    "regulatory": REGULATORY_AGENT_COLUMNS,
                    "proximity": PROXIMITY_AGENT_COLUMNS
                }
                
                agent_key = next((k for k in source_cols_map if k in agent_type), None)
                source_cols = []
                if agent_key:
                    source_cols = [c for c in source_cols_map[agent_key] if c in result.columns]
                    involved_cols.extend([c for c in source_cols if c not in involved_cols])
                
                # INPUT: Nomi delle colonne coinvolte (solo sorgenti)
                input_data = ", ".join(source_cols) if source_cols else ", ".join([c for c in involved_cols if c != "id"])
                
                # OUTPUT: Formula per immobile (primi 20)
                ranking_entries = []
                for _, row in result.iterrows():
                    formula_parts = []
                    
                    if agent_name == "ranking-agent":
                        # Final global ranking score
                        scores = []
                        for agent in ["location", "regulatory", "energy", "building", "proximity"]:
                            sc = row.get(f"{agent}_score", 0.0)
                            w = row.get(f"ranking_weight_{agent}", 0.0)
                            scores.append(f"{agent}_score({sc}) * Weight({w})")
                        
                        formula_list = ["RankingSum("] + [f"  {s}," for s in scores[:-1]] + [f"  {scores[-1]}", ")"]
                    elif "building" in agent_type:
                        rank_pos = row.get(f"{prefix}rank_position", "N/A")
                        formula_list = [f"100 / Position({rank_pos})" if rank_pos != "N/A" else "0 (No match)"]
                    elif "location" in agent_type:
                        dist = row.get("distanza_km")
                        poi = row.get("poi_riferimento")
                        score = row.get(score_col, 0)
                        if dist is not None and not pd.isna(dist) and poi and score > 0:
                            # Formula: 100 * exp(-(dist/2.5)^3)
                            formula_list = [f"100 * exp(-({dist:.2f}/2.5)^3)"]
                        else:
                            if score == 0:
                                if not poi or pd.isna(dist):
                                    formula_list = ["Località non identificata -> 0"]
                                else:
                                    formula_list = [f"Distanza eccessiva ({dist:.2f}km) -> 0"]
                            else:
                                formula_list = [f"Score: {score}"]
                    elif "proximity" in agent_type or "energy" in agent_type or "regulatory" in agent_type:
                        # These logics average partial scores (0-100)
                        partial_cols = [c for c in result.columns if f"{prefix}partial_score_" in c]
                        
                        if partial_cols:
                            weighted_parts = []
                            for pc in partial_cols:
                                col_name = pc.replace(f"{prefix}partial_score_", "")
                                if col_name not in result.columns:
                                    continue
                                
                                val_raw = row.get(col_name)
                                score_pt = row.get(pc)
                                weight = row.get(f"{prefix}weight_{col_name}", 1.0 / len(partial_cols))
                                
                                # Special case: Energy Class (Categorical)
                                if col_name == "classe_energetica_ape":
                                    rank_pos = row.get(f"energy_rank_position_{col_name}", "N/A")
                                    if rank_pos != "N/A":
                                        desc = f"{col_name}({val_raw})[Rank {rank_pos}/10]: 100*(1-{int(rank_pos)-1}/9)={score_pt}"
                                    else:
                                        desc = f"{col_name}({val_raw}): {score_pt}"
                                
                                # Special case: Regulatory Typology Rank
                                elif col_name == "tipologia_bene_immobile" and "regulatory" in agent_type:
                                    rank_pos = row.get(f"regulatory_rank_position_{col_name}", "N/A")
                                    if rank_pos != "N/A":
                                        desc = f"{col_name}({val_raw})[Rank {rank_pos}]: {score_pt}"
                                    else:
                                        desc = f"{col_name}({val_raw}): {score_pt}"

                                # Caso Numerico (Min-Max Scaling relativo al dataset via global_stats o locale)
                                else:
                                    try:
                                        if pd.api.types.is_numeric_dtype(result[col_name]):
                                            # Prefer GLOBAL stats if available (deterministic reference)
                                            col_stats = global_stats.get(col_name) if global_stats else None
                                            if col_stats and isinstance(col_stats, dict) and "min" in col_stats and "max" in col_stats:
                                                c_min = float(col_stats["min"])
                                                c_max = float(col_stats["max"])
                                                ref_type = "[Global Ref]"
                                            else:
                                                c_vals = pd.to_numeric(result[col_name], errors='coerce').dropna()
                                                c_min = c_vals.min()
                                                c_max = c_vals.max()
                                                ref_type = "[Local Range]"
                                                
                                            val_num = float(val_raw) if val_raw not in [None, "N/D", "N/A"] else 0
                                            
                                            if c_max == c_min:
                                                desc = f"{col_name}({val_raw}): 100 {ref_type}"
                                            else:
                                                f_up = round(100 * (val_num - c_min) / (c_max - c_min), 1)
                                                f_down = round(100 * (c_max - val_num) / (c_max - c_min), 1)
                                                
                                                if abs(f_up - score_pt) < 1.0:
                                                    desc = f"{col_name}: 100*({val_num}-{c_min})/({c_max}-{c_min})={score_pt} {ref_type}"
                                                elif abs(f_down - score_pt) < 1.0:
                                                    desc = f"{col_name}: 100*({c_max}-{val_num})/({c_max}-{c_min})={score_pt} {ref_type}"
                                                else:
                                                    desc = f"{col_name}({val_num}) [Min {c_min}, Max {c_max}]: {score_pt} {ref_type}"
                                        else:
                                            desc = f"{col_name}({val_raw}): {score_pt} (Match)"
                                    except:
                                        desc = f"{col_name}({val_raw}): {score_pt}"
                                
                                weighted_parts.append(f"{desc} * Weight({weight:.3f})")

                            if len(weighted_parts) > 1:
                                formula_list = ["Sum("] + [f"  {p}," for p in weighted_parts[:-1]] + [f"  {weighted_parts[-1]}", ")"]
                            else:
                                formula_list = [f"{weighted_parts[0]}"]
                        else:
                            # Fallback generico
                            val_main = row.get(source_cols[0], "N/D") if source_cols else "N/D"
                            formula_list = [f"Score({val_main})"]
                    else:
                        formula_list = ["Logic Default"]

                    ranking_entries.append({
                        "id": row["id"],
                        "score": row.get(score_col, 0),
                        "formula": formula_list
                    })
                
                output_data = ranking_entries
            else:
                # Standard behavior for DataFrames (e.g., filtering)
                output_data = json.loads(result.to_json(orient="records"))
                input_data = f"{mode.capitalize()} mode: {len(result)} records"
        elif agent_name == "ranking-agent":
            # For ranking agent, include both ranking and weights for transparency
            output_data = {}
            if hasattr(result, 'ranking') and result.ranking:
                output_data["ranking"] = [r.model_dump() for r in result.ranking.ranking] if hasattr(result.ranking.ranking[0], 'model_dump') else result.ranking.ranking
            if hasattr(result, 'weights'):
                output_data["weights"] = result.weights.model_dump()
            if hasattr(result, 'reasoning'):
                output_data["reasoning"] = result.reasoning
        elif agent_name == "proximity-agent":
            # For proximity agent (filtering), show requirements
            if hasattr(result, 'requirements'):
                output_data = {
                    "requirements": result.requirements
                }
            elif hasattr(result, 'raw_text'):
                output_data = result.raw_text
        elif hasattr(result, 'raw_text'):
            output_data = result.raw_text
        elif isinstance(result, dict) and 'raw_text' in result:
            output_data = result['raw_text']
        elif hasattr(result, 'model_dump'):
            output_data = result.model_dump()
        else:
            output_data = str(result)
            
        entry = {
            "agent_name": agent_name,
            "agent_mode": mode,
            "timestamp": datetime.now().isoformat(),
            "execution_time_ms": duration_ms,
            "input": input_data,
            "output": output_data
        }
        state["agent_trace"].append(entry)

        # SE esiste un logger di valutazione attivo (es. durante i test sintetici), propaga il log
        from app.services.agent_logger import get_active_evaluation_logger
        eval_logger = get_active_evaluation_logger()
        if eval_logger:
            eval_logger.log_agent_execution(
                agent_name=agent_name,
                input_data=input_data,
                output_data=result, # Passiamo il result originale per permettere al logger di estrarre ciò che gli serve
                execution_time_ms=duration_ms,
                agent_mode=mode
            )

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def _update_progress(self, state: GraphState, key: str, message: str, status: str = "current"):
        if state["set_progress"]:
            # Find index by key in step_definitions
            idx = -1
            for i, step in enumerate(state["step_definitions"]):
                if step["key"] == key:
                    idx = i
                    break

            if idx != -1:
                state["steps_state"][idx]["state"] = status
                state["steps_state"][idx]["detail"] = message
                
                # If we are marking something as current/done, ensure previous ones are done
                # (but only if they are not already current - to support parallel)
                if status in ["current", "done"]:
                    for i in range(idx):
                        if state["steps_state"][i]["state"] == "pending":
                            state["steps_state"][i]["state"] = "done"
                            state["steps_state"][i]["detail"] = ""
            
            total_steps = len(state["step_definitions"])
            # Use found index + 1 for percentage calculation
            step_num = idx + 1 if idx != -1 else 1 # Fallback to 1 if not found
            percent = min(100, step_num * 100 / total_steps) if total_steps > 0 else 0

            state["set_progress"]((percent, state["steps_state"]))

    def _get_column_statistics(self, columns: List[str], dataset_path: str = None, dataset_df: pd.DataFrame = None, target_not_na_col: str = None, db_metadata: dict = None) -> dict:
        """Estrae statistiche per un set di colonne per gli agenti LLM."""
        try:
            if dataset_df is not None:
                # Filtraggio colonne presenti
                available_cols = [c for c in columns if c in dataset_df.columns]
                df = dataset_df[available_cols] if available_cols else pd.DataFrame()
            elif dataset_path and os.path.exists(dataset_path):
                available_cols = [c for c in columns] # Initial list
                if dataset_path.endswith('.parquet'):
                    df = pd.read_parquet(dataset_path, columns=available_cols)
                elif dataset_path.endswith('.csv'):
                    df = pd.read_csv(dataset_path, usecols=lambda col: col in available_cols)
                else:
                    try:
                        df = pd.read_parquet(dataset_path, columns=available_cols)
                    except:
                        df = pd.read_csv(dataset_path, usecols=lambda col: col in available_cols)
            else:
                return {}

            stats = {
                "total_records": len(df),
            }
            
            if target_not_na_col and target_not_na_col in df.columns:
                stats[f"with_{target_not_na_col}_data"] = int(df[target_not_na_col].notna().sum())

            for col in columns:
                if col not in df.columns:
                    # Se la colonna non è nel DF, mostriamo 0 per ogni valore possibile dai metadati (se presenti)
                    if db_metadata and col in db_metadata and "values" in db_metadata[col]:
                        stats[col] = {str(val): 0 for val in db_metadata[col]["values"]}
                    continue

                if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
                    valid_data = df[col].dropna()
                    if not valid_data.empty:
                        stats[col] = {
                            "min": round(float(valid_data.min()), 2),
                            "max": round(float(valid_data.max()), 2),
                            "mean": round(float(valid_data.mean()), 2),
                            "median": round(float(valid_data.median()), 2),
                            "percentiles": {
                                "25%": round(float(valid_data.quantile(0.25)), 2),
                                "50%": round(float(valid_data.quantile(0.50)), 2),
                                "75%": round(float(valid_data.quantile(0.75)), 2),
                            },
                        }
                else:
                    # Categorie - Gestione smart per evitare prompt troppo grandi
                    unique_count = df[col].nunique()
                    
                    if unique_count > 100:
                        # Troppi valori unici (es. IDs, particelle), mostriamo solo statistiche aggregate
                        stats[col] = {
                            "unique_values_count": int(unique_count),
                            "top_10_values": df[col].value_counts().head(10).to_dict(),
                            "note": f"Colonna con alta cardinalità ({unique_count} valori). Mostrati solo i primi 10 per brevità."
                        }
                        continue

                    counts = df[col].value_counts().to_dict()
                    
                    real_values = []
                    if db_metadata and col in db_metadata and "values" in db_metadata[col]:
                        real_values = db_metadata[col]["values"]
                    
                    if real_values:
                        # Mostra 0 per i valori reali non presenti nel dataset
                        stats[col] = {str(val): int(counts.get(val, 0)) for val in real_values}
                        # Aggiungiamo eventuali valori nel dataset non presenti nei metadati (safety)
                        for val, count in counts.items():
                            if str(val) not in stats[col]:
                                stats[col][str(val)] = int(count)
                    else:
                        # Limita a primi 50 valori se non ci sono metadati
                        top_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:50]
                        stats[col] = {str(k): int(v) for k, v in top_items}
                        if len(counts) > 50:
                            stats[col]["_other_count"] = int(sum(v for k, v in list(counts.items())[50:]))

            return stats
        except Exception as e:
            logger.error(f"Error calculating column statistics: {e}")
            return {"error": str(e)}

    def _get_energy_statistics(self, dataset_path: str = None, dataset_df: pd.DataFrame = None, db_metadata: dict = None) -> dict:
        """Wrapper for retrocompatibility or specific Energy (EPC) logic."""
        return self._get_column_statistics(
            columns=ENERGY_AGENT_COLUMNS, 
            dataset_path=dataset_path, 
            dataset_df=dataset_df,
            target_not_na_col="classe_energetica_ape",
            db_metadata=db_metadata
        )

    def _extract_categorical_values(self, db_metadata: dict) -> dict:
        """
        Extract categorical value lists for columns with discrete values.

        Returns:
            Dict mapping column names to list of possible values
        """
        categorical_columns = [
            "classe_energetica_ape",
            "tipologia_bene_immobile",
            "epoca_costruzione",
            "finalita",
        ]

        categorical_values = {}

        for col in categorical_columns:
            if col in db_metadata and "values" in db_metadata[col]:
                categorical_values[col] = db_metadata[col]["values"]
            elif col == "classe_energetica_ape":
                # Hardcode energy classes if not in metadata
                categorical_values[col] = [
                    "A1",
                    "A2",
                    "A3",
                    "A4",
                    "B",
                    "C",
                    "D",
                    "E",
                    "F",
                    "G",
                ]

        return categorical_values

    def _analyze_request(self, state: GraphState) -> GraphState:
        self._update_progress(state, "ranking_init", "Analysis & priority agent listening...")
        query = state["query"]
        logger.info(f"Starting analysis for query: {query} (Architecture: {self.architecture})")

        # --- BASELINE PATH: Unified Planner ---
        if state["architecture"] == "baseline":
            return self._unified_analysis(state)

        # --- MULTIAGENT PATH: Parallel Technical Agents ---
        # 1. Define ALL tasks (Ranking + 5 Technicians) in Parallel
        # We run Ranking simultaneously with the others to save the sequential delay,
        # as all agents are usually active per system instructions.
        
        base_dataset = state.get("base_dataset")
        dataset_path = state.get("dataset_path")

        def run_ranking():
            start_t = time.time()
            self._update_progress(state, "ranking_init", "Analyzing requirement priority...")
            logger.info("Executing Ranking agent (parallel)")
            result = self.ranking_agent.run(query=query, mode="filtering")
            logger.info("Ranking agent completed")
            return result, (time.time() - start_t) * 1000

        def run_building():
            start_t = time.time()
            self._update_progress(state, "building", "Technical analysis...")
            prop_stats = {}
            if state.get("use_data_knowledge", True):
                prop_stats = self._get_column_statistics(
                    columns=BUILDING_AGENT_COLUMNS,
                    dataset_path=dataset_path, dataset_df=base_dataset,
                    db_metadata=state.get("db_metadata")
                )
            result = self.building_agent.run(
                query=query, mode="filtering",
                available_typologies=str(state["db_metadata"].get("tipologia_bene_immobile", {}).get("values", [])),
                statistics=prop_stats
            )
            logger.info("Building agent completed")
            return result, (time.time() - start_t) * 1000

        def run_location():
            start_t = time.time()
            self._update_progress(state, "location", "Geographic search...")
            result = self.location_agent.run(query=query)
            logger.info("Location agent completed")
            return result, (time.time() - start_t) * 1000

        def run_energy():
            start_t = time.time()
            if base_dataset is not None or dataset_path is not None:
                self._update_progress(state, "energy", "Energy assessment...")
                energy_stats = {}
                if state.get("use_data_knowledge", True):
                    energy_stats = self._get_column_statistics(
                        columns=ENERGY_AGENT_COLUMNS, 
                        dataset_path=dataset_path, 
                        dataset_df=base_dataset,
                        target_not_na_col="classe_energetica_ape",
                        db_metadata=state.get("db_metadata")
                    )
                result = self.energy_agent.run(
                    query=query, mode="filtering",
                    statistics=energy_stats, score_legend=ENERGY_SCORE_LEGEND,
                )
                logger.info("Energy agent completed")
                return result, (time.time() - start_t) * 1000
            return None, 0

        def run_proximity():
            start_t = time.time()
            self._update_progress(state, "proximity", "Proximity analysis...")
            proximity_stats = {}
            if state.get("use_data_knowledge", True):
                proximity_stats = self._get_column_statistics(
                    columns=PROXIMITY_AGENT_COLUMNS,
                    dataset_path=dataset_path, dataset_df=base_dataset,
                    db_metadata=state.get("db_metadata")
                )
            result = self.proximity_agent.run(query=query, mode="filtering", statistics=proximity_stats)
            logger.info("Proximity agent completed")
            return result, (time.time() - start_t) * 1000
        
        def run_regulatory():
            start_t = time.time()
            self._update_progress(state, "regulatory", "Regulatory check...")
            regulatory_stats = {}
            if state.get("use_data_knowledge", True) and (base_dataset is not None or dataset_path is not None):
                regulatory_stats = self._get_column_statistics(
                    columns=REGULATORY_AGENT_COLUMNS,
                    dataset_path=dataset_path, dataset_df=base_dataset,
                    db_metadata=state.get("db_metadata")
                )
            result = self.regulatory_agent.run(query=query, available_columns=REGULATORY_AGENT_COLUMNS, statistics=regulatory_stats)
            logger.info("Regulatory agent completed")
            return result, (time.time() - start_t) * 1000

        # 2. Execute all in parallel
        # We start EVERYTHING since prompt_config enforces all agents in ranking anyway.
        active_tasks = {
            "ranking": run_ranking,
            "building": run_building,
            "location": run_location,
            "energy": run_energy,
            "proximity": run_proximity,
            "regulatory": run_regulatory
        }
        
        # Filter out disabled agents for ablation study
        disabled = state.get("disabled_agents", [])
        if disabled:
            logger.info(f"Ablation mode: Disabling agents: {disabled}")
            active_tasks = {k: v for k, v in active_tasks.items() if k not in disabled}

        results = {k: None for k in active_tasks.keys()}
        logger.info(f"Executing active agents in parallel: {', '.join(active_tasks.keys())}")
        
        with ThreadPoolExecutor(max_workers=len(active_tasks)) as executor:
            future_to_name = {executor.submit(task): name for name, task in active_tasks.items()}
            for future in as_completed(future_to_name):
                agent_name = future_to_name[future]
                try:
                    res, duration = future.result()
                    results[agent_name] = res
                    if res:
                        # Map to correct trace name
                        trace_name = "ranking-agent" if agent_name == "ranking" else f"{agent_name}-agent"
                        self._log_execution(state, trace_name, res, duration)
                        
                    # Mark step as done
                    ui_key = "ranking_init" if agent_name == "ranking" else agent_name
                    self._update_progress(state, ui_key, "", status="done")
                except Exception as e:
                    import traceback
                    logger.error(f"Error executing {agent_name}: {e}\n{traceback.format_exc()}")

        # 3. Collect results
        ranking_result = results.get("ranking")
        state["ranking_result"] = ranking_result
        
        building_result = results.get("building")
        loc_result = results.get("location")
        energy_result = results.get("energy")
        proximity_result = results.get("proximity")
        regulatory_result = results.get("regulatory")

        # Process building result
        typologies = []
        if building_result:
            prop_data = safe_extract_json(building_result.raw_text, schema=BuildingResponse)
            typologies = prop_data.typologies if prop_data else []
            state["gemini_responses"]["building_extraction"] = {
                "prompt": (
                    building_result.prompt.model_dump() if building_result.prompt else None
                ),
                "response": building_result.raw_text,
                "typologies": typologies,
            }
        else:
            state["gemini_responses"]["building_extraction"] = {
                "prompt": None,
                "response": "Agent deactivated due to irrelevance",
                "typologies": [],
            }
        state["building_result"] = building_result
        state["context"].building_result = building_result

        # Process Location
        places = []
        if loc_result:
            loc_data = safe_extract_json(loc_result.raw_text, schema=LocationResponse)
            places = loc_data.places if loc_data else []
            state["gemini_responses"]["location_extraction"] = {
                "prompt": loc_result.prompt.model_dump() if loc_result.prompt else None,
                "response": loc_result.raw_text,
                "places": [p.model_dump() for p in places],
            }
        else:
            state["gemini_responses"]["location_extraction"] = {
                "prompt": None,
                "response": "Agent deactivated due to irrelevance",
                "places": [],
            }
        state["context"].locations = places
        
        # If no places found after execution, remove step from UI
        if loc_result and not places:
             logger.info("Location Agent found no places: removing step from UI.")
             state["step_definitions"] = [s for s in state["step_definitions"] if s["key"] != "location"]
             state["steps_state"] = [s for s in state["steps_state"] if s["label"] != "Identifying search geographic area..."]
             # Force update to refresh UI
             self._update_progress(state, "ranking_init", "", status="done")

        location_payload = []
        if places:
            for p in places:
                if p.lat is not None and p.lon is not None:
                    # Construct search query for payload format consistency
                    search_query = f"{p.name}, {p.city}" if p.city else p.name
                    location_payload.append([search_query, p.lat, p.lon])

        state["location_payload"] = location_payload

        # Process proximity
        state["proximity_result"] = proximity_result
        state["context"].proximity_result = proximity_result
        proximity_data = safe_extract_json(proximity_result.raw_text) if proximity_result else {}
        if proximity_result is not None:
            state["gemini_responses"]["proximity_analysis"] = {
                "prompt": proximity_result.prompt.model_dump() if proximity_result.prompt else None,
                "response": proximity_result.raw_text,
                "requirements": proximity_data.get('requirements', []),
                "found": proximity_data.get('found', False)
            }
        else:
            state["gemini_responses"]["proximity_analysis"] = {
                "prompt": None,
                "response": "Agent deactivated due to irrelevance",
                "requirements": [],
                "found": False
            }

        # Process energy
        state["energy_result"] = energy_result
        state["context"].energy_result = energy_result
        energy_data = safe_extract_json(energy_result.raw_text) if energy_result else {}
        if energy_result:
            state["gemini_responses"]["energy_analysis"] = {
                "prompt": energy_result.prompt.model_dump() if energy_result.prompt else None,
                "response": energy_result.raw_text,
                "requirements": energy_data.get('requirements', []),
                "found": energy_data.get("found", False)
            }
        else:
            state["gemini_responses"]["energy_analysis"] = {
                "prompt": None,
                "response": "Agent deactivated due to irrelevance",
                "requirements": [],
                "found": False
            }

        # Process energy text for context
        energy_text = ""
        if energy_data and energy_data.get("found"):
            energy_reqs = energy_data.get("requirements", [])
            if energy_reqs:
                target_cols = [r.get('target_column', r.get('colonna_target')) for r in energy_reqs if isinstance(r, dict)]
                energy_text = f"\n\nEnergy analysis: The user expressed needs regarding efficiency (EPC). Requirements on: {', '.join(target_cols)}."

        # Process proximity text for context
        proximity_text = ""
        if proximity_data:
            proximity_requirements = proximity_data.get('requirements', [])
            # Consider high priority if value is relatively high (e.g. >= 3.0)
            def safe_float_compare(v, threshold):
                if isinstance(v, list):
                    v = v[0] if v else 0
                try:
                    return float(v) >= threshold
                except (ValueError, TypeError):
                    return False

            high_priority = [r.get('target_column', r.get('colonna_target')) for r in proximity_requirements if isinstance(r, dict) and safe_float_compare(r.get('value', r.get('valore', 0)), 3.0)]
            if high_priority:
                proximity_text = f"\n\nProximity analysis: The user expressed preference for: {', '.join(high_priority)} with high quality thresholds."
            elif proximity_requirements:
                all_targets = [r.get('target_column', r.get('colonna_target')) for r in proximity_requirements if isinstance(r, dict)]
                proximity_text = f"\n\nProximity analysis: Relevant categories: {', '.join(all_targets)}."

        # Save regulatory result in state and context
        state["regulatory_result"] = regulatory_result
        state["context"].regulatory_result = regulatory_result
        reg_data = safe_extract_json(regulatory_result.raw_text, schema=RegulatoryResponse) if regulatory_result else None
        if regulatory_result:
            state["gemini_responses"]["regulatory_analysis"] = {
                "prompt": regulatory_result.prompt.model_dump() if regulatory_result.prompt else None,
                "response": regulatory_result.raw_text,
                "regulatory_info": regulatory_result.raw_text,
                "sources": regulatory_result.sources,
                "found": reg_data.found if reg_data else False,
                "retrieval": regulatory_result.retrieval,
            }
        else:
            state["gemini_responses"]["regulatory_analysis"] = {
                "prompt": None,
                "response": "Agent deactivated due to irrelevance",
                "regulatory_info": "",
                "sources": [],
            }

        # Build use_case_str from agent results (no needs_metric)
        use_case_parts = []
        if energy_text:
            use_case_parts.append(energy_text.strip())
        if proximity_text:
            use_case_parts.append(proximity_text.strip())
        if regulatory_result:
            use_case_parts.append(f"Regulatory: {regulatory_result.raw_text[:200]}")
        state["use_case_str"] = "\n".join(use_case_parts)

        return state

    def _fix_sql_quotes(self, sql: str) -> str:
        """
        Heuristic repair of common LLM quoting errors.
        Handles cases like 'value'' (incorrect final double single quote) or missing quotes.
        """
        if not sql:
            return sql
        
        sql = sql.strip()
        
        # Caso 1: Doppio apice finale con conteggio totale dispari (hallucination di escaping)
        if sql.endswith("''") and sql.count("'") % 2 != 0:
             sql = sql[:-1]
             
        # Caso 2: Conteggio dispari di apici (apice mancante alla fine)
        # Se l'ultimo apice è seguito da testo o chiusura parentesi senza un apice di chiusura
        if sql.count("'") % 2 != 0:
            # Proviamo a chiudere la stringa se sembra sensato
            if sql[-1] not in ["'", ";", " "]:
                sql += "'"
                
        return sql

    def _format_agent_requirements(self, agent_result: Any) -> str:
        """Formats an agent's requirements (Energy or Regulatory) in compact format [col] [op] [val]."""
        if not agent_result or not agent_result.raw_text or agent_result.raw_text == "N/A":
            return "N/A"
        
        try:
            # Safely extract JSON (handles markdown blocks and extra text)
            data = safe_extract_json(agent_result.raw_text)
            
            if not data or not isinstance(data, dict):
                # If not a valid dict, return the original text but limited
                return str(agent_result.raw_text)[:500]
                
            requirements = data.get("requirements", data.get("requisiti", []))
            if not requirements:
                return "No specific requirements identified."
            
            formatted = []
            for req in requirements:
                col = req.get("target_column", req.get("colonna_target"))
                op = req.get("operator", req.get("operatore"))
                val = req.get("value", req.get("valore"))
                if col and op and val is not None:
                    # If value is a list, format it as (val1, val2)
                    if isinstance(val, list):
                        if len(val) == 1:
                            val_str = f"'{val[0]}'" if isinstance(val[0], str) else str(val[0])
                            formatted.append(f"{col} {op} {val_str}")
                        else:
                            val_str = "(" + ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in val) + ")"
                            # If the operator is not IN/NOT IN, using a list might be technically incorrect for the SQL agent
                            # but we pass it anyway trusting its correction capability.
                            formatted.append(f"{col} {op} {val_str}")
                    else:
                        val_str = f"'{val}'" if isinstance(val, str) else str(val)
                        formatted.append(f"{col} {op} {val_str}")
            
            return "; ".join(formatted) if formatted else "Nessun requisito specifico identificato."
        except Exception as e:
            logger.error(f"Error formatting agent requirements: {e}")
            # Fallback in caso di errore
            return str(agent_result.raw_text)[:500]


    def _generate_sql(self, state: GraphState) -> GraphState:
        retry_count = state["retry_count"]
        if "sql_history" not in state or retry_count == 0:
            state["sql_history"] = []
        self._update_progress(
            state, "sql", ""
        )

        is_relaxing = state.get("relax_constraints", False)
        
        if state.get("sql_query") and retry_count == 0 and not is_relaxing:
            logger.info("SQL already generated by Baseline Planner, skipping SQLAgent.")
            return state

        # DETERMINISTIC RELAXATION: Se abbiamo già una query rilassata pronta prodotta dal workflow AST
        # saltiamo l'invocazione dell'LLM SQL Agent
        prepared_relaxed_sql = state.get("prepared_relaxed_sql")
        if is_relaxing and prepared_relaxed_sql:
            logger.info("Using prepared relaxed SQL from deterministic workflow.")
            state["sql_query"] = prepared_relaxed_sql
            state.pop("prepared_relaxed_sql", None)
            return state

        query = state["query"]
        db_schema = state["db_schema"]
        location_payload = state["location_payload"]
        failed_query = state["sql_query"] if (retry_count > 0 or is_relaxing) else ""
        error_msg = state.get("execution_error")

        loc_obj = None
        if isinstance(location_payload, list) and location_payload:
            _, lat, lon = location_payload[0]
            loc_obj = {"lat": lat, "lon": lon}

        # Raw agent outputs
        building_result = state.get("building_result")
        proximity_result = state.get("proximity_result")
        energy_result = state.get("energy_result")
        regulatory_result = state.get("regulatory_result")
        
        # Format locations as JSON string for clarity: only lat, lon, radius_km
        locations_list = state["gemini_responses"].get("location_extraction", {}).get("places", [])
        filtered_locations = []
        for loc in locations_list:
            filtered_locations.append({
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "radius_km": loc.get("radius_km"),
                "threshold": loc.get("threshold")
            })

        # Aggregate all requirements into a single list
        all_reqs = []
        # 1. Building / Property Technical
        if building_result and building_result.raw_text != "N/D":
            try:
                t_data = safe_extract_json(building_result.raw_text)
                if t_data and isinstance(t_data, dict) and t_data.get("typologies"):
                    typs = t_data['typologies']
                    all_reqs.append(f"tipologia_bene_immobile: {', '.join(typs)}")
            except: pass
            
            # BuildingAgent now also extracts structured requirements (ID, contracts, surfaces)
            building_fmt = self._format_agent_requirements(building_result)
            if building_fmt != "N/D" and "Nessun requisito" not in building_fmt:
                all_reqs.append(building_fmt)

        # 2. Locations
        if filtered_locations:
            for loc in filtered_locations:
                radius_str = f"raggio {loc['radius_km']}km"
                if loc.get("threshold"):
                    radius_str += f", threshold {loc['threshold']}km"
                all_reqs.append(f"Coordinate: {loc['lat']}, {loc['lon']} ({radius_str})")

        # 3. Structured requirements (Energy, Proximity, Regulatory)
        # Use helper for Energy, Regulatory, and Proximity
        energy_fmt = self._format_agent_requirements(energy_result)
        if energy_fmt != "N/D" and "Nessun requisito" not in energy_fmt:
            all_reqs.append(energy_fmt)
            
        regulatory_fmt = self._format_agent_requirements(regulatory_result)
        if regulatory_fmt != "N/D" and "Nessun requisito" not in regulatory_fmt:
             all_reqs.append(regulatory_fmt)
 
        proximity_fmt = self._format_agent_requirements(proximity_result)
        if proximity_fmt != "N/D" and "Nessun requisito" not in proximity_fmt:
            all_reqs.append(proximity_fmt)

        # 4. Relaxation Proposals (if active)
        if state.get("relax_constraints") and state.get("relaxation_proposals"):
            for p in state["relaxation_proposals"]:
                all_reqs.append(f"RELAXATION SUGGESTION for field '{p['field']}': expand from '{p['condizione_iniziale']}' to '{p['condizione_relaxed']}' because: {p['reason']}")

        all_requirements_str = "\n".join([f"- {r}" for r in all_reqs]) if all_reqs else "N/D"

        # Determination of whether to use Retry Prompt
        # Prepare failed query and error message for the SQL Agent.
        is_sql_error = bool(state.get("execution_error"))
        effective_failed_query = failed_query
        effective_error_msg = error_msg

        # Prepare technical scheme (all columns and their types)
        db_schema_obj = state.get("db_schema", {})
        col_types = db_schema_obj.get("types", {})
        scheme_str = "\n".join([f"{col}: {dtype}" for col, dtype in col_types.items()])

        start_t = time.time()
        sql_result = self.sql_agent.run(
            query=query, # use original query
            scheme=scheme_str,
            all_requirements=all_requirements_str,
            location=loc_obj,
            failed_query=effective_failed_query,
            error_msg=effective_error_msg,
            db_metadata=json.dumps(state.get("db_metadata", {}), ensure_ascii=False),
            raw_response=None
        )
        duration_ms = (time.time() - start_t) * 1000

        # Append to history
        state["sql_history"].append(sql_result.raw_text)

        # sql_result.sql now contains the cleaned SQL
        state["sql_query"] = sql_result.sql
        key = (
            "sql_generation"
            if retry_count == 0
            else f"sql_generation_retry_{retry_count}"
        )
        state["gemini_responses"][key] = {
            "prompt": sql_result.prompt.model_dump() if sql_result.prompt else None,
            "response": sql_result.raw_text,
            "sql_query": sql_result.sql,
            "sql_history": state["sql_history"]
        }
        
        self._log_execution(state, "sql-agent", sql_result, duration_ms)
        return state

    def _execute_sql(self, state: GraphState) -> GraphState:
        self._update_progress(state, "sql", "")
        sql_query = state["sql_query"]
        dataset_path = state.get("dataset_path")
        base_dataset = state.get("base_dataset")

        logger.info(f"Executing SQL: {sql_query}")
        # Use base_dataset if available, otherwise rely on dataset_path
        pd_data = base_dataset if base_dataset is not None else None
        selected_data, error = self.execute_sql_fn(
            sql_query, pd_data, dataset_path=dataset_path
        )

        if error:
            logger.error(f"SQL Execution Error: {error}")
        else:
            logger.info(f"SQL returned {len(selected_data)} rows")

        state["selected_data"] = selected_data
        state["execution_error"] = error
        return state

    def _check_sql_execution(self, state: GraphState) -> str:
        if state.get("execution_error"):
            if state["retry_count"] < 3:
                return "retry"
            return "fallback"

        num_results = 0 if state.get("selected_data") is None or state.get("selected_data").empty else len(state["selected_data"])

        # Se ci sono meno di 10 risultati e il rilassamento è abilitato e non abbiamo superato i tentativi
        if num_results < 10 and state.get("use_relaxation", True) and state.get("relaxation_count", 0) < 3:
            return "relax"

        if num_results > 0:
            return "continue"

        return "fallback"

    def _should_broker_review(self, state: GraphState) -> str:
        """Route to broker only if there are evaluation results."""
        if state["context"].evaluation_results:
            return "continue"
        return "skip"

    def _handle_retry(self, state: GraphState) -> GraphState:
        return {
            "retry_count": state["retry_count"] + 1, 
            "last_retry_reason": "error"
        }

    def _fix_sql_quotes(self, sql: str) -> str:
        """Riparazione euristica di errori comuni di quotatura degli LLM."""
        if not sql: return sql
        sql = sql.strip()
        if sql.endswith("''") and sql.count("'") % 2 != 0:
             sql = sql[:-1]
        if sql.count("'") % 2 != 0:
            if sql[-1] not in ["'", ";", " "]:
                sql += "'"
        return sql

    def _prepare_relaxation_data(self, state: GraphState, sql_query: str):
        """Estrae in modo strutturato le condizioni WHERE usando sqlglot."""
        try:
            sql_query = self._fix_sql_quotes(sql_query)
            expression = parse_one(sql_query, read="duckdb")
            where = expression.find(exp.Where)
            if not where:
                return [], [], expression

            def get_conditions(node):
                if isinstance(node, exp.And):
                    return get_conditions(node.left) + get_conditions(node.right)
                return [node]

            conditions = get_conditions(where.this)
            
            where_details = []
            for cond in conditions:
                cols = [col.name for col in cond.find_all(exp.Column)]
                col_type = "categorica"
                main_col = cols[0] if cols else "N/D"
                
                if main_col != "N/D":
                    db_meta = state.get("db_metadata", {})
                    if main_col in db_meta and db_meta[main_col].get("type") in ["integer", "float", "double"]:
                        col_type = "continua"
                
                # Identifier operator key
                op = cond.key
                if isinstance(cond, exp.EQ): op = "="
                elif isinstance(cond, exp.GT): op = ">"
                elif isinstance(cond, exp.LT): op = "<"
                elif isinstance(cond, exp.GTE): op = ">="
                elif isinstance(cond, exp.LTE): op = "<="
                elif isinstance(cond, exp.In): op = "IN"
                elif isinstance(cond, exp.Between): op = "BETWEEN"
                
                where_details.append({
                    "colonna": main_col,
                    "operatore": op,
                    "valore": cond.sql(dialect="duckdb").split(op)[-1].strip() if op in cond.sql(dialect="duckdb") else "N/D",
                    "tipo": col_type,
                    "condizione_full": cond.sql(dialect="duckdb")
                })
            return where_details, conditions, expression
        except Exception as e:
            logger.error(f"Error preparing relaxation data: {e}")
            return [], [], None

    def _relax_query(self, state: GraphState) -> GraphState:
        """Propone rilassamenti ai criteri di ricerca se i risultati sono vuoti."""
        self._update_progress(state, "sql", "Analizzo possibili rilassamenti dei criteri...")
        
        sql_query = state.get("sql_query", "")
        where_details, _, _ = self._prepare_relaxation_data(state, sql_query)
        
        # Estrazione colonne per recuperare statistiche
        unique_cols = list(set([d["colonna"] for d in where_details if d["colonna"] != "N/D"]))
        
        stats = self._get_column_statistics(
            columns=unique_cols,
            dataset_path=state.get("dataset_path"),
            dataset_df=state.get("base_dataset"),
            db_metadata=state.get("db_metadata")
        )
        
        start_t = time.time()
        # 1. Carichiamo tutte le proposte dal RelaxationAgent in un solo colpo
        result = self.relaxation_agent.run(
            where_conditions=json.dumps(where_details, indent=2, ensure_ascii=False),
            statistics=json.dumps(stats, indent=2, ensure_ascii=False),
            current_results_count=len(state.get("selected_data", []))
        )
        duration_ms = (time.time() - start_t) * 1000
        
        # 2. Applichiamo il workflow deterministico: una condizione alla volta, dall'ultima alla prima
        final_relaxed_sql = self._apply_ast_relaxation_workflow(state, sql_query, result.proposals)
        
        # Salviamo le proposte nello stato per la UI
        proposals = [p.model_dump() if hasattr(p, "model_dump") else p for p in result.proposals]
        self._log_execution(state, "relaxation-agent", result, duration_ms)
        
        applied = final_relaxed_sql != sql_query
        if applied:
            state["prepared_relaxed_sql"] = final_relaxed_sql
            self._update_progress(state, "sql", "Rilassamento deterministico applicato con successo.")
        else:
            self._update_progress(state, "sql", "Nessun rilassamento ha prodotto risultati sufficienti.")

        current_count = state.get("relaxation_count", 0) + 1
        all_proposals = state.get("relaxation_proposals", []) + proposals

        return {
            "sql_query": final_relaxed_sql if applied else sql_query,
            "relaxation_proposals": all_proposals,
            "relax_constraints": True,
            "relaxation_applied": applied,
            "relaxation_count": current_count,
            "execution_error": f"[Rilassamento #{current_count}] Zero o insufficenti risultati trovati (< 10 righe). Applica i rilassamenti suggeriti per ampliare la ricerca.",
            "status_msg": "Rilassamento criteri attivato per mancanza di risultati."
        }

    def _apply_ast_relaxation_workflow(self, state, initial_sql, proposals):
        """Applica rilassamenti uno alla volta, dall'ultimo al primo, testando i risultati."""
        threshold = 10
        where_details, base_conditions, expression = self._prepare_relaxation_data(state, initial_sql)
        if not expression or not base_conditions: return initial_sql

        current_active_conditions = [c.copy() for c in base_conditions]
        
        # STEP 1: Prova i rilassamenti suggeriti dall'LLM (Progressione: Low -> Medium -> High)
        for level in ["low", "medium", "high"]:
            # Iterate from last to first
            for i in range(len(current_active_conditions) - 1, -1, -1):
                orig_cond_sql = base_conditions[i].sql(dialect="duckdb")
                # Trova la proposta per questa specifica stringa SQL e livello
                match = next((p for p in proposals if p.livello_rilassamento.lower() == level and p.condizione_iniziale.strip() == orig_cond_sql.strip()), None)
                
                if match:
                    temp_conditions = [c.copy() for c in current_active_conditions]
                    try:
                        temp_conditions[i] = parse_one(self._fix_sql_quotes(match.condizione_relaxed), read="duckdb")
                    except: continue
                    
                    trial_sql = self._rebuild_sql(expression, temp_conditions)
                    trial_df, error = self.execute_sql_fn(trial_sql, state.get("base_dataset"), dataset_path=state.get("dataset_path"))
                    
                    if not error and len(trial_df) >= threshold:
                        logger.info(f"Successo relaxation '{level}' su cond {i}: trovato {len(trial_df)} immobili.")
                        return trial_sql
                    
                    # Se migliora ma non raggiunge la soglia, manteniamo il rilassamento e proseguiamo (Greedy)
                    if not error and len(trial_df) > len(state.get("selected_data", [])):
                        current_active_conditions[i] = temp_conditions[i]

        # STEP 2: Fallback Estremo - Rimozione progressiva dall'ultima alla prima
        for i in range(len(current_active_conditions) - 1, -1, -1):
            temp_conditions = [c.copy() for j, c in enumerate(current_active_conditions) if i != j]
            trial_sql = self._rebuild_sql(expression, temp_conditions, remove_where=not temp_conditions)
            trial_df, error = self.execute_sql_fn(trial_sql, state.get("base_dataset"), dataset_path=state.get("dataset_path"))
            
            if not error and len(trial_df) >= threshold:
                 logger.info(f"Successo via rimozione cond {i}: trovato {len(trial_df)} immobili.")
                 return trial_sql
            
            if not error and len(trial_df) > len(state.get("selected_data", [])):
                current_active_conditions = temp_conditions # Rimaniamo con la condizione in meno

        return self._rebuild_sql(expression, current_active_conditions)

    def _rebuild_sql(self, expression, conditions, remove_where=False):
        """Ricostruisce la query SQL a partire dall'espressione e dai nuovi nodi WHERE."""
        new_expression = expression.copy()
        where = new_expression.find(exp.Where)
        if not conditions or remove_where:
            if where: where.pop()
        else:
            new_predicate = conditions[0]
            for next_cond in conditions[1:]:
                new_predicate = exp.And(this=new_predicate, expression=next_cond)
            where.set("this", new_predicate)
        return new_expression.sql(dialect="duckdb", pretty=True)


    def _fallback_results(self, state: GraphState) -> GraphState:
        """
        Fallback when SQL queries fail after max retries.
        Returns top results from full dataset so user always gets something.
        """
        self._update_progress(
            state, "sql", ""
        )
        logger.warning("Fallback activated: loading top results from full dataset")

        dataset_path = state.get("dataset_path")
        base_dataset = state.get("base_dataset")
        
        if not dataset_path and base_dataset is None:
            logger.error("No dataset path or base_dataset for fallback")
            state["status_msg"] = "Errore: impossibile generare alternative."
            return state

        try:
            if dataset_path:
                full_df = pd.read_parquet(dataset_path)
                logger.error(f"DEBUG FALLBACK FILE: columns: {full_df.columns.tolist()}")
                logger.info(f"Fallback loaded {len(full_df)} rows from dataset file")
            else:
                full_df = base_dataset.copy()
                logger.error(f"DEBUG FALLBACK BASE: columns: {full_df.columns.tolist()}")
                logger.info(f"Fallback using base_dataset ({len(full_df)} rows)")

            # Get user location if available for sorting
            user_location = None
            loc_payload = state.get("location_payload")
            if isinstance(loc_payload, list) and len(loc_payload) > 0:
                try:
                    user_location = (float(loc_payload[0][1]), float(loc_payload[0][2]))
                except Exception:
                    pass

            # Sort by best energy (EPC) score, or by distance if location available
            if not self.location_agent.is_found(state["location_payload"]):
                # Sort by energy score (higher is better)
                if "ape_score_total" in full_df.columns:
                    fallback_df = full_df.nlargest(500, "ape_score_total")
                else:
                    fallback_df = full_df.sample(min(500, len(full_df)))
            elif (
                user_location
                and "latitudine" in full_df.columns
                and "longitudine" in full_df.columns
            ):
                # Calculate distance for sorting
                lat, lon = user_location
                full_df["_fallback_dist"] = np.sqrt(
                    (full_df["latitudine"] - lat) ** 2
                    + (full_df["longitudine"] - lon) ** 2
                )
                fallback_df = full_df.nsmallest(500, "_fallback_dist")
                fallback_df = fallback_df.drop(columns=["_fallback_dist"])
            elif "ape_score_total" in full_df.columns:
                # Sort by energy score (higher is better)
                fallback_df = full_df.nlargest(500, "ape_score_total")
            else:
                # Random sample as last resort
                fallback_df = full_df.sample(min(500, len(full_df)))

            state["selected_data"] = fallback_df
            state["gemini_responses"]["fallback_activated"] = True
            state["status_msg"] = (
                "Nessun risultato trovato con i criteri specificati. "
                "Mostro alternative suggerite."
            )
            logger.info(f"Fallback returning {len(fallback_df)} results")

        except Exception as e:
            logger.error(f"Fallback failed: {e}")
            state["status_msg"] = f"Errore durante la generazione di alternative: {e}"

        return state

    def _enrich_results(self, state: GraphState) -> GraphState:
        self._update_progress(state, "sql", "")
        selected_data = state["selected_data"]
        sql_query = state["sql_query"]
        location_payload = state["location_payload"]
        dataset_path = state.get("dataset_path")

        # Post-processing similar to OrchestratorAgent
        try:
            parsed = sqlparse.parse(sql_query)[0]
            state["where_clause"] = next(
                (
                    str(token)
                    for token in parsed.tokens
                    if isinstance(token, sqlparse.sql.Where)
                ),
                "Nessuna clausola WHERE trovata.",
            )
        except Exception:
            state["where_clause"] = "Nessuna clausola WHERE trovata."

        # ============================================================
        # CRITICAL: LEFT JOIN with full dataset to recover ALL columns
        # SQL might only select specific columns, but we need everything
        # for the UI (surface_area, meta_immobile, ape_scores, etc.)
        # ============================================================
        if dataset_path and not selected_data.empty and "id" in selected_data.columns:
            try:
                full_df = pd.read_parquet(dataset_path)
                logger.info(
                    f"Enrichment: merging {len(selected_data)} SQL results with "
                    f"{len(full_df)} full dataset rows"
                )

                # Ensure ID is string for matching
                selected_data["id"] = selected_data["id"].astype(str)
                full_df["id"] = full_df["id"].astype(str)

                # Get columns only in full_df (not in selected_data)
                sql_columns = set(selected_data.columns)
                full_columns = set(full_df.columns)
                missing_columns = full_columns - sql_columns

                if missing_columns:
                    # Only merge the missing columns (more efficient)
                    merge_columns = ["id"] + list(missing_columns)
                    full_subset = full_df[merge_columns].drop_duplicates(subset=["id"])

                    # LEFT JOIN: preserve all SQL rows, add missing columns
                    selected_data = selected_data.merge(
                        full_subset, on="id", how="left"
                    )
                    logger.info(
                        f"Enrichment: added {len(missing_columns)} missing columns"
                    )

            except Exception as e:
                logger.warning(f"Failed to enrich with full dataset: {e}")
                # Continue with whatever data we have

        # 1. Calculate travel times on the SELECTED subset first (for performance)
        location_list = location_payload if isinstance(location_payload, list) else []

        if location_list:
            # Enrich with direct Haversine distance (distanza_km) for ranking & filters
            selected_data = calculate_travel_times_df(selected_data, location_list)

        # 2. Mark as matched
        enriched_data = selected_data.copy()
        enriched_data["is_match"] = True

        if "distanza_km" not in enriched_data.columns:
            enriched_data["distanza_km"] = np.nan

        state["selected_data"] = enriched_data # Update state
        state["context"].filtered_dataset_preview = enriched_data.head(10).to_dict(
            "records"
        )
        return state

    def _extract_search_radius(self, sql_query: str) -> float:
        """Extract search radius from SQL WHERE clause (e.g., '< 15' -> 15.0)."""
        import re

        if not sql_query:
            return 5.0

        # Look for pattern like: haversine_km(...) < 15
        match = re.search(
            r"haversine_km.*?<\s*(\d+(?:\.\d+)?)", sql_query, re.IGNORECASE
        )
        return float(match.group(1)) if match else 5.0

    def _calculate_ranking_weights(self, state: GraphState) -> GraphState:
        self._update_progress(state, "ranking", "")
        query = state["query"]
        
        # Check if ranking was already computed in baseline phase
        ranking_result = state.get("ranking_result")
        duration_ms = 0
        
        if ranking_result and self.architecture == "baseline":
            logger.info("Ranking weights already provided by Baseline Planner, using them.")
        else:
            if not ranking_result:
                disabled = state.get("disabled_agents", [])
                if "ranking" in disabled:
                    logger.info("RankingAgent disabled by ablation config, using uniform weights.")
                    ranking_result = RankingAgentResult(
                        raw_text="{\"reasoning\": \"Ranking disabled for ablation comparison. Using uniform weights.\"}",
                        weights=RankingWeights()
                    )
                else:
                    start_t = time.time()
                    ranking_result = self.ranking_agent.run(
                        query=query, 
                        mode="filtering", 
                        db_metadata=state.get("db_metadata")
                    )
                    duration_ms = (time.time() - start_t) * 1000
                state["ranking_result"] = ranking_result

        state["context"].ranking_result = ranking_result

        # ALWAYS populate gemini_responses if we have a result
        if ranking_result:
            state["gemini_responses"]["ranking_weights"] = {
                "prompt": ranking_result.prompt.model_dump() if ranking_result.prompt else None,
                "response": ranking_result.raw_text,
                "weights": ranking_result.weights.model_dump()
            }
            self._log_execution(state, "ranking-agent", ranking_result, duration_ms, mode="ranking")
        
        return state


    def _rank_results(self, state: GraphState) -> GraphState:
        self._update_progress(state, "ranking", "")
        df = state["selected_data"]
        if df is None or df.empty:
            return state

        if "id" not in df.columns and (df.index.name == "id" or "id" in df.index.names):
            df = df.reset_index()

        # Fix: Ensure ID is string for matching with agent results (which use string IDs from JSON)
        if "id" in df.columns:
            df["id"] = df["id"].astype(str)

        ranking_res = state.get("ranking_result")
        weights = ranking_res.weights if ranking_res else RankingWeights()
        
        # Get active agents from ranking result
        active_agents = []
        if ranking_res and ranking_res.ranking:
            # Extract agent names from RankedAgent list
            active_agents = [r.agent_name for r in ranking_res.ranking.ranking]
        else:
            # Fallback to all if ranking failed
            active_agents = ["location", "regulatory", "energy", "building", "proximity"]
        
        logger.info(f"Active agents for ranking: {active_agents}")

        # Identify agents that actually found something during filtering phase
        # to exclude those that returned nothing from final ranking determination.
        really_found_agents = []
        
        # 1. Location
        if state["context"].locations and len(state["context"].locations) > 0:
            really_found_agents.append("location")
            
        # 2. Building
        if state.get("building_result"):
            t_data = safe_extract_json(state["building_result"].raw_text, schema=BuildingResponse)
            if t_data and (len(t_data.typologies) > 0 or len(t_data.requirements) > 0):
                really_found_agents.append("building")
                
        # 3. Energy
        if state.get("energy_result"):
            a_data = safe_extract_json(state["energy_result"].raw_text, schema=EnergyResponse)
            if a_data and len(a_data.requirements) > 0:
                really_found_agents.append("energy")
                
        # 4. Proximity
        if state.get("proximity_result"):
            p_data = safe_extract_json(state["proximity_result"].raw_text)
            if p_data and p_data.get("requirements") and len(p_data["requirements"]) > 0:
                really_found_agents.append("proximity")
                
        # 5. Regulatory
        if state.get("regulatory_result"):
            n_data = safe_extract_json(state["regulatory_result"].raw_text, schema=RegulatoryResponse)
            if n_data and n_data.requirements and len(n_data.requirements) > 0:
                really_found_agents.append("regulatory")

        logger.info("Agents with found requirements in filtering phase: {}", really_found_agents)

        # Check for agents that are in active_agents (selected by query) but didn't find anything
        agents_to_exclude = [a for a in active_agents if a not in really_found_agents and getattr(weights, a, 0.0) > 0]
        
        if agents_to_exclude:
            logger.info(" Excluding agents from ranking due to no results in filtering: {}", agents_to_exclude)
            
            # Copy weights and identify target for redistribution
            current_weights_dict = weights.model_dump()
            
            # Remaining agents from active_agents that found something
            active_and_found = [a for a in active_agents if a in really_found_agents]
            
            # Sum of weights of agents to be kept
            remaining_weight_sum = sum(current_weights_dict[a] for a in active_and_found)
            
            if remaining_weight_sum > 0:
                # Redistribute the total weight of excluded agents to remaining found agents
                new_weights_dict = {}
                for a in ["location", "building", "energy", "proximity", "regulatory"]:
                    if a in active_and_found:
                        # Proportional redistribution
                        new_weights_dict[a] = round(current_weights_dict[a] / remaining_weight_sum, 2)
                    else:
                        new_weights_dict[a] = 0.0

                # Adjustment for rounding to ensure sum is exactly 1.0
                current_total = sum(new_weights_dict.values())
                diff = round(1.0 - current_total, 2)
                if diff != 0 and active_and_found:
                    # Adjust the agent with the highest new weight
                    max_agent = max(active_and_found, key=lambda a: new_weights_dict[a])
                    new_weights_dict[max_agent] = round(new_weights_dict[max_agent] + diff, 2)
                
                weights = RankingWeights(**new_weights_dict)
                logger.info(" Adjusted ranking weights: {}", new_weights_dict)
                
                # Update active_agents to only include those that really found something
                # This avoids running ranking mode for agents with 0 weight
                active_agents = active_and_found
            else:
                logger.warning(
                    "No active agent returned requirements in filtering. "
                    "Falling back to uniform weights (0.2 each) so that ranking still runs "
                    "and properties are scored by their intrinsic qualities."
                )
                # FALLBACK UNIFORME: nessun agente ha trovato requisiti specifici
                # (query generica). Usiamo pesi uniformi e lasciamo girare tutti gli agenti
                # di ranking in modo da produrre comunque uno score significativo basato
                # sulle caratteristiche intrinseche degli immobili (classe energetica,
                # zona, servizi di prossimità, ecc.).
                uniform_weights_dict = {a: 0.2 for a in ["location", "building", "energy", "proximity", "regulatory"]}
                weights = RankingWeights(**uniform_weights_dict)
                active_agents = ["location", "building", "energy", "proximity", "regulatory"]

        # Compute global statistics for all relevant columns for ranking
        # This allows agents to normalize scores against the entire dataset instead of the current subset.
        all_ranking_cols = list(set(ENERGY_AGENT_COLUMNS + REGULATORY_AGENT_COLUMNS + PROXIMITY_AGENT_COLUMNS + BUILDING_AGENT_COLUMNS))
        global_stats = self._get_column_statistics(
            columns=all_ranking_cols,
            dataset_path=state.get("dataset_path"),
            dataset_df=state.get("base_dataset"),
            db_metadata=state.get("db_metadata")
        )

        # Define ranking tasks for parallel execution
        def rank_building():
            start_t = time.time()
            res = state.get("building_result")
            if res:
                data = safe_extract_json(res.raw_text, schema=BuildingResponse)
                if data and (data.typologies or data.requirements):
                    # For ranking, typologies is the main driver, but we pass requirements for numerical scoring
                    tmp = self.building_agent.run(
                        mode="ranking", 
                        df=df.copy(), 
                        ranked_typologies=data.typologies,
                        requirements=data.requirements,
                        global_stats=global_stats
                    )
                    return tmp, (time.time() - start_t) * 1000
            tmp = df.copy()
            tmp["building_score"] = 0.0
            return tmp[["id", "building_score"]], (time.time() - start_t) * 1000

        def rank_location():
            start_t = time.time()
            if state["context"].locations:
                 tmp = self.location_agent.run(mode="ranking", df=df.copy(), places=state["context"].locations)
                 return tmp, (time.time() - start_t) * 1000
            tmp = df.copy()
            tmp["location_score"] = 0.0
            return tmp[["id", "location_score"]], (time.time() - start_t) * 1000

        def rank_energy():
            start_t = time.time()
            res = state.get("energy_result")
            requirements = None
            if res:
                data = safe_extract_json(res.raw_text, schema=EnergyResponse)
                if data and data.found:
                    requirements = data.requirements
            
            tmp = self.energy_agent.run(mode="ranking", df=df.copy(), requirements=requirements, global_stats=global_stats)
            return tmp, (time.time() - start_t) * 1000

        def rank_regulatory():
            start_t = time.time()
            res = state.get("regulatory_result")
            if res:
                data = safe_extract_json(res.raw_text, schema=RegulatoryResponse)
                if data and data.found:
                    tmp = self.regulatory_agent.run(mode="ranking", df=df.copy(), requirements=data.requirements, available_columns=REGULATORY_AGENT_COLUMNS, global_stats=global_stats)
                    return tmp, (time.time() - start_t) * 1000
            tmp = df.copy()
            tmp["regulatory_score"] = 0.0
            return tmp[["id", "regulatory_score"]], (time.time() - start_t) * 1000

        def rank_proximity():
            start_t = time.time()
            res = state.get("proximity_result")
            if res:
                proximity_data = safe_extract_json(res.raw_text)
                if proximity_data and proximity_data.get("requirements"):
                    tmp = self.proximity_agent.run(mode="ranking", df=df.copy(), requirements=proximity_data.get("requirements"), global_stats=global_stats)
                    return tmp, (time.time() - start_t) * 1000
            tmp = df.copy()
            tmp["proximity_score"] = 0.0
            return tmp[["id", "proximity_score"]], (time.time() - start_t) * 1000

        # Build active ranking tasks based on active_agents
        ranking_tasks = {}
        if "building" in active_agents:
            ranking_tasks["building"] = rank_building
        if "location" in active_agents:
            ranking_tasks["location"] = rank_location
        if "energy" in active_agents:
            ranking_tasks["energy"] = rank_energy
        if "regulatory" in active_agents:
            ranking_tasks["regulatory"] = rank_regulatory
        if "proximity" in active_agents:
            ranking_tasks["proximity"] = rank_proximity

        # Execute parallel ranking tasks (only for active agents)
        with ThreadPoolExecutor(max_workers=len(ranking_tasks) if ranking_tasks else 1) as executor:
            task_map = {
                executor.submit(task): name 
                for name, task in ranking_tasks.items()
            }
            
            for future in as_completed(task_map):
                name = task_map[future]
                try:
                    res_df, duration = future.result()
                    
                    # Log ranking results as JSON for UI visualization
                    if not res_df.empty:
                        # Transform to clean JSON (handles NaNs by converting to null)
                        # and put it at the top level for the UI to show an accordion
                        clean_json = res_df.to_json(orient="records")
                        state["gemini_responses"][f"{name}_ranking"] = {
                            "response": json.loads(clean_json)
                        }
                        
                        # Log this ranking agent execution to the trace
                        self._log_execution(state, f"{name}-agent", res_df, duration, mode="ranking", global_stats=global_stats)

                    # Aggiungiamo solo nuove colonne evitando duplicazioni
                    new_cols = [c for c in res_df.columns if c not in df.columns or c == "id"]
                    df = df.merge(res_df[new_cols], on="id", how="left")
                except Exception as e:
                    logger.error(f"Error in parallel ranking part {name}: {e}")
                    col = f"{name}_score"
                    if col not in df.columns:
                        df[col] = 0.0

        # Ensure all agent score columns exist (set to 0.0 for inactive agents)
        all_possible_agents = ["location", "regulatory", "energy", "building", "proximity"]
        for agent in all_possible_agents:
            score_col = f"{agent}_score"
            if score_col not in df.columns:
                logger.info(f"Agent '{agent}' not active, setting {score_col} to 0.0")
                df[score_col] = 0.0

        # Calculate final weighted score via RankingAgent (ranking mode)
        start_t = time.time()
        df = self.ranking_agent.run(mode="ranking", df=df, weights=weights)
        duration_ms = (time.time() - start_t) * 1000
        
        # Log this final ranking step
        self._log_execution(state, "ranking-agent", df, duration_ms, mode="ranking")
        self._update_progress(state, "ranking", "", status="done")

        # Sort by total score
        df = df.sort_values(by="final_ranking_score", ascending=False)
        state["selected_data"] = df
        
        return state

    def _evaluate_results(self, state: GraphState) -> GraphState:
        enriched_data = state["selected_data"]

        # Restore top 10 as per user request
        llm_cap = MAX_LLM_CAP

        # Limit evaluation to top results defined by llm_cap
        eval_input_df = enriched_data.head(llm_cap).copy()
        eval_input_df["is_evaluated"] = True

        total_items = len(eval_input_df)
        self._update_progress(
            state,
            "evaluation",
            "",
        )

        # Evaluation batching
        # Increased batch size for faster models to reduce sequential overhead
        # Process one by one (batch size 1) as per user request
        batch_size = 1
        
        batches = [
            eval_input_df[i : i + batch_size]
            for i in range(0, len(eval_input_df), batch_size)
        ]

        all_results = []
        total_batches = len(batches)
        finished_batches = 0

        # Columns relevant for LLM evaluation (reduced set for clarity and token efficiency)
        EVAL_COLUMNS = [
            "id",
            "indirizzo",
            "numero_civico",
            "zona_omi",
            "superficie_di_riferimento_mq",
            "tipologia_bene_immobile",
            "epoca_costruzione",
            "finalita",
            # Energy data
            "classe_energetica_ape",
            "ape_score_classe",
            "ape_score_impianto",
            "ape_score_involucro",
            "ape_score_rinnovabili",
            "ape_score_total",
            # POI scores
            "sanita",
            "mobilita",
            "verde",
            "sport",
            "commerciale",
            "educazione",
            # Travel times (if enriched)
            "distanza_km",
            # Agent scores
            "ape_score",
            "energy_score",
            "location_score",
            "normative_score",
            "regulatory_score",
            "property_technical_score",
            "building_score",
            "poi_score",
            "proximity_score",
            # Calculated Score
            "final_ranking_score"
        ]

        def prepare_estates_json(batch_df: pd.DataFrame) -> str:
            """Convert DataFrame to JSON with only relevant columns for LLM evaluation."""
            # Select only columns that exist in the dataframe
            available_cols = [c for c in EVAL_COLUMNS if c in batch_df.columns]
            subset = batch_df[available_cols].copy()

            # Identify columns that are numeric scores to be rounded to nearest int
            # Includes explicit scores (ending in _score or score_*) and POI pillars (1-5 range)
            poi_pillars = ["sanita", "mobilita", "verde", "sport", "commerciale", "educazione"]
            score_cols_to_round = []
            
            for col in subset.columns:
                if not pd.api.types.is_numeric_dtype(subset[col]):
                    continue
                    
                is_score = (
                    col.endswith("score") or 
                    "_score_" in col or 
                    col == "final_ranking_score" or 
                    col == "score" or
                    col in poi_pillars
                )
                
                if is_score:
                    score_cols_to_round.append(col)

            # Round to nearest integer and cast to int
            for col in score_cols_to_round:
                # fillna(0) for safety on scores, though some might naturally be NaN. 
                # For presentation to LLM as "rounded score", 0 is reasonable default for missing.
                subset[col] = pd.to_numeric(subset[col], errors='coerce').fillna(0).round().astype(int)

            # We DO NOT rename final_ranking_score to score anymore, as per user request.
            # LLM prompt now expects 'final_ranking_score'.
            
            # Convert to list of dicts
            records = subset.to_dict(orient="records")

            # Clean up NaN/None values to "N/D" for readability
            for record in records:
                for key, val in list(record.items()):
                    # Check for None, NaN (float), or pandas NA
                    if val is None or (isinstance(val, float) and np.isnan(val)):
                        record[key] = "N/D"

            return json.dumps(records, indent=2, ensure_ascii=False)

        def process_batch(batch_df):
            if batch_df.empty:
                return []

            start_t = time.time()
            # Use JSON format instead of tabulate for better LLM comprehension
            # The `eval_input_df` is already capped by `llm_cap` (now 10)
            # and `batch_df` is a slice of `eval_input_df`.
            # So, `estates_data_str` already represents a subset of the top `llm_cap` results.
            estates_data_str = prepare_estates_json(batch_df)

            logger.info(f"Evaluating batch of {len(batch_df)} items")
            eval_payload: EvaluationAgentResponse = self.evaluation_agent.run(
                use_case=state["use_case_str"],
                estates_data=estates_data_str,
                original_query=state["query"],  # NEW: pass original query for context
                score_legend=SCORE_LEGEND,
            )
            return eval_payload, (time.time() - start_t) * 1000

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(process_batch, batch): i
                for i, batch in enumerate(batches)
            }

            for future in as_completed(futures):
                try:
                    payload, duration = future.result()
                    finished_batches += 1
                    
                    # Parse results from raw_text using EvaluationList schema
                    eval_data = safe_extract_json(payload.raw_text, schema=EvaluationList)
                    batch_results = []
                    if eval_data and eval_data.evaluations:
                        batch_results = eval_data.evaluations
                    else:
                        # Fallback for unexpected formats
                        raw_data = safe_extract_json(payload.raw_text)
                        if isinstance(raw_data, list):
                            batch_results = [EvaluationResult(**r) if isinstance(r, dict) else r for r in raw_data]
                        elif isinstance(raw_data, dict) and "evaluations" in raw_data:
                            batch_results = [EvaluationResult(**r) if isinstance(r, dict) else r for r in raw_data["evaluations"]]
                    
                    # Update progress dynamically for each batch
                    self._update_progress(
                        state,
                        "evaluation",
                        "",
                    )
                    
                    self._log_execution(state, "evaluation-agent", payload, duration, mode="evaluation")

                    if batch_results:
                        all_results.extend(batch_results)
                        # We keep the last prompt/response for logging purposes
                        state["gemini_responses"]["evaluation"] = {
                            "prompt": (
                                payload.prompt.model_dump() if payload.prompt else None
                            ),
                            "response": payload.raw_text,
                            "results_count": len(all_results),
                        }
                except Exception as e:
                    logger.error(f"Error parsing evaluation batch: {e}")
                    pass

        state["context"].evaluation_results = all_results
        # Update full results list in log
        if "evaluation" in state["gemini_responses"]:
            state["gemini_responses"]["evaluation"]["results"] = [
                res.model_dump() if hasattr(res, "model_dump") else res for res in all_results
            ]

        self._update_progress(
            state, "evaluation", "", status="done"
        )
        return state

    def _broker_review(self, state: GraphState) -> GraphState:
        self._update_progress(state, "broker", "")

        eval_results = state["context"].evaluation_results

        # Create structured text for the broker
        candidates = []
        for i, res in enumerate(eval_results[:MAX_LLM_CAP]):  # Analyze top candidates
            if hasattr(res, "id") and hasattr(res, "final_ranking_score"):
                # Handle as Pydantic model
                candidates.append(
                    f"Candidato #{i+1} (ID: {res.id}, Score: {res.final_ranking_score}):\n"
                    f"Motivazione: {res.evaluation_text}\n"
                    f"Pro: {', '.join(res.pros)}\n"
                    f"Contro: {', '.join(res.cons)}\n"
                )
            elif isinstance(res, dict):
                # Handle as dictionary
                score_val = res.get("final_ranking_score") or res.get("score")
                candidates.append(
                    f"Candidato #{i+1} (ID: {res.get('id')}, Score: {score_val}):\n"
                    f"Motivazione: {res.get('evaluation_text')}\n"
                    f"Pro: {', '.join(res.get('pros', []))}\n"
                    f"Contro: {', '.join(res.get('cons', []))}\n"
                )

        candidates_text = "\n---\n".join(candidates)

        summary = self.broker_agent.run(
            query=state["query"], candidates_data=candidates_text
        )

        state["broker_summary"] = summary
        state["gemini_responses"]["broker_review"] = summary
        self._update_progress(state, "broker", "", status="done")
        return state

    def _finalize_results(self, state: GraphState) -> GraphState:
        trace_len = len(state.get("agent_trace", []))
        logger.info(f"Finalizing results. Agent trace size: {trace_len}")

        if state["selected_data"].empty:
            state["status_msg"] = (
                "La ricerca non ha prodotto risultati specifici. Mostro un campione di immobili."
            )

            # Fallback: Fetch a sample from DuckDB since working_dataset is not in state
            dataset_path = state.get("dataset_path")
            fallback_query = "SELECT * FROM IMMOBILI LIMIT 500"
            # Use execute_sql_fn to get data
            fallback_data, _ = self.execute_sql_fn(
                fallback_query, None, dataset_path=dataset_path
            )

            full_df = fallback_data
            full_df["is_match"] = False
            full_df["score"] = 0
            full_df["is_selected_by_llm"] = False
            full_df["is_evaluated"] = False

            if "distanza_km" not in full_df.columns:
                full_df["distanza_km"] = np.nan

            state["selected_data"] = full_df
            state["match_count"] = 0
            state["context"].filtered_dataset_preview = []
            state["gemini_responses"]["agent_context"] = state["context"].model_dump()
            self._update_progress(state, "complete", "")
            return state

        # Dati arricchiti dal percorso agente (solo subset selezionato dalla query)
        enriched_data = state["selected_data"]
        map_cap = self._resolve_map_cap(state.get("map_limit"))
        llm_cap = self._resolve_llm_cap(state.get("llm_limit"))
        # Carica SEMPRE il dataset completo da DuckDB/IMMOBILI per la mappa,
        # così l'esperto può vedere tutti gli edifici (fino al limite mappa),
        # indipendentemente da come l'agente ha filtrato i candidati.
        base_df: Optional[pd.DataFrame] = None
        dataset_path = state.get("dataset_path")
        try:
            if dataset_path:
                base_df, _ = self.execute_sql_fn(
                    "SELECT * FROM IMMOBILI", None, dataset_path=dataset_path
                )
        except Exception as e:
            logger.warning(
                f"Impossibile caricare il dataset completo per la mappa; "
                f"uso il solo subset arricchito. Dettagli: {e}"
            )
            base_df = None

        if base_df is None or base_df.empty:
            # Fallback: mantieni il comportamento precedente (solo subset arricchito)
            base_df = enriched_data.copy()

        map_df = base_df.copy()

        # Normalizza ID per i merge
        if "id" not in map_df.columns and (map_df.index.name == "id" or "id" in map_df.index.names):
            map_df = map_df.reset_index()
        if "id" in map_df.columns:
            map_df["id"] = map_df["id"].astype(str)
        if "id" not in enriched_data.columns and (enriched_data.index.name == "id" or "id" in enriched_data.index.names):
            enriched_data = enriched_data.reset_index()
        if "id" in enriched_data.columns:
            enriched_data = enriched_data.copy()
            enriched_data["id"] = enriched_data["id"].astype(str)

        # Sovrascrivi/aggiungi colonne di matching e ranking solo dove l'agente ha lavorato
        overlay_cols = ["id", "is_match", "final_ranking_score", "distanza_km"]
        
        # Drop existing columns from map_df to avoid suffix collisions and ensure overlay wins
        cols_to_drop = [c for c in overlay_cols if c != "id" and c in map_df.columns]
        if cols_to_drop:
            map_df = map_df.drop(columns=cols_to_drop)

        if not enriched_data.empty:
            actual_overlay_cols = [c for c in overlay_cols if c in enriched_data.columns]
            overlay_df = enriched_data[actual_overlay_cols].drop_duplicates("id")
            map_df = pd.merge(map_df, overlay_df, on="id", how="left")

        # Default robusti per flag e score
        if "is_match" not in map_df.columns:
            map_df["is_match"] = False
        map_df["is_match"] = map_df["is_match"].fillna(False).infer_objects(copy=False).astype(bool)

        if "final_ranking_score" in map_df.columns:
            map_df["final_ranking_score"] = pd.to_numeric(
                map_df["final_ranking_score"], errors="coerce"
            ).fillna(0).infer_objects(copy=False)

        eval_results = state["context"].evaluation_results
        
        # The true "found" count is the number of matching properties
        total_matches = len(enriched_data)
        state["match_count"] = total_matches
        evaluated_total = 0

        if eval_results:
            # Costruisci DataFrame con le valutazioni LLM (solo per gli ID valutati)
            val_data: List[Dict[str, Any]] = []
            for res in eval_results:
                pro_text = "\n".join(res.pros) if res.pros else ""
                contro_text = "\n".join(res.cons) if res.cons else ""
                val_data.append(
                    {
                        "id": res.id,
                        "final_ranking_score": res.final_ranking_score,
                        "motivazione": res.evaluation_text,
                        "pro": pro_text,
                        "contro": contro_text,
                    }
                )

            valutazioni_df = pd.DataFrame(val_data)

            if not valutazioni_df.empty and "id" in map_df.columns:
                valutazioni_df["id"] = valutazioni_df["id"].astype(str)
                evaluated_total = len(valutazioni_df)

                # Keep a backup of deterministic final_ranking_score before merging
                det_scores = map_df[["id", "final_ranking_score"]].copy() if "final_ranking_score" in map_df.columns else pd.DataFrame(columns=["id", "final_ranking_score"])

                if "final_ranking_score" in map_df.columns:
                    map_df = map_df.drop(columns=["final_ranking_score"])

                map_df = pd.merge(
                    map_df,
                    valutazioni_df[
                        ["id", "final_ranking_score", "motivazione", "pro", "contro"]
                    ].drop_duplicates("id"),
                    on="id",
                    how="left",
                )

                # For buildings not evaluated, fall back to the deterministic score
                if not det_scores.empty:
                    map_df = pd.merge(map_df, det_scores, on="id", how="left", suffixes=("", "_det"))
                    if "final_ranking_score_det" in map_df.columns:
                        map_df["final_ranking_score"] = map_df["final_ranking_score"].fillna(map_df["final_ranking_score_det"])
                        map_df = map_df.drop(columns=["final_ranking_score_det"])

                map_df["final_ranking_score"] = pd.to_numeric(
                    map_df["final_ranking_score"], errors="coerce"
                ).fillna(0)
                map_df["is_evaluated"] = map_df["id"].isin(valutazioni_df["id"])
                map_df["is_selected_by_llm"] = map_df["final_ranking_score"] >= 60
            else:
                # Caso in cui eval_results esiste ma valutazioni_df è vuoto
                # (es. errore nel parsing dei singoli item)
                if "final_ranking_score" not in map_df.columns:
                    map_df["final_ranking_score"] = 0
                map_df["is_evaluated"] = False
                map_df["is_selected_by_llm"] = False
        else:
            # Caso in cui NON sono state proprio effettuate valutazioni (classico fallback)
            eval_ids = (
                enriched_data.head(llm_cap)["id"]
                if "id" in enriched_data.columns and llm_cap > 0
                else []
            )
            if "id" in map_df.columns:
                map_df["is_evaluated"] = map_df["id"].isin(eval_ids)
            else:
                map_df["is_evaluated"] = False
            
            # CRITICAL: Preserve existing final_ranking_score if it exists (from ranking_agent)
            if "final_ranking_score" not in map_df.columns:
                map_df["final_ranking_score"] = 0
            map_df["is_selected_by_llm"] = False

        # Normalizza flag booleani
        for col in ["is_evaluated", "is_selected_by_llm"]:
            if col in map_df.columns:
                map_df[col] = map_df[col].fillna(False).astype(bool)

        # Ordina: prima i risultati che matchano la query, poi ranking/ distanza
        sort_cols = []
        ascending = []
        if "is_match" in map_df.columns:
            sort_cols.append("is_match")
            ascending.append(False)
        if "final_ranking_score" in map_df.columns:
            sort_cols.append("final_ranking_score")
            ascending.append(False)
        if "distanza_km" in map_df.columns:
            sort_cols.append("distanza_km")
            ascending.append(True)

        if sort_cols:
            map_df = map_df.sort_values(sort_cols, ascending=ascending)

        # Applica il limite mappa globale (MAX_ITEMS_FOR_MAP o override utente)
        if len(map_df) > map_cap:
            map_df = map_df.head(map_cap)

        # Costruisci messaggio di stato coerente con i limiti applicati
        shown_count = len(map_df)
        evaluated_in_map = (
            int(map_df["is_evaluated"].sum()) if "is_evaluated" in map_df.columns else 0
        )
        selected_in_map = (
            int(map_df["is_selected_by_llm"].sum())
            if "is_selected_by_llm" in map_df.columns
            else 0
        )

        if evaluated_total > 0:
            msg = (
                f"Trovati {total_matches} immobili corrispondenti. "
                f"Punti visibili in mappa: {shown_count} (limite {map_cap}). "
                f"LLM ha valutato {evaluated_total}/{llm_cap} elementi. "
            )
        else:
            msg = (
                f"Trovati {total_matches} immobili corrispondenti. "
                f"Punti visibili in mappa: {shown_count} (limite {map_cap}). "
                "Nessuna valutazione LLM disponibile."
            )

        state["status_msg"] = msg
        logger.info(msg)

        state["selected_data"] = map_df  # Risultato finale per la mappa
        state["gemini_responses"]["agent_context"] = state["context"].model_dump()
        return state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_llm_cap(self, llm_limit: Optional[int]) -> int:
        try:
            val = int(llm_limit) if llm_limit else MAX_ITEMS_FOR_LLM
            return min(max(1, val), MAX_LLM_CAP)
        except (TypeError, ValueError):
            return MAX_ITEMS_FOR_LLM

    def _resolve_map_cap(self, map_limit: Optional[int]) -> int:
        try:
            m_val = int(map_limit) if map_limit else MAX_ITEMS_FOR_MAP
            return max(1, m_val)
        except (TypeError, ValueError):
            return MAX_ITEMS_FOR_MAP
