from functools import lru_cache
import os
from typing import Optional

from dotenv import load_dotenv

from app.core.config import settings

load_dotenv()

# Global Langfuse client instance
_langfuse_client = None


@lru_cache(maxsize=8)
def _get_llm_internal(
    model_name: str, 
    temperature: float, 
    openai_api_base: Optional[str],
    openai_api_key: Optional[str] = None
):
    """Internal cached model factory to ensure unified instances."""
    
    # Select the correct API key based on override or active endpoint.
    if openai_api_key:
        api_key = openai_api_key
    else:
        # If the base URL points to the institutional instance, prefer INSTITUTIONAL_LLM_API_KEY.
        is_institutional = openai_api_base and "institutional-endpoint.edu" in openai_api_base
        if is_institutional:
            api_key = (
                settings.INSTITUTIONAL_LLM_API_KEY
                or os.getenv("INSTITUTIONAL_LLM_API_KEY")
                or settings.OPENAI_API_KEY
                or os.getenv("OPENAI_API_KEY")
            )
        else:
            api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")

    if not api_key and not openai_api_base:
        raise RuntimeError(
            "OPENAI_API_KEY non configurata per utilizzare i modelli API."
        )

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        try:
            from langchain_community.chat_models import ChatOpenAI
        except ImportError as e:
            raise RuntimeError(
                "Manca il pacchetto 'langchain-openai'. Installalo con pip install langchain-openai"
            ) from e

    print(f"[LLM] Inizializzazione modello {model_name} (Base URL: {openai_api_base or 'Default OpenAI'})")
    # Only include `chat_template_kwargs` when talking to a compatible local/institutional
    # API base that expects this non-standard parameter. Official OpenAI endpoints
    # reject unknown parameters which results in a 400 error.
    client_kwargs = dict(
        model=model_name,
        api_key=api_key or "sk-dummy",  # Fallback for local servers without auth
        temperature=temperature,
        base_url=openai_api_base,
    )

    safe_bases = ["localhost", "127.0.0.1", "institutional"]
    include_chat_template = False
    if openai_api_base:
        lower_base = openai_api_base.lower()
        # Ollama exposes an OpenAI-compatible API but does not understand vLLM's
        # `chat_template_kwargs`. It ignores the unknown field today, but it is not a
        # supported parameter there, so skip it for Ollama endpoints.
        is_ollama = "ollama" in lower_base or ":11434" in lower_base
        if not is_ollama:
            for token in safe_bases:
                if token in lower_base:
                    include_chat_template = True
                    break

    if include_chat_template:
        client_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    return ChatOpenAI(**client_kwargs)



def get_llm(
    model_name: Optional[str] = None, 
    temperature: Optional[float] = None,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None
):
    """Restituisce un'istanza Chat LLM tramite LangChain.
    Usa una cache interna per evitare di caricare lo stesso modello più volte.

    Args:
        model_name: override esplicito del modello da usare (es. 'gpt-4o', 'nvidia/Llama-3_3-Nemotron-Super-49B-v1').
        temperature: override della temperatura del modello (default: settings.AGENT_TEMPERATURE).
        api_base: override dell'URL base dell'API (es. per vLLM).
        api_key: override della chiave API.
    """
    # Determina il modello di default dinamicamente dalle impostazioni globali
    if not model_name:
        model_name = settings.LLM_MODEL

    resolved_model = model_name or os.getenv("LLM_MODEL_DEFAULT")

    # Risoluzione della temperatura: 
    # Priorità: argomento esplicito > colonna env > settings.AGENT_TEMPERATURE (default 0.0)
    if temperature is not None:
        resolved_temperature = float(temperature)
    else:
        try:
            env_temp = os.getenv("LLM_TEMPERATURE")
            if env_temp is not None:
                resolved_temperature = float(env_temp)
            else:
                resolved_temperature = float(settings.AGENT_TEMPERATURE)
        except ValueError:
            resolved_temperature = 0.0

    # Pass along overrides to the internal factory.
    return _get_llm_internal(
        model_name=resolved_model,
        temperature=resolved_temperature,
        openai_api_base=api_base or settings.OPENAI_API_BASE,
        openai_api_key=api_key
    )


def is_oss_model(model_name: Optional[str] = None) -> bool:
    """Return True when the active model does not reliably support structured output.

    Resolution order:
    1. Look up the model by name in MODEL_OPTIONS (authoritative, explicit).
    2. Fall back to heuristic substring matching for models not registered there.

    Args:
        model_name: Override the model name to check. If None, the current
            settings.LLM_MODEL is used.

    Returns:
        True if the model should use plain text output instead of
        function_calling / json_mode structured output.
    """
    try:
        from tests.model_config import MODEL_OPTIONS
        resolved = model_name or settings.LLM_MODEL
        for cfg in MODEL_OPTIONS.values():
            if cfg["model"] == resolved:
                return not cfg["supports_structured_output"]
    except ImportError:
        pass

    # Fallback: heuristic based on model name and endpoint URL.
    resolved = (model_name or settings.LLM_MODEL or "").lower()
    api_base = (settings.OPENAI_API_BASE or "").lower()
    return (
        "oss" in resolved
        or "llama" in resolved
        or "qwen" in resolved
        or "gemma" in resolved
        or "deepseek" in resolved
        or "mistral" in resolved
        or "ollama" in api_base
        or "institutional" in api_base
        or "localhost" in api_base
    )

def get_langfuse_callback(session_id: Optional[str] = None, user_id: Optional[str] = None, tags: Optional[list] = None, trace_name: Optional[str] = None):
    """Restituisce il callback handler per Langfuse se configurato.
    
    Args:
        session_id: ID sessione per raggruppare le trace (default: da LANGFUSE_SESSION_ID env var)
        user_id: ID utente (default: da LANGFUSE_USER_ID env var)
        tags: Tag per categorizzare le trace (default: da LANGFUSE_TAGS env var, comma-separated)
        trace_name: Nome della trace
    """
    try:
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        
        if not public_key or not secret_key:
            return None
        
        try:
            from langfuse.langchain import CallbackHandler
            from langfuse.types import TraceContext
        except ImportError:
            return None
        
        # Leggi da env vars se non specificato
        if not session_id:
            session_id = os.getenv("LANGFUSE_SESSION_ID", "").strip() or None
        if not user_id:
            user_id = os.getenv("LANGFUSE_USER_ID", "").strip() or None
        if not tags:
            tags_str = os.getenv("LANGFUSE_TAGS", "").strip()
            if tags_str:
                tags = [t.strip() for t in tags_str.split(",")]
        if not trace_name:
            trace_name = os.getenv("LANGFUSE_TRACE_NAME", "").strip() or None
        
        # Crea TraceContext se ci sono parametri da passare
        trace_context = None
        if session_id or user_id or tags or trace_name:
            trace_context_dict = {}
            if session_id:
                trace_context_dict['session_id'] = session_id
            if user_id:
                trace_context_dict['user_id'] = user_id
            if tags:
                trace_context_dict['tags'] = tags
            if trace_name:
                trace_context_dict['name'] = trace_name
            
            trace_context = TraceContext(**trace_context_dict)
        
        # Le credenziali vengono lette automaticamente dalle variabili d'ambiente
        # LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
        return CallbackHandler(trace_context=trace_context)
    except Exception as e:
        print(f"Langfuse callback error (tracing disabled): {e}")
        import traceback
        traceback.print_exc()
    return None


def invoke_with_langfuse(chain, inputs, session_id=None, user_id=None, tags=None, trace_name=None, **kwargs):
    """Invoca una chain LangChain con callback Langfuse se configurato.
    
    Args:
        chain: LangChain chain da invocare
        inputs: Input per la chain
        session_id: ID sessione per raggruppare le trace
        user_id: ID utente
        tags: Tag per categorizzare le trace
        trace_name: Nome della trace
        **kwargs: Altri argomenti per chain.invoke()
    """
    callback = get_langfuse_callback(session_id=session_id, user_id=user_id, tags=tags, trace_name=trace_name)
    if callback:
        callbacks = kwargs.get('callbacks', [])
        if isinstance(callbacks, list):
            callbacks.append(callback)
        else:
            callbacks = [callback]
        kwargs['callbacks'] = callbacks
    
    return chain.invoke(inputs, **kwargs)

def get_langfuse_client():
    """Restituisce il client Langfuse singleton se configurato."""
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
        
    try:
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()
        
        if not public_key or not secret_key:
            missing = []
            if not public_key:
                missing.append("LANGFUSE_PUBLIC_KEY")
            if not secret_key:
                missing.append("LANGFUSE_SECRET_KEY")
            print(f"Langfuse tracing disabled: missing environment variables: {', '.join(missing)}")
            return None
        
        try:
            from langfuse import Langfuse
        except ImportError:
            print("Langfuse tracing disabled: 'langfuse' package not installed. Install with: pip install langfuse")
            return None
            
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            flush_at=0,  # Invia immediatamente ogni evento singolo
            flush_interval=0.1  # Verifica ogni 0.1 secondi
        )
        print(f" Langfuse client initialized successfully (host: {host})")
        return _langfuse_client
        
    except Exception as e:
        print(f"Langfuse client initialization error: {e}")
        import traceback
        traceback.print_exc()
    return None

def flush_langfuse():
    """Forza il flush di tutti i dati pendenti su Langfuse."""
    client = get_langfuse_client()
    if client:
        try:
            print(" Flushing Langfuse data...")
            client.flush()
            print(" Langfuse data flushed successfully")
        except Exception as e:
            print(f"Error flushing Langfuse data: {e}")
            import traceback
            traceback.print_exc()
