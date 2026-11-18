"""FastAPI entrypoint.  Run:  uvicorn app.main:app --host 0.0.0.0 --port 4000"""
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.errors import AppError
from app.schemas import ErrorResponse, LinkAnalysisRequest, LinkAnalysisResponse
from app.services.analysis_service import AnalysisService
from app.services.llm_service import LLMService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # One pooled client for site fetches + LLM calls (per-request timeouts override this default).
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds, connect=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
    ) as client:
        app.state.service = AnalysisService(client, LLMService(client, settings), settings)
        yield


app = FastAPI(
    title="AI-Powered Website Link Analysis API",
    description="Analyzes a website URL with an LLM: competitors, search queries, SEO, services and target audience.",
    version="1.0.0",
    lifespan=lifespan,
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error={"code": code, "message": message}).model_dump())


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return _error(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    return _error(422, "invalid_request", "Request body must be JSON like {\"url\": \"https://example.com\"}.")


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception):
    log.exception("unhandled error")  # details stay in logs, never in the response
    return _error(500, "internal_error", "An unexpected error occurred.")


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


@app.post(
    "/link-analysis",
    response_model=LinkAnalysisResponse,
    responses={
        400: {"model": ErrorResponse, "description": "URL points to a private/internal address (SSRF protection)"},
        422: {"model": ErrorResponse, "description": "Invalid URL, or website unreachable / access denied / no content"},
        502: {"model": ErrorResponse, "description": "LLM provider failure or invalid LLM output"},
        504: {"model": ErrorResponse, "description": "Website timed out"},
    },
    tags=["analysis"],
)
async def link_analysis(body: LinkAnalysisRequest, request: Request) -> LinkAnalysisResponse:
    return await request.app.state.service.analyze(body.url)
