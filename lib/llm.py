"""
[AGENT C — INTERPRETATION]
lib/llm.py: Multi-provider LLM caller with JSON schema enforcement,
cache integration, timeouts, retry, and deterministic guardrail demotion.
"""
import os
import json
import time
from typing import List, Optional
import requests

from lib.contracts import (
    DirectiveInterpretation,
    BatteryConfig
)
from lib.prompt import SYSTEM_PROMPT, build_user_prompt
from lib.guardrails import (
    sanitize_and_guardrail_interpretations,
)
from lib.cache import (
    get_cached_interpretation,
    set_cached_interpretation
)

def _call_gemini_api(system: str, user: str, api_key: str) -> Optional[dict]:
    """Call Google Gemini via REST, with automatic model fallback if one encounters quota limit."""
    models_to_try = [
        os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite-preview"
    ]
    # Deduplicate while preserving order
    seen = set()
    models = [m for m in models_to_try if not (m in seen or seen.add(m))]

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0
        }
    }
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                cand = data.get("candidates", [])[0]
                text = cand.get("content", {}).get("parts", [])[0].get("text", "")
                return json.loads(text)
        except Exception:
            continue
    return None

def _call_groq_api(system: str, user: str, api_key: str) -> Optional[dict]:
    """Call Groq API (llama-3.3-70b-versatile or llama-3.1-8b-instant)."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=12)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception:
        pass
    return None

def _call_openai_api(system: str, user: str, api_key: str) -> Optional[dict]:
    """Call OpenAI API (gpt-4o-mini)."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=12)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception:
        pass
    return None

def interpret_operator_notes(
    operator_notes: List[str],
    battery: BatteryConfig
) -> List[DirectiveInterpretation]:
    """
    Interprets operator notes through LLM, cache, and deterministic guardrails.
    Always returns a full list of valid DirectiveInterpretation items.
    """
    n_notes = len(operator_notes)
    
    # 1. Check cache for all notes
    cached_results = []
    all_cached = True
    for idx, note in enumerate(operator_notes):
        cached = get_cached_interpretation(note, battery.capacity_kwh, idx)
        if cached is not None:
            cached_results.append(cached)
        else:
            all_cached = False
            break
            
    if all_cached and len(cached_results) == n_notes:
        return cached_results

    # 2. If OFFLINE_FALLBACK is set or no keys, run deterministic heuristic
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    raw_output = None
    user_prompt = build_user_prompt(operator_notes, battery)

    # OFFLINE_FALLBACK env var (documented in README) forces keyless execution.
    # This is the degraded path; the LLM is the primary interpreter.
    offline_fallback = os.environ.get("OFFLINE_FALLBACK") == "1"

    # Try providers in priority order. Skip if OFFLINE_FALLBACK=1.
    if not offline_fallback and gemini_key:
        raw_output = _call_gemini_api(SYSTEM_PROMPT, user_prompt, gemini_key)
    if not offline_fallback and raw_output is None and groq_key:
        raw_output = _call_groq_api(SYSTEM_PROMPT, user_prompt, groq_key)
    if not offline_fallback and raw_output is None and openai_key:
        raw_output = _call_openai_api(SYSTEM_PROMPT, user_prompt, openai_key)

    # Extract raw list
    raw_entries = []
    if isinstance(raw_output, dict):
        if "directives" in raw_output and isinstance(raw_output["directives"], list):
            raw_entries = raw_output["directives"]
        elif "directive_interpretation" in raw_output and isinstance(raw_output["directive_interpretation"], list):
            raw_entries = raw_output["directive_interpretation"]
        elif isinstance(raw_output, list):
            raw_entries = raw_output

    # 3. Apply deterministic guardrails, normalization, and demotion
    clean_interpretations = sanitize_and_guardrail_interpretations(raw_entries, operator_notes, battery)

    # 4. Save clean interpretations to memo cache
    for interp in clean_interpretations:
        note_text = operator_notes[interp.note_index]
        set_cached_interpretation(note_text, battery.capacity_kwh, interp)

    return clean_interpretations
