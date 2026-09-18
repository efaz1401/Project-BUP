---
name: deploy-runbook
description: Use for containerization, registry push, hosting, and README.
---
Dockerfile: python:3.12-slim, pip install --no-cache-dir, non-root user,
EXPOSE the documented port, CMD uvicorn app.main:app --host 0.0.0.0
--port ${PORT:-8080}. No secrets, no .env, no keys in any layer.

Build multi-arch or the judge on Apple Silicon silently fails:
  docker buildx build --platform linux/amd64,linux/arm64 \
    -t ghcr.io/<user>/gridwise:<tag> --push .
Make the GHCR package public. Record the digest.

Deploy the SAME image. Primary: Cloud Run with --min-instances=1
--allow-unauthenticated. Equal primary: Fly.io with auto_stop_machines=false.
No-card backup: Hugging Face Spaces, Docker SDK, MUST listen on 7860.
Last resort: Render free, but it sleeps in 15 min with ~50s cold start —
only with a 5-minute external pinger. Point a pinger at /health regardless.

Document env var NAMES only, never values. Ship OFFLINE_FALLBACK=1 so a
keyless judge still gets a valid response, and state plainly in the README
that this is a degraded path and the LLM is the primary interpreter.

README sections, in order: what it is; architecture (note -> LLM ->
guardrails -> LP -> replay -> response); quickstart from clean clone; env var
names; model/provider; LLM role; guardrails; solver; exact run command;
/health curl; public-sample curl with expected output; docker pull/run;
dependencies and credits; known limitations; secret handling.
