"""
Main FastAPI application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.core.config import settings
from app.api.v1.router import api_router
from app.core.init_app import startup_event, shutdown_event
from app.core.exceptions import setup_exception_handlers

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for the application.
    Handles startup and shutdown events via modularized functions.
    """
    await startup_event()
    yield
    await shutdown_event()


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="RESTful API for RealEstate-AI analysis platform",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Setup exception handlers
setup_exception_handlers(app)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


if __name__ == "__main__":
    import uvicorn
    import argparse

    # Command line argument parsing
    parser = argparse.ArgumentParser(description=f"Start the {settings.APP_NAME} server")
    parser.add_argument(
        "--model", 
        choices=["gpt-oss-120b", "deepseek-r1-8b", "vllm-gemma3-27b", "vllm-qwen", "gpt-5-nano", "gemma4-local", "gemma4-31b"],
        help="Choose the LLM model to use"
    )
    
    # parse_known_args allows ignoring uvicorn arguments if present
    args, _ = parser.parse_known_args()

    # Apply model configuration if specified
    if args.model:
        settings.set_llm_model(args.model)
        logger.info(f"LLM model set to: {args.model} ({settings.OPENAI_MODEL_FAST})")

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
