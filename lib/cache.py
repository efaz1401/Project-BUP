"""
[AGENT C — INTERPRETATION]
lib/cache.py: In-memory SHA-256 memo cache for directive interpretations.
Key is sha256(note_text.strip().lower() + "|" + str(round(capacity_kwh, 2))).
Per-note cache ensures repeated notes and paraphrase drills resolve in <1ms.
"""
import hashlib
from typing import Optional, Dict
from lib.contracts import DirectiveInterpretation

_MEMO_CACHE: Dict[str, DirectiveInterpretation] = {}

def get_cache_key(note_text: str, capacity_kwh: float) -> str:
    norm = note_text.strip().lower()
    raw = f"{norm}|{round(capacity_kwh, 2)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def get_cached_interpretation(note_text: str, capacity_kwh: float, note_index: int) -> Optional[DirectiveInterpretation]:
    key = get_cache_key(note_text, capacity_kwh)
    cached = _MEMO_CACHE.get(key)
    if cached is not None:
        # Return a copy with the current note_index
        data = cached.model_dump()
        data["note_index"] = note_index
        return DirectiveInterpretation.model_validate(data)
    return None

def set_cached_interpretation(note_text: str, capacity_kwh: float, interp: DirectiveInterpretation) -> None:
    key = get_cache_key(note_text, capacity_kwh)
    _MEMO_CACHE[key] = interp
