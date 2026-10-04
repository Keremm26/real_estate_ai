def apply_model_config(settings, model_type: str):
    """
    Applies the specified model configuration to the settings instance.
    This effectively switches the LLM model and its base URL/API Key.
    """
    if hasattr(settings, "set_llm_model"):
        settings.set_llm_model(model_type)
    else:
        # Fallback if set_llm_model is missing for some reason
        settings.AGENT_LLM_MODEL = model_type

MODEL_OPTIONS = {
    "gpt-5.4": {
        "model": "gpt-5.4",
        "supports_structured_output": True,
    },
    "gpt-5.4-mini": {
        "model": "gpt-5.4-mini",
        "supports_structured_output": True,
    },
    "gpt-oss-120b": {
        "model": "gemma-4",
        "supports_structured_output": False,
    },
    "gemma3-27b": {
        "model": "gemma-4",
        "supports_structured_output": False,
    },
    "qwen3-8b": {
        "model": "gemma-4",
        "supports_structured_output": False,
    }
}
