"""
[AGENT B — OPTIMIZER & PRIVATE JUDGE]
lib/fallback.py: Trivial always-valid 24-hour plan.
Holds the battery idle every hour, uses solar up to demand, buys rest from grid.
Mathematically guaranteed to satisfy neutrality, battery bounds, and hourly balance.
"""
from typing import List, Optional
from lib.contracts import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    DirectiveInterpretation,
    HourlyPlanEntry
)

def trivial_plan(
    request: OptimizeEnergyRequest,
    directives: Optional[List[DirectiveInterpretation]] = None,
    summary_prefix: str = "Trivial baseline dispatch"
) -> OptimizeEnergyResponse:
    """
    Generates an always-valid fallback plan where the battery remains idle.
    """
    if directives is None:
        directives = [
            DirectiveInterpretation(
                note_index=i,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation="Degraded fallback: note ignored to maintain schedule validity."
            )
            for i in range(len(request.operator_notes))
        ]

    # Effective solar accounting
    eff_solar = [h.solar_kwh for h in request.hours]
    for d in directives:
        if d.applies and d.directive_type == "solar_reduction" and d.structured_adjustment:
            adj = d.structured_adjustment
            hours = getattr(adj, "hours", [])
            factor = getattr(adj, "factor", 1.0)
            for h in hours:
                if 0 <= h < 24:
                    eff_solar[h] *= factor

    plan: List[HourlyPlanEntry] = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    init_e = request.battery.initial_energy_kwh

    for h in range(24):
        demand = request.hours[h].demand_kwh
        tariff = request.hours[h].tariff_bdt_per_kwh
        solar_avail = eff_solar[h]
        
        solar_used = min(solar_avail, demand)
        grid = max(0.0, demand - solar_used)
        
        total_grid += grid
        total_cost += grid * tariff
        if grid > peak_grid:
            peak_grid = grid
            
        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(grid, 4),
                solar_used_kwh=round(solar_used, 4),
                battery_action="idle",
                battery_kwh=0.0,
                battery_energy_after_kwh=round(init_e, 4)
            )
        )

    return OptimizeEnergyResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        total_grid_kwh=round(total_grid, 4),
        total_cost_bdt=round(total_cost, 4),
        peak_grid_kwh=round(peak_grid, 4),
        plan_summary=f"{summary_prefix}: Battery held idle, demand served by solar and grid; end-of-day battery neutrality preserved."
    )
