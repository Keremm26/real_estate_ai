"""Analysis Service - Orchestrates LLM-based real estate analysis.

This service wraps the existing graph_agent orchestrator and provides:
- Async support for FastAPI integration
- Progress callbacks for WebSocket streaming
- Structured results for API responses
- Error handling and logging

NOTE: Heavy LLM/agent dependencies are imported lazily to keep import-time fast
and to make mocked tests run without pulling large optional stacks.
"""

import json
from app.services.agent_logger import AgentLogger
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, TYPE_CHECKING
import asyncio
import pandas as pd
from datetime import datetime
import logging

from app.data.loaders import load_and_merge_data
from app.core.config import settings
from app.services.real_estate_service import RealEstateService
from app.utils.json_sanitizer import make_json_safe

if TYPE_CHECKING:
    from app.services.llm.agents.graph_agent import GraphOrchestratorAgent, OrchestratorResult

import os

logger = logging.getLogger(__name__)


class AnalysisService:
    """Service for running real estate analysis with LLM agents."""

    def __init__(self):
        """Initialize the analysis service."""
        # Typed as Any at runtime to avoid importing heavy dependencies on module import.
        self.graph_agent: Optional[Any] = None
        self._base_dataset_cache: Dict[str, pd.DataFrame] = {}
        self._real_estate_service = RealEstateService()

    def _get_or_load_dataset(self, dataset_key: str) -> pd.DataFrame:
        """
        Load dataset with caching.

        Args:
            dataset_key: Dataset identifier ('full', 'meta', 'ape')

        Returns:
            Loaded DataFrame
        """
        if dataset_key not in self._base_dataset_cache:
            dataset_path = settings.dataset_options.get(dataset_key)
            if not dataset_path:
                raise ValueError(f"Unknown dataset key: {dataset_key}")

            logger.info(f"Loading dataset: {dataset_key} from {dataset_path}")
            self._base_dataset_cache[dataset_key] = load_and_merge_data(dataset_path)
            logger.info(
                f"Dataset loaded: {len(self._base_dataset_cache[dataset_key])} records"
            )

        return self._base_dataset_cache[dataset_key]

    def _init_graph_agent(self, force: bool = False):
        """
        Initialize the graph orchestrator agent.

        Args:
            force: If True, re-initialize even if already exists.

        Returns:
            Configured GraphOrchestratorAgent
        """
        if self.graph_agent is None or force:
            if force:
                logger.info("Re-initializing GraphOrchestratorAgent (forced)...")
            else:
                logger.info("Initializing GraphOrchestratorAgent...")

            # Lazy import: avoids pulling langchain/transformers unless actually needed.
            from app.services.llm.agents.graph_agent import GraphOrchestratorAgent

            # Import SQL execution function
            from app.services.analysis.executor import execute_sql_query

            # Create agent with all dependencies
            self.graph_agent = GraphOrchestratorAgent(
                analysis_mode="agent",  # Default mode
                execute_sql_fn=execute_sql_query,
            )

            logger.info("GraphOrchestratorAgent initialized successfully")

        return self.graph_agent

    async def run_analysis(
        self,
        run_id: str,
        query: str,
        dataset_key: str = "full",
        map_limit: int = 1000,
        llm_limit: int = 25,
        analysis_mode: str = "agent",
        progress_callback: Optional[Callable[[int, List[Dict]], None]] = None,
        dataset: Optional[pd.DataFrame] = None,
        disabled_agents: Optional[List[str]] = None,
        use_data_knowledge: bool = True,
        use_relaxation: bool = True,
        architecture: str = "multiagent",
        model_type: Optional[str] = None,
        constraint_mode: str = "observe",
    ) -> Dict[str, Any]:
        """
        Run a complete real estate analysis.

        This method:
        1. Loads the appropriate dataset
        2. Initializes the LLM graph agent
        3. Runs the analysis with progress callbacks
        4. Returns structured results

        Args:
            run_id: Unique identifier for this analysis run
            query: Natural language search query
            dataset_key: Dataset to use ('full', 'meta', 'ape')
            map_limit: Max results to return for map
            llm_limit: Max results to evaluate with LLM
            analysis_mode: 'agent' or 'classic'
            progress_callback: Optional callback for progress updates
            dataset: Optional custom dataset DataFrame to use instead of loading from file

        Returns:
            Dict containing analysis results

        Raises:
            Exception: If analysis fails
        """
        try:
            logger.info(f"Starting analysis {run_id}: '{query}'")

            # Import ProgressManager
            from app.services.progress_manager import progress_manager
            from app.models.responses import ProgressUpdate, StepState

            # Load dataset or use provided one
            if dataset is not None:
                base_dataset = dataset
                dataset_path = None  # No path when using custom dataset
            else:
                base_dataset = await asyncio.to_thread(
                    self._get_or_load_dataset, dataset_key
                )
                dataset_path = settings.dataset_options.get(dataset_key)

            # Initialize agent (force re-init if model_type is explicitly provided)
            agent = self._init_graph_agent(force=(model_type is not None))

            # Prepare database schema (simplified for now)
            db_schema = {
                "columns": list(base_dataset.columns),
                "types": {
                    col: str(dtype) for col, dtype in base_dataset.dtypes.items()
                },
            }

            # Capture the main event loop BEFORE entering the thread pool.
            # The callback will be invoked from a worker thread, so we need to
            # schedule coroutines back onto the main loop via call_soon_threadsafe.
            main_loop = asyncio.get_running_loop()

            def wrapped_progress_callback(progress_tuple):
                """Wrap progress callback to publish via ProgressManager (thread-safe)."""
                try:
                    percent, steps_state = progress_tuple

                    # Extract current step info
                    current_step = ""
                    current_detail = ""

                    for step in steps_state:
                        if step.get("state") == "current":
                            current_step = step.get("label", "")
                            current_detail = step.get("detail", "")
                            break

                    # Create progress update
                    update = ProgressUpdate(
                        type="progress",
                        progress=int(percent),
                        step=current_step,
                        detail=current_detail,
                        steps_state=(
                            [StepState(**step) for step in steps_state]
                            if steps_state
                            else None
                        ),
                    )

                    # Ensure JSON safety for all fields (especially steps_state which might have numpy objects)
                    update_dict = make_json_safe(update.model_dump())
                    
                    # Schedule the coroutine on the MAIN event loop (thread-safe).
                    # This ensures WebSocket publish actually runs.
                    def schedule_publish():
                        # We use the raw dict here to avoid re-serializing in the publisher if possible,
                        # but keep the object for internal consistency if needed.
                        asyncio.create_task(progress_manager.publish(run_id_for_callback, update))

                    main_loop.call_soon_threadsafe(schedule_publish)

                    # Call original callback if provided
                    if progress_callback:
                        progress_callback(progress_tuple)

                except Exception as e:
                    logger.error(f"Error in progress callback for run {run_id}: {e}", exc_info=True)

            # Ensure we are using the correct run_id inside the wrapper
            run_id_for_callback = run_id

            # Run analysis in thread pool (since graph_agent is sync)
            logger.info("Running LLM orchestration...")
            result = await asyncio.to_thread(
                agent.run,
                query=query,
                dataset_key=dataset_key,
                base_dataset=base_dataset,
                db_schema=db_schema,
                dataset_path=dataset_path,
                set_progress=wrapped_progress_callback,
                llm_limit=llm_limit,
                map_limit=map_limit,
                disabled_agents=disabled_agents,
                use_data_knowledge=use_data_knowledge,
                use_relaxation=use_relaxation,
                analysis_mode=analysis_mode,
                architecture=architecture,
                constraint_mode=constraint_mode,
            )

            logger.info(f"Analysis {run_id} completed successfully")

            # Convert result to dict format
            return self._format_results(run_id, query, result)

        except Exception as e:
            logger.error(f"Analysis {run_id} failed: {e}", exc_info=True)
            raise

    def _format_results(
        self, run_id: str, query: str, orchestrator_result
    ) -> Dict[str, Any]:
        """
        Format orchestrator results for API response.

        Args:
            run_id: Analysis run ID
            query: Original query
            orchestrator_result: Result from GraphOrchestratorAgent

        Returns:
            Formatted results dict
        """
        # Convert DataFrame to API models
        buildings = []
        if (
            orchestrator_result.map_df is not None
            and not orchestrator_result.map_df.empty
        ):
            logger.info(
                f"Formatting {len(orchestrator_result.map_df)} rows from orchestrator"
            )
            for _, row in orchestrator_result.map_df.iterrows():
                try:
                    buildings.append(self._real_estate_service._df_row_to_building(row))
                except Exception as e:
                    logger.warning(
                        f"Skipping building row due to conversion error: {e}"
                    )
                    continue
        else:
            logger.info("Orchestrator returned no map_df or empty")

        # Generate Agent HTML Log
        html_log = None
        agent_executions = None
        agent_trace = getattr(orchestrator_result, "agent_trace", None)
        
        # Skip saving trace files if we are in synthetic evaluation (to avoid duplication)
        is_synthetic_eval = os.environ.get("_AGENT_LOGGER_ACTIVE") == "true"
        
        if agent_trace is not None and not is_synthetic_eval:
            try:
                # Use settings for log directory
                log_dir = Path(settings.AGENT_LOGS_DIR)
                log_dir.mkdir(parents=True, exist_ok=True)
                
                agent_logger = AgentLogger(log_dir=log_dir)
                # Use a timestamp in the filename to maintain chronological history
                timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                json_filename = f"trace_{timestamp_str}_{run_id}.json"
                
                run_props = {
                    "use_case": "Live Analysis",
                    "prompt_id": run_id,
                    "user_query": query,
                    "run_number": 1,
                    "run_timestamp": datetime.utcnow().isoformat()
                }
                # Process trace data
                agent_executions = agent_logger._process_log_data_for_export(agent_trace, run_props)
                
                # Save JSON trace to disk
                json_path = log_dir / json_filename
                log_data_final = {"agent_executions": agent_executions}
                
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(log_data_final, f, indent=2, ensure_ascii=False)
                
                # Generate HTML
                html_log = agent_logger.generate_html_view(
                    agent_executions, 
                    query, 
                    json_filename=json_filename, 
                    view_mode="tabs"
                )
                logger.info(f"Saved trace to history: {json_filename}")
            except Exception as e:
                logger.error(f"Failed to generate agent HTML log: {e}")
        elif agent_trace is not None and is_synthetic_eval:
             # In synthetic eval, we still might want the structured trace in the response
             # but we skip saving to file and generating HTML.
             try:
                 agent_logger = AgentLogger() # No log_dir
                 run_props = {"use_case": "Synthetic Eval", "prompt_id": run_id, "user_query": query, "run_number": 1, "run_timestamp": datetime.utcnow().isoformat()}
                 agent_executions = agent_logger._process_log_data_for_export(agent_trace, run_props)
             except:
                 pass

        return {
            "run_id": run_id,
            "query": query,
            "status": "completed",
            "buildings": buildings,
            "location": make_json_safe(orchestrator_result.location),
            "filters_applied": {
                "where_clause": make_json_safe(orchestrator_result.where_clause),
                "final_sql": make_json_safe(orchestrator_result.sql_query),
            },
            "gemini_responses": make_json_safe(orchestrator_result.gemini_responses),
            "results_count": orchestrator_result.match_count,
            "broker_summary": make_json_safe(orchestrator_result.broker_summary),
            "agent_trace": agent_executions,
            "context": (
                orchestrator_result.context.model_dump()
                if orchestrator_result.context
                else None
            ),
            "html_log": html_log,
            "created_at": datetime.utcnow(),
            "completed_at": datetime.utcnow(),
        }

    def clear_cache(self):
        """Clear the dataset cache."""
        self._base_dataset_cache.clear()
        logger.info("Dataset cache cleared")


# Global service instance
analysis_service = AnalysisService()
