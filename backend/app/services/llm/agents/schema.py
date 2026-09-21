from typing import Union
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PromptRecord(BaseModel):
    """Record of prompts sent to the LLM model.

    - system: System instructions defining the agent's role and behavior
    - user: User template/message with variables replaced
    - full_text: Complete concatenation of system + user (for debugging)
    """

    system: str = Field(
        default="N/A",
        description="System prompt: role and behavior instructions for the agent",
    )
    user: str = Field(
        default="N/A",
        description="User prompt: specific request with interpolated data",
    )
    full_text: Optional[str] = Field(
        None, description="Full prompt sent to the model (system + user)"
    )


class Place(BaseModel):
    name: str = Field(..., description="Name of the place, POI or landmark (e.g., 'Colosseum', 'Piazza del Popolo')")
    city: Optional[str] = Field(None, description="City of the place (e.g., 'Rome')")
    lat: Optional[float] = Field(None, description="Latitude (if known)")
    lon: Optional[float] = Field(None, description="Longitude (if known)")
    radius_km: float = Field(default=3.0, description="Search radius in km (default 3.0)")



class CategoryResponse(BaseModel):
    categories: List[str] = Field(default_factory=list, description="List of selected categories")
    requirements: List[Dict[str, Any]] = Field(default_factory=list, description="List of structured requirements")


class AmenityResponse(BaseModel):
    amenities: Dict[str, List[str]] = Field(default_factory=dict, description="Amenities selected per category")


class RegulatoryResponse(BaseModel):
    requirements: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted regulatory requirements")
    found: bool = Field(default=False, description="True if pertinent requirements were found")


class EnergyResponse(BaseModel):
    requirements: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted energy requirements")
    found: bool = Field(default=False, description="True if pertinent requirements were found")


class BuildingResponse(BaseModel):
    typologies: List[str] = Field(default_factory=list, description="Selected typologies")
    found: bool = Field(default=False, description="True if pertinent requirements were found")
    requirements: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted structured requirements")


class LocationResponse(BaseModel):
    places: List[Place] = Field(default_factory=list, description="Identified places")
    found: bool = Field(default=False, description="True if geographical references were found")


class SQLResponse(BaseModel):
    sql: str = Field(..., description="Valid DuckDB SQL query (SELECT)")
    explanation: Optional[str] = Field(None, description="Brief explanation of the query logic")





class EvaluationResult(BaseModel):
    id: Union[int, str] = Field(..., description="Real estate ID")
    evaluation_text: str = Field(default="", description="Qualitative evaluation text")
    final_ranking_score: Optional[int] = Field(default=None, description="Relevance score (0-100) based on deterministic ranking")
    pros: List[str] = Field(default_factory=list, description="List of top 3 strengths")
    cons: List[str] = Field(default_factory=list, description="List of top 3 weaknesses")


class EvaluationList(BaseModel):
    evaluations: List[EvaluationResult] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Standard agent result.
    
    - prompt: Prompt metadata
    - raw_text: Model textual response
    - sources: Optional sources (e.g., URL, documents)
    """
    prompt: Optional[PromptRecord] = Field(None)
    raw_text: str = Field(..., description="Raw model response")
    sources: Optional[List[str]] = Field(default_factory=list, description="Sources or references")




# Specific aliases or classes that now follow the same schema for type backward compatibility
class LocationAgentResult(AgentResult): 
    has_locations: bool = False
    places: List[Place] = Field(default_factory=list)

class BuildingAgentResult(AgentResult): pass

class EnergyAgentResult(AgentResult): 
    has_filters: bool = False

class RegulatoryAgentResult(AgentResult):
    has_requirements: bool = False
    # Trace of the regulatory context that was actually placed in the prompt
    # (retrieval mode, in-scope chunks, context size). Used by the downstream
    # RAG evaluation and shown in the UI; None when the agent errored out.
    retrieval: Optional[Dict[str, Any]] = None

class ProximityAgentResult(AgentResult): 
    has_proximity: bool = False
    requirements: List[Dict[str, Any]] = Field(default_factory=list)

class SQLAgentResult(AgentResult):
    sql: str = ""
    explanation: str = ""

class EvaluationAgentResponse(AgentResult): pass

class RankingWeights(BaseModel):
    location: float = Field(default=0.2)
    regulatory: float = Field(default=0.2)
    energy: float = Field(default=0.2)
    building: float = Field(default=0.2)
    proximity: float = Field(default=0.2)

class RankedAgent(BaseModel):
    agent_name: str = Field(..., description="Name of the agent (location, regulatory, energy, building, proximity)")
    rank: int = Field(..., description="Ranking position (1 = highest priority). Ties are allowed.")

class RankingRanking(BaseModel):
    ranking: List[RankedAgent] = Field(
        default_factory=list,
        description="List of agents with their respective ranks"
    )
    reasoning: Optional[str] = Field(
        None, description="Brief reasoning for the chosen agents and their priorities"
    )

class RankingAgentResult(AgentResult): 
    weights: RankingWeights = Field(default_factory=RankingWeights)
    ranking: Optional[RankingRanking] = Field(None)


class RelaxationProposal(BaseModel):
    condizione_iniziale: str = Field(..., description="La condizione SQL originale (es. 'superficie > 100')")
    condizione_relaxed: str = Field(..., description="La NUOVA condizione SQL rilassata (es. 'superficie > 80')")
    piani_progressivi: Optional[List[str]] = Field(default_factory=list, description="Eventuali step intermedi di rilassamento (opzionale)")
    strategia: str = Field(..., description="Tipo di intervento (es. 'Espansione raggio', 'Cambio soglia')")
    motivazione: str = Field(..., description="Perché questo rilassamento è sensato rispetto alla query")
    livello_rilassamento: str = Field(..., description="Indice di deviazione: 'low', 'medium' o 'high'")


class RelaxationAgentResult(AgentResult):
    proposals: List[RelaxationProposal] = Field(default_factory=list)
    attempts: List[Dict[str, Any]] = Field(default_factory=list)
    final_sql: Optional[str] = None


class AgentContext(BaseModel):
    user_query: str = Field(..., description="Original user query")
    locations: List[Place] = Field(
        default_factory=list, description="Identified places"
    )
    building_result: Optional[BuildingAgentResult] = Field(
        None, description="Result of the Building Agent"
    )
    regulatory_result: Optional[RegulatoryAgentResult] = Field(
        None, description="Result of the Regulatory Agent for compliance requirements"
    )
    proximity_result: Optional[ProximityAgentResult] = Field(
        None, description="Result of the Proximity Agent for POI analysis"
    )
    energy_result: Optional[EnergyAgentResult] = Field(
        None, description="Result of the Energy Agent for EPC analysis"
    )
    ranking_result: Optional[RankingAgentResult] = Field(
        None, description="Result of the Ranking Agent for defining ranking weights"
    )
    filtered_dataset_preview: List[dict] = Field(
        default_factory=list, description="Preview of the filtered dataset"
    )
    evaluation_results: List[Any] = Field(
        default_factory=list, description="Evaluations produced by the LLM"
    )


class ChatAction(BaseModel):
    action_type: str = Field(
        ..., description="Action type: 'filter', 'reset', 'rerun', 'none'"
    )
    filter_field: Optional[str] = Field(
        None, description="Field to filter on (e.g., 'typology', 'class')"
    )
    filter_value: Optional[str] = Field(
        None, description="Filter value (e.g., 'Office', 'A4')"
    )
    reasoning: str = Field(
        ..., description="Explanation of the action or response to the user"
    )


class MapAssistantResponse(AgentResult):
    action: Optional[ChatAction] = Field(
        None, description="Action to perform on the UI"
    )

