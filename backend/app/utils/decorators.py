import functools
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.utils.logger import LLM_LOG_DIR, logger


def retry_with_backoff(
    retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator that retries a function call with exponential backoff.

    Args:
        retries: Maximum number of retries.
        initial_delay: Initial delay in seconds.
        backoff_factor: Multiplier for the delay after each failure.
        exceptions: Tuple of exceptions to catch and retry on.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None

            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == retries:
                        logger.error(
                            f"Function {func.__name__} failed after {retries} retries. Last error: {e}"
                        )
                        raise last_exception

                    sleep_time = delay + random.uniform(0, 0.1)  # Add jitter
                    logger.warning(
                        f"Function {func.__name__} failed (Attempt {attempt + 1}/{retries + 1}). Retrying in {sleep_time:.2f}s. Error: {e}"
                    )
                    time.sleep(sleep_time)
                    delay *= backoff_factor

            return None  # Should not be reached

        return wrapper

    return decorator


def _record_agent_error(agent_name: str, function: str, error: Exception, query: Any) -> None:
    """Append the error to $AGENT_ERROR_LOG (JSON lines) when set. The agents
    return a fallback on error, so in an evaluation run a failed call would
    otherwise look like 'found nothing'; tests/audit_runs.py reads this file."""
    import json
    import os
    from datetime import datetime

    path = os.environ.get("AGENT_ERROR_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "agent": agent_name,
                                "function": function, "error": f"{type(error).__name__}: {error}"[:300],
                                "query": query if isinstance(query, str) else None}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def handle_agent_error(fallback_value: Any = None):
    """
    Decorator to handle exceptions in Agent methods safely.
    Logs the error with correlation ID and full traceback, returns a fallback value.

    Args:
        fallback_value: Value to return if an exception occurs.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            import traceback
            import uuid

            # Generate correlation ID for this execution
            correlation_id = str(uuid.uuid4())[:8]

            try:
                return func(*args, **kwargs)
            except Exception as e:
                agent_name = (
                    getattr(args[0], "name", "Unknown Agent")
                    if args
                    else "Unknown Function"
                )

                # Log with correlation ID and full context
                logger.error(
                    "[{}] Error in {}.{}: {}",
                    correlation_id,
                    agent_name,
                    func.__name__,
                    e,
                    extra={
                        "correlation_id": correlation_id,
                        "agent_name": agent_name,
                        "function": func.__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
                _record_agent_error(agent_name, func.__name__, e, kwargs.get("query"))

                # If a fallback is provided, return it
                if fallback_value is not None:
                    return fallback_value

                return None

        return wrapper

    return decorator


def _write_llm_log(entry: dict) -> None:
    try:
        log_dir = Path(LLM_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"llm_usage_{datetime.utcnow():%Y-%m-%d}.log"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"Failed to write LLM log: {exc}")


def log_llm_usage(func: Callable) -> Callable:
    """Decorator that records metadata for each LLM-backed agent call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        agent = args[0] if args else None
        agent_name = getattr(agent, "name", func.__qualname__)

        llm_client = getattr(agent, "llm", None)
        model_name = None
        if llm_client is not None:
            for attr in ("model_name", "model", "deployment_name"):
                model_name = getattr(llm_client, attr, None)
                if model_name:
                    break

        start_time = time.time()
        status = "success"
        error_message = ""

        def _shorten(value: Any) -> str:
            text = value if isinstance(value, str) else str(value)
            return text[:200] + ("..." if len(text) > 200 else "")

        def _collect_context() -> dict:
            context: dict[str, str] = {}
            keys = ("query", "use_case", "estates_data")
            for key in keys:
                value = kwargs.get(key)
                if isinstance(value, str) and value:
                    context[key] = _shorten(value)
            if "query" not in context and len(args) > 1 and isinstance(args[1], str):
                context["query"] = _shorten(args[1])
            return context

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as exc:
            status = "error"
            error_message = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "agent": agent_name,
                "model": model_name or "unknown",
                "status": status,
                "duration_ms": duration_ms,
                "context": _collect_context(),
            }
            if error_message:
                entry["error"] = error_message
            _write_llm_log(entry)

    return wrapper
