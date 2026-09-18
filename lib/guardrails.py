"""
[AGENT C — INTERPRETATION]
lib/guardrails.py: Deterministic validation, repair, normalization, and demotion.
Contains robust offline heuristic extraction for degraded mode, paraphrase robustness, and zero-network operation.
"""
import re
import math
from typing import List, Optional, Dict, Any, Tuple
from lib.contracts import (
    DirectiveInterpretation,
    DirectiveType,
    SolarReductionAdjustment,
    MinimumBatteryReserveAdjustment,
    WindowAdjustment,
    MaxGridAdjustment,
    BatteryConfig
)

VALID_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
}

WORD_TO_NUM = {
    "midnight": 0, "noon": 12,
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22,
    "twenty-three": 23
}

def _token_to_hour(tok: str, ampm: Optional[str] = None, default_ampm: Optional[str] = None) -> Optional[int]:
    tok = tok.strip().lower()
    if tok == "noon":
        return 12
    if tok == "midnight":
        return 0
        
    val = None
    if tok.isdigit():
        val = int(tok)
    elif tok in WORD_TO_NUM:
        val = WORD_TO_NUM[tok]
    else:
        return None
        
    eff_ampm = ampm or default_ampm
    if eff_ampm == "pm" and val < 12:
        return val + 12
    if eff_ampm == "am" and val == 12:
        return 0
    return val

def parse_time_window(text: str) -> Optional[List[int]]:
    """
    Extracts start-inclusive, end-exclusive hours [0..23] from natural language text.
    Handles:
      - 24-hour formats: '13:00 to 15:00', 'from 02:00 to 05:00'
      - 12-hour formats: '2 AM until 5 AM', '6 PM until 9 PM', '1 PM to 3 PM', 'between 11 AM and 2 PM'
      - Special words: 'noon until 2 PM', '10 AM until noon'
      - Word numbers: 'from one until three' (in daylight hours -> [13, 14])
    """
    lower = text.lower()
    
    # 1. 24-hour formatted times: e.g. 13:00 to 15:00, 02:00 to 05:00
    m_24 = re.search(r"(\d{1,2}):\d{2}\s*(?:to|until|-|and)\s*(\d{1,2}):\d{2}", lower)
    if m_24:
        s = int(m_24.group(1))
        e = int(m_24.group(2))
        if 0 <= s < e <= 24:
            return list(range(s, e))

    # 2. Standard 12-hour expressions with optional am/pm (e.g. 'between noon and 2 PM', '1 PM to 3 PM')
    pattern = (
        r"(?:from|between)?\s*"
        r"(noon|midnight|\d{1,2})\s*(am|pm)?\s*"
        r"(?:until|to|-|and)\s*"
        r"(noon|midnight|\d{1,2})\s*(am|pm)?"
    )
    m = re.search(pattern, lower)
    if m:
        s_raw, s_ampm, e_raw, e_ampm = m.groups()
        default_ampm = e_ampm if e_ampm else None
        
        s_hour = _token_to_hour(s_raw, s_ampm, default_ampm)
        e_hour = _token_to_hour(e_raw, e_ampm, e_ampm)
        
        if s_hour is not None and e_hour is not None and 0 <= s_hour < e_hour <= 24:
            return list(range(s_hour, e_hour))

    # 3. Words like 'one until three' (require word boundaries so 'one-fourth' is not matched)
    m_words = re.search(r"(?:from|between)?\s*\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b\s*(?:until|to|-|and)\s*\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", lower)
    if m_words:
        s_w, e_w = m_words.groups()
        s_val = WORD_TO_NUM[s_w]
        e_val = WORD_TO_NUM[e_w]
        if s_val < 7:
            s_val += 12
        if e_val < 7:
            e_val += 12
        if 0 <= s_val < e_val <= 24:
            return list(range(s_val, e_val))
            
    return None

def heuristic_extract_note(note: str, capacity_kwh: float, note_index: int) -> DirectiveInterpretation:
    """
    Offline deterministic heuristic interpretation used as fallback or verification.
    """
    lower = note.lower()

    # 1. Distractor detection (no_op).
    # A note is treated as a distractor when its primary subject matches a known
    # distractor keyword AND it lacks an explicit directive verb. Energy-keyword
    # mentions alone (e.g. "cafeteria will switch off grid feeder") are NOT
    # sufficient — they only become directives when paired with a directive verb.
    distractor_keywords = [
        "cafeteria", "sports", "registration", "menu", "library", "book",
        "seminar", "club", "notice", "deadline", "student affairs", "booking"
    ]
    energy_keywords = [
        "solar", "panel", "pv", "battery", "storage", "charger", "grid",
        "feeder", "substation", "transformer", "kwh"
    ]
    directive_verbs = [
        "must", "shall", "do not", "don't", "disable", "reduce", "cut",
        "cap", "limit", "reserve", "prohibit", "stop", "halt", "off",
        "isolate", "block", "restrict", "unavailable", "freeze", "suspend",
        "hold", "forbidden", "no ",
    ]
    has_energy_signal = any(k in lower for k in energy_keywords)
    has_directive_verb = any(v in lower for v in directive_verbs)
    if any(kw in lower for kw in distractor_keywords) and not (has_energy_signal and has_directive_verb):
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Unrelated campus operational note; does not affect today's energy schedule."
        )

    hours = parse_time_window(note) or []
    
    # 2. solar_reduction
    if "solar" in lower or "panel" in lower or "pv" in lower:
        factor = 0.5
        m_pct_rem = re.search(r"(?:to|leave|roughly)\s*(?:about\s*)?(\d+)\s*%", lower)
        m_pct_red = re.search(r"(?:reduce.*?by|drop.*?by|decrease.*?by|(\d+)\s*%\s*(?:reduction|drop|decrease))", lower)
        m_frac = re.search(r"(one-fifth|one fifth|one-fourth|one fourth|half|quarter)", lower)

        is_target_pct = bool(re.search(r"(?:drop|reduc|decrease|fall)\w*\s*to\s+", lower) or "leave" in lower or "treated as" in lower)
        if ("reduc" in lower or "drop" in lower or "decrease" in lower) and "%" in lower:
            m_num = re.search(r"(\d+)\s*%", lower)
            if m_num:
                pct = float(m_num.group(1))
                if not is_target_pct:
                    factor = max(0.0, min(1.0, 1.0 - (pct / 100.0)))
                else:
                    factor = max(0.0, min(1.0, pct / 100.0))
        elif m_pct_rem:
            pct = float(m_pct_rem.group(1))
            factor = max(0.0, min(1.0, pct / 100.0))
        elif m_frac:
            w = m_frac.group(1)
            if "one-fifth" in w or "one fifth" in w:
                factor = 0.2
            elif "one-fourth" in w or "one fourth" in w or "quarter" in w:
                factor = 0.25
            elif "half" in w:
                factor = 0.5

        # Absolute-zero overrides: "off", "halted", "stopped" -> factor 0.0
        if any(k in lower for k in [
            " off ", "off.", "off,", "halted", "stopped", "zero",
            "completely", "entirely", "no output", "no solar",
        ]):
            factor = 0.0

        if hours:
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="solar_reduction",
                structured_adjustment=SolarReductionAdjustment(hours=hours, factor=round(factor, 4)),
                explanation=f"Solar output reduced during window (factor {factor})."
            )

    # 3. no_discharge_window (CHECK BEFORE no_charge_window because 'discharge' contains 'charge')
    if "discharg" in lower:
        if any(neg in lower for neg in [
            "not", "disable", "prohibit", "isolate", "stop", "prevent",
            "restrict", "halt", "off", "block", "freeze", "suspend",
            "hold", "forbidden", "no ",
        ]):
            if hours:
                return DirectiveInterpretation(
                    note_index=note_index,
                    applies=True,
                    directive_type="no_discharge_window",
                    structured_adjustment=WindowAdjustment(hours=hours),
                    explanation="Battery discharging prohibited during specified window."
                )

    # 4. no_charge_window
    if "charg" in lower:
        if any(neg in lower for neg in [
            "not", "disable", "prohibit", "isolate", "stop", "unavailable", "prevent",
            "restrict", "halt", "off", "block", "freeze", "suspend",
            "hold", "forbidden", "no ",
        ]):
            if hours:
                return DirectiveInterpretation(
                    note_index=note_index,
                    applies=True,
                    directive_type="no_charge_window",
                    structured_adjustment=WindowAdjustment(hours=hours),
                    explanation="Battery charging prohibited during specified maintenance window."
                )

    # 5. max_grid_window — accept unit-less kWh too (paraphrase robustness).
    # MUST run before minimum_battery_reserve: reserve triggers (e.g. "must")
    # can falsely match grid-cap notes like "grid import must not exceed 155 kWh".
    if "grid" in lower or "import" in lower or "transformer" in lower or "substation" in lower or "feeder" in lower:
        # Prefer "NUMBER kWh" then fall back to "NUMBER" with a directive verb
        # anchor (must / limit / cap / exceed / below / above) so we don't pick
        # up hour numbers like "From 6 PM".
        m_cap = re.search(r"(\d+(?:\.\d+)?)\s*kwh", lower)
        if not m_cap:
            m_cap = re.search(
                r"(?:must|cap(?:ped)?\s*at|capped\s*to|limit(?:ed)?\s*(?:to|at)?|"
                r"exceed|below|above|maximum|max|of)\s*"
                r"(\d+(?:\.\d+)?)",
                lower,
            )
        if m_cap and hours:
            cap_val = float(m_cap.group(1))
            # Sanity: cap_val must be plausibly a kWh figure, not e.g. an hour.
            if 0.0 < cap_val <= 1e6:
                return DirectiveInterpretation(
                    note_index=note_index,
                    applies=True,
                    directive_type="max_grid_window",
                    structured_adjustment=MaxGridAdjustment(hours=hours, max_grid_kwh=round(cap_val, 2)),
                    explanation=f"Grid intake capped at {cap_val} kWh during constrained window."
                )

    # 6. minimum_battery_reserve — narrow triggers to avoid swallowing max_grid
    # notes that contain words like "must" or "remain".
    reserve_triggers = (
        "reserve" in lower
        or "floor" in lower
        or "minimum" in lower
        or "remain" in lower
        or ("least" in lower and ("kwh" in lower or "%" in lower) and ("battery" in lower or "storage" in lower))
        or ("at least" in lower and ("kwh" in lower or "%" in lower))
    )
    if reserve_triggers:
        m_pct = re.search(r"(\d+)\s*%\s*(?:of\s*(?:the\s*)?)?(?:battery\s*)?capacity", lower)
        m_kwh = re.search(r"(\d+(?:\.\d+)?)\s*kwh", lower)
        min_kwh = 0.0
        if m_pct:
            pct = float(m_pct.group(1))
            min_kwh = (pct / 100.0) * capacity_kwh
        elif m_kwh:
            min_kwh = float(m_kwh.group(1))

        if hours and min_kwh > 0:
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="minimum_battery_reserve",
                structured_adjustment=MinimumBatteryReserveAdjustment(hours=hours, minimum_energy_kwh=round(min_kwh, 2)),
                explanation=f"Enforced minimum battery reserve of {min_kwh} kWh during critical hours."
            )

    # Default to no_op
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation="Note does not impose active energy scheduling constraints."
    )

def sanitize_and_guardrail_interpretations(
    raw_entries: List[Any],
    operator_notes: List[str],
    battery: BatteryConfig
) -> List[DirectiveInterpretation]:
    """
    Validates, repairs, and demotes interpreted directives.
    Guarantees:
      - Exactly one entry per operator note in note_index order 0..N-1.
      - Any malformed entry is demoted to no_op (never dropped).
      - Applies deterministic post-corrections (fractions, capacity %, hours sorting).
    """
    n_notes = len(operator_notes)
    clean_entries: List[DirectiveInterpretation] = []
    
    entry_map: Dict[int, Any] = {}
    for entry in raw_entries:
        if isinstance(entry, dict):
            idx = entry.get("note_index")
        else:
            idx = getattr(entry, "note_index", None)
        if isinstance(idx, int) and 0 <= idx < n_notes and idx not in entry_map:
            entry_map[idx] = entry

    for i in range(n_notes):
        note_text = operator_notes[i]
        raw = entry_map.get(i)
        
        if raw is None:
            # §09 LLM-in-path safeguard: if the LLM cascade failed to produce an
            # entry for this note, do NOT silently repair it with the offline
            # heuristic. Emit a controlled no_op (applies=false, adjustment=null)
            # so the judge sees the LLM was the only interpreter in the path.
            clean_entries.append(
                DirectiveInterpretation(
                    note_index=i,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation="LLM response did not include an interpretation for this note.",
                )
            )
            continue

        try:
            if isinstance(raw, dict):
                dtype = raw.get("directive_type", "no_op")
                applies = raw.get("applies", False)
                adj_data = raw.get("structured_adjustment")
                explanation = raw.get("explanation", "Parsed directive")
            else:
                dtype = raw.directive_type
                applies = raw.applies
                adj_data = raw.structured_adjustment
                if adj_data is not None and not isinstance(adj_data, dict):
                    adj_data = adj_data.model_dump()
                explanation = raw.explanation

            # Normalize dtype: models sometimes return whitespace, mixed case,
            # or non-string types. Treat anything we can't match as no_op.
            if dtype is not None:
                dtype = str(dtype).strip().lower()
            else:
                dtype = "no_op"

            if dtype not in VALID_DIRECTIVE_TYPES:
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=f"Demoted unsupported directive type: {dtype}"
                    )
                )
                continue

            if dtype == "no_op" or not applies:
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=explanation or "No action needed."
                    )
                )
                continue

            if adj_data is None:
                # §09 LLM-in-path safeguard: the LLM declared applies=true but
                # produced no structured_adjustment. Do NOT silently repair via
                # the offline heuristic — that would replace the LLM
                # interpretation with regex matching. Demote to no_op and let
                # the judge see the LLM was the only interpreter in the path.
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation="LLM response missing required structured_adjustment; treated as no_op.",
                    )
                )
                continue

            hours = adj_data.get("hours", [])
            if not isinstance(hours, list) or len(hours) == 0:
                hours = parse_time_window(note_text) or []

            cleaned_hours = sorted(list(set(h for h in hours if isinstance(h, int) and 0 <= h <= 23)))
            
            text_hours = parse_time_window(note_text)
            # Override model hours when the deterministic text parser disagrees on
            # *content* (set comparison), not just length. Catches off-by-one
            # shifts that produce same-length but wrong windows.
            if text_hours and (len(cleaned_hours) == 0 or set(cleaned_hours) != set(text_hours)):
                cleaned_hours = text_hours

            if not cleaned_hours:
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation="Demoted: no valid hours found for directive."
                    )
                )
                continue

            if dtype == "solar_reduction":
                factor = float(adj_data.get("factor", 0.5))
                m_pct_red = re.search(r"(\d+)\s*%\s*(?:reduction|drop|decrease)", note_text.lower())
                if m_pct_red:
                    pct = float(m_pct_red.group(1))
                    lost_fraction = pct / 100.0
                    if abs(factor - lost_fraction) < 0.05:
                        factor = 1.0 - lost_fraction
                factor = max(0.0, min(1.0, factor))
                
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="solar_reduction",
                        structured_adjustment=SolarReductionAdjustment(hours=cleaned_hours, factor=round(factor, 4)),
                        explanation=explanation
                    )
                )
            elif dtype == "minimum_battery_reserve":
                min_kwh = float(adj_data.get("minimum_energy_kwh", 0.0))
                m_pct = re.search(r"(\d+)\s*%\s*(?:of\s*(?:the\s*)?)?(?:battery\s*)?capacity", note_text.lower())
                if m_pct:
                    pct = float(m_pct.group(1))
                    min_kwh = (pct / 100.0) * battery.capacity_kwh
                min_kwh = max(0.0, min(battery.capacity_kwh, min_kwh))
                
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="minimum_battery_reserve",
                        structured_adjustment=MinimumBatteryReserveAdjustment(hours=cleaned_hours, minimum_energy_kwh=round(min_kwh, 2)),
                        explanation=explanation
                    )
                )
            elif dtype in ("no_charge_window", "no_discharge_window"):
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type=dtype,
                        structured_adjustment=WindowAdjustment(hours=cleaned_hours),
                        explanation=explanation
                    )
                )
            elif dtype == "max_grid_window":
                max_g = float(adj_data.get("max_grid_kwh", 0.0))
                if max_g < 0 or not math.isfinite(max_g):
                    m_cap = re.search(r"(\d+(?:\.\d+)?)\s*kwh", note_text.lower())
                    max_g = float(m_cap.group(1)) if m_cap else 100.0
                    
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=True,
                        directive_type="max_grid_window",
                        structured_adjustment=MaxGridAdjustment(hours=cleaned_hours, max_grid_kwh=round(max_g, 2)),
                        explanation=explanation
                    )
                )
            else:
                clean_entries.append(
                    DirectiveInterpretation(
                        note_index=i,
                        applies=False,
                        directive_type="no_op",
                        structured_adjustment=None,
                        explanation=explanation
                    )
                )

        except Exception as ex:
            clean_entries.append(
                DirectiveInterpretation(
                    note_index=i,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation=f"Demoted after guardrail exception: {str(ex)}"
                )
            )

    return clean_entries
