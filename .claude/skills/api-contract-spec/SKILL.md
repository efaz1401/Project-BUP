---
name: api-contract-spec
description: Use when writing FastAPI routes, models, error handlers,
  or anything touching the request/response shape.
---
Pydantic v2 models mirroring the spec exactly; extra="ignore" on input,
exclude_none=False on output. Echo scenario_id verbatim.

Override RequestValidationError to return 400 (FastAPI defaults to 422;
422 is only acceptable for well-formed-but-semantically-invalid input).
Global exception handler returns 500 with {"error":"internal error"} and
nothing else — no message, no trace, no key fragments.

/health does zero I/O: no model call, no network, no disk. Returns
{"status":"ok"} always, even with no env vars set.

Startup hook: solve a dummy 24-hour LP to warm HiGHS/CBC and import paths so the
first real request does not pay initialization cost.

Log request-scoped: scenario_id, note count, llm provider used, fallback
tier, solve ms, total ms. Never log prompts containing keys or raw responses
in production mode.
