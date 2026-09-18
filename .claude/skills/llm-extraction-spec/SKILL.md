---
name: llm-extraction-spec
description: Use when implementing or tuning operator-note interpretation,
  prompts, structured output, or guardrails.
---
ONE model call per request covering all notes. temperature=0. Use provider
schema-constrained JSON (Gemini response_schema / OpenAI-compatible
json_schema on Groq). Primary Gemini 2.x Flash, secondary Groq, then
guardrail-only degraded mode.

Schema field order MUST put "explanation" before directive_type/hours/values
so the model reasons before committing. Reorder on serialization.

Always inject the battery object into the prompt; percentage reserves are
unanswerable without capacity_kwh.

Guardrails run on EVERY entry and never drop an entry. A failing entry is
DEMOTED to {applies:false, directive_type:"no_op", structured_adjustment:null}
because a missing entry is a schema failure worth more than a wrong directive.
Checks: index coverage 0..N-1 exactly once; type in enum; applies/no_op
consistency; hours unique ints 0-23 sorted; numeric ranges per I7.

Deterministic post-correction (guardrail layer, applied AFTER the model, as
a correction not a replacement):
- note matches /(\d+)\s*%\s*(reduction|drop|decrease)/ and returned factor
  is approximately pct/100 -> replace with 1 - pct/100
- note matches /(\d+)\s*%\s*(of|the)?\s*(battery|capacity)/ for a reserve ->
  recompute minimum_energy_kwh = pct/100 * capacity_kwh
- if hours span length != (end_hour - start_hour) parsed from the note,
  prefer the arithmetic span (end-exclusive)

Cache key: sha256(note_text + "|" + str(capacity_kwh)). Cache is per-note,
not per-request, so paraphrase drills and repeats are free.

Never let model output reach the optimizer unvalidated. Never invent a
directive type. On total provider failure, return all notes as no_op and
still produce a valid optimized plan.
