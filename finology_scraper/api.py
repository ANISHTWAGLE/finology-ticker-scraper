"""HTTP adapter. Run with python -m uvicorn finology_scraper.api:app."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
import logging
import math
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Body, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .models import Issue, ScrapeRequest, ScrapeResponse
from .service import failed_response, response_http_status, scrape_company
from .retrieval import BrowserRetriever


log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.scrape_lock = asyncio.Lock()
    app.state.browser_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finology-browser")
    app.state.retriever = BrowserRetriever()
    try:
        yield
    finally:
        try:
            await asyncio.get_running_loop().run_in_executor(
                app.state.browser_worker, app.state.retriever.close)
        finally:
            app.state.browser_worker.shutdown(wait=True)


app = FastAPI(title="Finology Financial Data Scraper", version="1.0.0", lifespan=lifespan,
              description="Get shareholding, quarterly results, profit and loss, balance sheet and cash flow. "
              "Open POST /scrape below, select an example, click Try it out, edit the company and click Execute. "
              "Only company and reporting_basis are required.")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # The JSON decoder accepts NaN/Infinity and can overflow large exponents.
    # Keep invalid inputs visible without putting non-finite numbers in JSON.
    detail = jsonable_encoder(exc.errors(), custom_encoder={
        float: lambda number: number if math.isfinite(number) else str(number),
    })
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health", summary="Check whether the API is running",
         description="Checks the API process only. It does not check Finology availability.")
async def health():
    return {"status": "ok"}


@app.post("/scrape", response_model=ScrapeResponse, summary="Get a company's financial data",
          description="Enter a company-page identifier such as TCS, or paste its Finology URL. "
          "Choose standalone (company only) or consolidated (company and subsidiaries). "
          "All available public rows and periods are returned. "
          "HTTP 200 means complete, possibly with warnings; HTTP 207 means partial: "
          "check sections for missing data and explanations. For HTTP 422, correct the request. "
          "For HTTP 503, wait for the active scrape to finish before retrying.", responses={
    207: {"model": ScrapeResponse, "description": "Partial extraction; inspect section statuses."},
    404: {"model": ScrapeResponse}, 502: {"model": ScrapeResponse}, 504: {"model": ScrapeResponse},
    503: {"model": ScrapeResponse, "description": "Another scrape is already running."},
    422: {"description": "Invalid request fields or invalid company target.", "content": {
        "application/json": {"schema": {"anyOf": [
            {"$ref": "#/components/schemas/ScrapeResponse"},
            {"type": "object", "required": ["detail"], "properties": {
                "detail": {"type": "array", "items": {"type": "object"}}}},
        ]}}}},
})
async def scrape(body: Annotated[ScrapeRequest, Body(openapi_examples={
    "standalone": {"summary": "Company only (standalone)",
                   "value": {"company": "TCS", "reporting_basis": "standalone"}},
    "consolidated": {"summary": "Company and subsidiaries (consolidated)",
                     "value": {"company": "TCS", "reporting_basis": "consolidated"}},
    "company_url": {"summary": "Paste a Finology company URL",
                    "value": {"company": "https://ticker.finology.in/company/TCS",
                              "reporting_basis": "standalone"}},
})], request: Request):
    lock = request.app.state.scrape_lock
    if lock.locked():
        result = failed_response(body, body.company, "", Issue(code="service_busy",
                                 message="Another scrape is running. Try again after it finishes."))
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"), headers={"Retry-After": "5"})
    async with lock:
        evidence_root = os.environ.get("FINOLOGY_EVIDENCE_DIR")
        evidence = Path(evidence_root) / uuid4().hex if evidence_root else None
        try:
            future = asyncio.get_running_loop().run_in_executor(
                request.app.state.browser_worker,
                partial(scrape_company, body, fetcher=request.app.state.retriever.fetch,
                        evidence_dir=evidence))
            try:
                result = await asyncio.shield(future)
            except asyncio.CancelledError:
                # Finish browser work before releasing the single-request slot.
                await asyncio.shield(future)
                raise
        except Exception:
            log.exception("Unexpected scrape failure")
            result = failed_response(body, body.company, "", Issue(code="internal_error",
                                     message="An unexpected extraction error occurred; no complete result is reported."))
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return JSONResponse(status_code=response_http_status(result), content=result.model_dump(mode="json"))
