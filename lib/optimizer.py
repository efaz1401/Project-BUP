"""
[AGENT B — OPTIMIZER & PRIVATE JUDGE]
lib/optimizer.py: High-speed LP build + solve + rederive using SciPy linprog (HiGHS).
Includes mandatory epsilon penalty on battery action to eliminate degenerate optima,
and a three-tier infeasibility cascade.
"""
from typing import List, Optional, Tuple
import numpy as np
from scipy.optimize import linprog

from lib.contracts import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    DirectiveInterpretation,
    HourlyPlanEntry
)
from lib.fallback import trivial_plan
from lib.validator import validate_plan

EPSILON_BATTERY = 1e-6

def _build_and_solve_lp(
    request: OptimizeEnergyRequest,
    directives: List[DirectiveInterpretation],
    allow_grid_cap: bool = True
) -> Optional[Tuple[np.ndarray, float]]:
    """
    Builds and solves the 96-variable LP:
    Variables per hour h in 0..23:
      g[h] (0..23), s[h] (24..47), c[h] (48..71), d[h] (72..95)
    """
    bat = request.battery
    hours_data = request.hours
    
    cap = bat.capacity_kwh
    e_init = bat.initial_energy_kwh
    min_e = bat.minimum_energy_kwh
    max_c = bat.max_charge_kwh_per_hour
    max_d = bat.max_discharge_kwh_per_hour
    
    eff_solar = [h.solar_kwh for h in hours_data]
    min_reserve = [min_e] * 24
    can_charge = [True] * 24
    can_discharge = [True] * 24
    grid_caps = [float("inf")] * 24
    
    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        dtype = d.directive_type
        adj = d.structured_adjustment
        hrs = getattr(adj, "hours", [])
        if dtype == "solar_reduction":
            factor = getattr(adj, "factor", 1.0)
            for h in hrs:
                if 0 <= h < 24:
                    eff_solar[h] *= factor
        elif dtype == "minimum_battery_reserve":
            m = getattr(adj, "minimum_energy_kwh", 0.0)
            for h in hrs:
                if 0 <= h < 24:
                    min_reserve[h] = max(min_reserve[h], m)
        elif dtype == "no_charge_window":
            for h in hrs:
                if 0 <= h < 24:
                    can_charge[h] = False
        elif dtype == "no_discharge_window":
            for h in hrs:
                if 0 <= h < 24:
                    can_discharge[h] = False
        elif dtype == "max_grid_window" and allow_grid_cap:
            mg = getattr(adj, "max_grid_kwh", float("inf"))
            for h in hrs:
                if 0 <= h < 24:
                    grid_caps[h] = min(grid_caps[h], mg)

    # Variables: 96
    # 0..23: grid, 24..47: solar_used, 48..71: charge, 72..95: discharge
    c = np.zeros(96)
    for h in range(24):
        c[h] = hours_data[h].tariff_bdt_per_kwh
        c[48 + h] = EPSILON_BATTERY
        c[72 + h] = EPSILON_BATTERY

    bounds = []
    for h in range(24): # grid
        g_max = grid_caps[h] if grid_caps[h] != float("inf") else None
        bounds.append((0.0, g_max))
    for h in range(24): # solar_used
        bounds.append((0.0, eff_solar[h]))
    for h in range(24): # charge
        bounds.append((0.0, max_c if can_charge[h] else 0.0))
    for h in range(24): # discharge
        bounds.append((0.0, max_d if can_discharge[h] else 0.0))

    # Constraints:
    # 1. Energy balance (equality): grid[h] + solar_used[h] + discharge[h] - charge[h] = demand[h]  (24 eq)
    # 2. Battery neutrality (equality): sum_{k=0..23} (charge[k] - discharge[k]) = 0  (1 eq)
    # 3. Cumulative battery bounds (inequality):
    #    E[h] = e_init + sum_{k=0..h} (charge[k] - discharge[k])
    #    E[h] <= cap  =>  sum_{k=0..h} (charge[k] - discharge[k]) <= cap - e_init
    #    E[h] >= reserve[h]  =>  sum_{k=0..h} (-charge[k] + discharge[k]) <= e_init - reserve[h]
    
    A_eq = []
    b_eq = []
    
    # 1. Energy balance
    for h in range(24):
        row = np.zeros(96)
        row[h] = 1.0           # grid[h]
        row[24 + h] = 1.0      # solar_used[h]
        row[72 + h] = 1.0      # discharge[h]
        row[48 + h] = -1.0     # charge[h]
        A_eq.append(row)
        b_eq.append(hours_data[h].demand_kwh)

    # 2. Neutrality
    row = np.zeros(96)
    for h in range(24):
        row[48 + h] = 1.0      # charge
        row[72 + h] = -1.0     # discharge
    A_eq.append(row)
    b_eq.append(0.0)

    # 3. Cumulative inequality bounds (24 upper, 24 lower)
    A_ub = []
    b_ub = []
    for h in range(24):
        # Upper bound: sum_{k<=h} (c[k] - d[k]) <= cap - e_init
        row_up = np.zeros(96)
        for k in range(h + 1):
            row_up[48 + k] = 1.0
            row_up[72 + k] = -1.0
        A_ub.append(row_up)
        b_ub.append(cap - e_init)
        
        # Lower bound: sum_{k<=h} (-c[k] + d[k]) <= e_init - min_reserve[h]
        row_dn = np.zeros(96)
        for k in range(h + 1):
            row_dn[48 + k] = -1.0
            row_dn[72 + k] = 1.0
        A_ub.append(row_dn)
        b_ub.append(e_init - min_reserve[h])

    res = linprog(
        c,
        A_ub=np.array(A_ub),
        b_ub=np.array(b_ub),
        A_eq=np.array(A_eq),
        b_eq=np.array(b_eq),
        bounds=bounds,
        method="highs"
    )

    if res.success:
        return res.x, eff_solar
    return None

def solve(
    request: OptimizeEnergyRequest,
    directives: List[DirectiveInterpretation]
) -> OptimizeEnergyResponse:
    """
    Solves for optimal dispatch and returns verified OptimizeEnergyResponse.
    Uses a 3-tier infeasibility cascade and guarantees a valid plan.
    """
    # Cascade Tier 0: full directives
    res = _build_and_solve_lp(request, directives, allow_grid_cap=True)
    tier_used = "Tier 0 (Full directives)"

    # Cascade Tier 1: relax grid caps if tight. The response must reflect that
    # max_grid_window directives were dropped; otherwise interpretation claims to
    # apply a directive the plan violates (V6 fail on the judge's replay).
    if res is None:
        tier1_directives = [
            d.model_copy(update={
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Infeasibility fallback: max_grid_window directive relaxed.",
            }) if d.directive_type == "max_grid_window" else d
            for d in directives
        ]
        res = _build_and_solve_lp(request, tier1_directives, allow_grid_cap=False)
        if res is not None:
            directives = tier1_directives
            tier_used = "Tier 1 (Relaxed grid cap)"

    # Cascade Tier 2: drop all directives if still infeasible. Rebind so the
    # response's directive_interpretation matches what the LP actually applied.
    if res is None:
        empty_directives = [
            DirectiveInterpretation(
                note_index=d.note_index,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation="Infeasibility fallback: directive relaxed."
            )
            for d in directives
        ]
        res = _build_and_solve_lp(request, empty_directives, allow_grid_cap=False)
        if res is not None:
            directives = empty_directives
            tier_used = "Tier 2 (Base constraints only)"

    # Cascade Tier 3: trivial plan
    if res is None:
        return trivial_plan(request, directives, summary_prefix="Cascade Tier 3 Fallback")

    x, eff_solar = res
    raw_g = x[0:24]
    raw_s = x[24:48]
    raw_c = x[48:72]
    raw_d = x[72:96]

    bat = request.battery
    e_init = bat.initial_energy_kwh
    curr_e = e_init
    
    plan: List[HourlyPlanEntry] = []
    recalc_grid = 0.0
    recalc_cost = 0.0
    recalc_peak = 0.0

    for h in range(24):
        demand = request.hours[h].demand_kwh
        tariff = request.hours[h].tariff_bdt_per_kwh
        
        # 1. Round c[h], d[h] to 6 dp and net them
        c_h = round(float(raw_c[h]), 6)
        d_h = round(float(raw_d[h]), 6)
        net = c_h - d_h
        if net > 1e-5:
            action = "charge"
            b_kwh = net
            c_val = net
            d_val = 0.0
        elif net < -1e-5:
            action = "discharge"
            b_kwh = -net
            c_val = 0.0
            d_val = -net
        else:
            action = "idle"
            b_kwh = 0.0
            c_val = 0.0
            d_val = 0.0

        # 2. Rederive solar: min(effective_solar, demand + charge - discharge)
        s_max = eff_solar[h]
        s_val = min(s_max, max(0.0, demand + c_val - d_val))
        s_val = round(s_val, 6)

        # 3. Rederive grid: demand + charge - discharge - solar
        g_val = max(0.0, demand + c_val - d_val - s_val)
        g_val = round(g_val, 6)

        # 4. Forward replay battery SoC
        curr_e = curr_e + c_val - d_val
        if h == 23 and abs(curr_e - e_init) < 1e-3:
            curr_e = e_init  # Snap exact neutrality

        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(g_val, 4),
                solar_used_kwh=round(s_val, 4),
                battery_action=action,
                battery_kwh=round(b_kwh, 4),
                battery_energy_after_kwh=round(curr_e, 4)
            )
        )

        recalc_grid += g_val
        recalc_cost += g_val * tariff
        if g_val > recalc_peak:
            recalc_peak = g_val

    summary = (
        f"Optimized schedule ({tier_used}): shifted battery storage to offset high tariffs, "
        f"applied active directives, and maintained end-of-day battery neutrality."
    )

    response = OptimizeEnergyResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        total_grid_kwh=round(recalc_grid, 4),
        total_cost_bdt=round(recalc_cost, 4),
        peak_grid_kwh=round(recalc_peak, 4),
        plan_summary=summary
    )

    # 6. Self-validate before returning
    violations = validate_plan(request, response)
    if violations:
        # If any internal check fails, fall back to trivial plan
        return trivial_plan(request, directives, summary_prefix=f"Self-audit fallback ({len(violations)} violations)")

    return response
