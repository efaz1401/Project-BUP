"""
[FROZEN CONTRACT] — DO NOT MODIFY DIRECTLY.
Canonical Pydantic v2 schemas for GridWise Preliminary Challenge.
Follows BUP CSE Fest 2026 Preliminary Problem Statement exactly.
"""
from typing import List, Optional, Literal, Union, Annotated
from pydantic import BaseModel, Field, ConfigDict

# --- Request Models ---

class HourInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0..23)")
    demand_kwh: float = Field(..., ge=0.0, description="Demand in kWh")
    solar_kwh: float = Field(..., ge=0.0, description="Available solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., description="Grid electricity price for this hour in BDT/kWh")

class BatteryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capacity_kwh: float = Field(..., gt=0.0, description="Maximum storage capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Battery energy at start of hour 0 in kWh")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Base minimum reserve in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Max charging rate in kWh/h")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Max discharging rate in kWh/h")

class OptimizeEnergyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scenario_id: str = Field(..., description="Scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 operator notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly profiles")
    battery: BatteryConfig = Field(..., description="Battery parameters")


# --- Directive Interpretation Models ---

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op"
]

class SolarReductionAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: List[int] = Field(..., description="Unique ascending hours [0..23]")
    factor: float = Field(..., ge=0.0, le=1.0, description="Usable solar fraction remaining (0..1)")

class MinimumBatteryReserveAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: List[int] = Field(..., description="Unique ascending hours [0..23]")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Minimum battery reserve in kWh")

class WindowAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: List[int] = Field(..., description="Unique ascending hours [0..23]")

class MaxGridAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: List[int] = Field(..., description="Unique ascending hours [0..23]")
    max_grid_kwh: float = Field(..., ge=0.0, description="Max grid electricity import in kWh")

StructuredAdjustment = Optional[
    Union[
        SolarReductionAdjustment,
        MinimumBatteryReserveAdjustment,
        WindowAdjustment,
        MaxGridAdjustment
    ]
]

class DirectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_index: int = Field(..., ge=0, description="0-based index matching operator_notes")
    applies: bool = Field(..., description="true for active directives, false only for no_op")
    directive_type: DirectiveType = Field(..., description="One of the 6 canonical directive types")
    structured_adjustment: StructuredAdjustment = Field(None, description="Directive parameters or null for no_op")
    explanation: str = Field(..., description="Concise explanation of the interpretation")


# --- Hourly Plan & Response Models ---

BatteryAction = Literal["charge", "discharge", "idle"]

class HourlyPlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(..., ge=0, le=23, description="Hour index 0..23")
    grid_kwh: float = Field(..., ge=0.0, description="Electricity purchased from grid in kWh")
    solar_used_kwh: float = Field(..., ge=0.0, description="Solar electricity used in kWh")
    battery_action: BatteryAction = Field(..., description="charge, discharge, or idle")
    battery_kwh: float = Field(..., ge=0.0, description="Battery energy exchanged (0 if idle)")
    battery_energy_after_kwh: float = Field(..., ge=0.0, description="Battery state of charge at end of hour")

class OptimizeEnergyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(..., description="Echoed scenario_id from request")
    directive_interpretation: List[DirectiveInterpretation] = Field(..., description="Ordered list of interpretations")
    hourly_plan: List[HourlyPlanEntry] = Field(..., min_length=24, max_length=24, description="24-hour schedule")
    total_grid_kwh: float = Field(..., ge=0.0, description="Recalculated sum of grid_kwh")
    total_cost_bdt: float = Field(..., description="Recalculated sum of grid cost in BDT")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Maximum hourly grid_kwh")
    plan_summary: str = Field(..., description="Brief summary of plan strategy")

class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"

class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: str = Field(..., description="Error message")
