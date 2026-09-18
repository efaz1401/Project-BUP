"""
[AGENT A — SPINE & SHIP]
app/main.py: FastAPI entrypoint for GridWise Preliminary Challenge.
Exposes GET /health and POST /optimize-energy.
Includes startup solver warmup, custom 400 handler, and secure 500 exception handler.
"""
import time
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from lib.contracts import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    HealthResponse,
    ErrorResponse,
    HourInput,
    BatteryConfig
)
from lib.llm import interpret_operator_notes
from lib.optimizer import solve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("gridwise")

def warmup_solver():
    """Warms up the HiGHS LP solver during startup to eliminate cold-start latency."""
    try:
        dummy_req = OptimizeEnergyRequest(
            scenario_id="WARMUP",
            operator_notes=["The cafeteria menu changes tomorrow."],
            hours=[
                HourInput(hour=h, demand_kwh=100.0, solar_kwh=20.0, tariff_bdt_per_kwh=10.0)
                for h in range(24)
            ],
            battery=BatteryConfig(
                capacity_kwh=200.0,
                initial_energy_kwh=100.0,
                minimum_energy_kwh=20.0,
                max_charge_kwh_per_hour=50.0,
                max_discharge_kwh_per_hour=50.0
            )
        )
        directives = interpret_operator_notes(dummy_req.operator_notes, dummy_req.battery)
        solve(dummy_req, directives)
        logger.info("LP solver and import warmup completed successfully.")
    except Exception as ex:
        logger.warning(f"Warmup skipped or encountered non-fatal error: {ex}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    warmup_solver()
    yield

app = FastAPI(
    title="GridWise Campus Energy Optimization Service",
    description="LLM-Assisted Operator Directive Interpretation and 24-hour Energy Dispatch Optimization",
    version="1.0.0",
    lifespan=lifespan
)

# --- Exception Handlers ---

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Override default 422 to return HTTP 400 for malformed/invalid JSON requests
    as strictly required by Problem Statement Section 06.
    """
    logger.warning(f"Request validation error on {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Malformed or structurally invalid request payload"}
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return JSONResponse(status_code=404, content={"error": "Endpoint not found"})
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global catch-all returning controlled 500 without leaking stack traces,
    tokens, keys, or internal exceptions.
    """
    logger.error("Unhandled internal server exception", exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal error"}
    )

# --- Endpoints ---

@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check readiness endpoint"
)
async def get_health():
    """
    Readiness endpoint: Zero I/O, zero network, always returns HTTP 200 {'status': 'ok'}.
    """
    return HealthResponse(status="ok")

@app.post(
    "/optimize-energy",
    response_model=OptimizeEnergyResponse,
    status_code=status.HTTP_200_OK,
    summary="Main LLM interpretation and energy optimization endpoint"
)
async def post_optimize_energy(request: OptimizeEnergyRequest):
    """
    Main endpoint:
    1. Extracts and guardrails directives from operator notes via LLM / heuristic cache.
    2. Builds and solves 24-hour optimal dispatch using HiGHS LP.
    3. Replays and self-validates plan invariants before returning.
    """
    t_start = time.perf_counter()
    
    # 1. Interpret notes
    t0 = time.perf_counter()
    directives = interpret_operator_notes(request.operator_notes, request.battery)
    t_interp = (time.perf_counter() - t0) * 1000.0

    # 2. Solve 24-hour dispatch
    t1 = time.perf_counter()
    response = solve(request, directives)
    t_solve = (time.perf_counter() - t1) * 1000.0

    total_ms = (time.perf_counter() - t_start) * 1000.0
    logger.info(
        f"scenario_id={request.scenario_id} notes={len(request.operator_notes)} "
        f"interp_ms={t_interp:.1f} solve_ms={t_solve:.1f} total_ms={total_ms:.1f}"
    )

    return response
