"""
[AGENT B — OPTIMIZER & PRIVATE JUDGE]
lib/validator.py: The canonical private judge.
Validates all interpretation invariants (I1..I8) and physics invariants (V1..V7).
"""
from typing import List, Optional, Union, Dict, Any
import math
from lib.contracts import (
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
    DirectiveInterpretation,
    HourlyPlanEntry
)

TOLERANCE = 0.01  # 0.01 kWh / 0.01 BDT canonical tolerance

def validate_plan(
    request: OptimizeEnergyRequest,
    response: OptimizeEnergyResponse,
    ground_truth_directives: Optional[List[Union[DirectiveInterpretation, dict]]] = None
) -> List[str]:
    """
    Validates a generated response against request and problem statement invariants.
    Returns a list of violation error strings (empty if valid).
    """
    violations = []
    
    # 0. Basic Schema checks
    if response.scenario_id != request.scenario_id:
        violations.append(f"Scenario ID mismatch: request={request.scenario_id}, response={response.scenario_id}")
        
    # --- Interpretation Invariants (I1..I8) ---
    notes = request.operator_notes
    interps = response.directive_interpretation
    
    # I1. Exactly one entry per note in note_index order 0..N-1
    if len(interps) != len(notes):
        violations.append(f"I1: Expected {len(notes)} directive interpretations, got {len(interps)}")
    for idx, interp in enumerate(interps):
        if interp.note_index != idx:
            violations.append(f"I1: Out-of-order or missing note_index at position {idx}: got {interp.note_index}")
            
        # I2. no_op <=> applies == False AND structured_adjustment is None
        if interp.directive_type == "no_op":
            if interp.applies is not False:
                violations.append(f"I2: note {idx} is no_op but applies is {interp.applies} (must be false)")
            if interp.structured_adjustment is not None:
                violations.append(f"I2: note {idx} is no_op but structured_adjustment is not null")
        else:
            if interp.applies is not True:
                violations.append(f"I2: note {idx} is {interp.directive_type} but applies is {interp.applies} (must be true)")
            if interp.structured_adjustment is None:
                violations.append(f"I2: note {idx} is {interp.directive_type} but structured_adjustment is null")
                
        # I6 & I7. Directive adjustments
        if interp.structured_adjustment is not None:
            adj = interp.structured_adjustment
            hours = getattr(adj, "hours", [])
            # I6. unique ints in [0, 23], ascending
            if not isinstance(hours, list) or len(hours) == 0:
                violations.append(f"I6: note {idx} hours must be a non-empty list")
            else:
                if any(not isinstance(h, int) or h < 0 or h > 23 for h in hours):
                    violations.append(f"I6: note {idx} hours contains invalid hour out of range [0, 23]: {hours}")
                if hours != sorted(list(set(hours))):
                    violations.append(f"I6: note {idx} hours not strictly ascending unique: {hours}")
                    
            # I7. Specific bounds
            if interp.directive_type == "solar_reduction":
                factor = getattr(adj, "factor", None)
                if factor is None or not (0.0 <= factor <= 1.0):
                    violations.append(f"I7: note {idx} solar_reduction factor must be in [0, 1], got {factor}")
            elif interp.directive_type == "minimum_battery_reserve":
                m = getattr(adj, "minimum_energy_kwh", None)
                if m is None or m < 0.0 or m > request.battery.capacity_kwh:
                    violations.append(f"I7: note {idx} minimum_energy_kwh out of bounds [0, {request.battery.capacity_kwh}], got {m}")
            elif interp.directive_type == "max_grid_window":
                g = getattr(adj, "max_grid_kwh", None)
                if g is None or g < 0.0 or not math.isfinite(g):
                    violations.append(f"I7: note {idx} max_grid_kwh must be finite and >= 0, got {g}")

    # Determine which directives to apply: ground_truth if provided, else response directives
    directives_to_check = ground_truth_directives if ground_truth_directives is not None else interps

    # Build active constraints
    eff_solar = [h.solar_kwh for h in request.hours]
    active_reserve = [request.battery.minimum_energy_kwh for _ in range(24)]
    can_charge = [True for _ in range(24)]
    can_discharge = [True for _ in range(24)]
    grid_caps = [float("inf") for _ in range(24)]
    
    for d in directives_to_check:
        if isinstance(d, dict):
            applies = d.get("applies", False)
            dtype = d.get("directive_type")
            adj = d.get("structured_adjustment")
        else:
            applies = d.applies
            dtype = d.directive_type
            adj = d.structured_adjustment
            if adj is not None and not isinstance(adj, dict):
                adj = adj.model_dump()
                
        if not applies or not adj:
            continue
            
        hrs = adj.get("hours", [])
        if dtype == "solar_reduction":
            factor = adj.get("factor", 1.0)
            for h in hrs:
                if 0 <= h < 24:
                    eff_solar[h] *= factor
        elif dtype == "minimum_battery_reserve":
            m = adj.get("minimum_energy_kwh", 0.0)
            for h in hrs:
                if 0 <= h < 24:
                    active_reserve[h] = max(active_reserve[h], m)
        elif dtype == "no_charge_window":
            for h in hrs:
                if 0 <= h < 24:
                    can_charge[h] = False
        elif dtype == "no_discharge_window":
            for h in hrs:
                if 0 <= h < 24:
                    can_discharge[h] = False
        elif dtype == "max_grid_window":
            g = adj.get("max_grid_kwh", float("inf"))
            for h in hrs:
                if 0 <= h < 24:
                    grid_caps[h] = min(grid_caps[h], g)

    # --- Physics Invariants (V1..V7) ---
    plan = response.hourly_plan
    if len(plan) != 24:
        violations.append(f"V7: hourly_plan must have exactly 24 entries, got {len(plan)}")
        return violations

    bat = request.battery
    prev_e = bat.initial_energy_kwh
    
    recalc_grid = 0.0
    recalc_cost = 0.0
    recalc_peak = 0.0

    for h in range(24):
        entry = plan[h]
        if entry.hour != h:
            violations.append(f"V7: hourly_plan entry {h} has hour {entry.hour} instead of {h}")
            
        demand = request.hours[h].demand_kwh
        tariff = request.hours[h].tariff_bdt_per_kwh
        grid = entry.grid_kwh
        solar_used = entry.solar_used_kwh
        action = entry.battery_action
        b_kwh = entry.battery_kwh
        e_after = entry.battery_energy_after_kwh
        
        # Non-negative checks
        if grid < -TOLERANCE or solar_used < -TOLERANCE or b_kwh < -TOLERANCE:
            violations.append(f"V3: Hour {h} has negative values: grid={grid}, solar={solar_used}, battery_kwh={b_kwh}")
            
        # V3. Action consistency
        if action == "idle":
            if abs(b_kwh) > TOLERANCE:
                violations.append(f"V3: Hour {h} action is idle but battery_kwh is {b_kwh} (must be 0)")
            charge = 0.0
            discharge = 0.0
        elif action == "charge":
            if b_kwh <= TOLERANCE:
                violations.append(f"V3: Hour {h} action is charge but battery_kwh is {b_kwh} (must be > 0)")
            if b_kwh > bat.max_charge_kwh_per_hour + TOLERANCE:
                violations.append(f"V3: Hour {h} charge {b_kwh} exceeds max_charge {bat.max_charge_kwh_per_hour}")
            charge = b_kwh
            discharge = 0.0
        elif action == "discharge":
            if b_kwh <= TOLERANCE:
                violations.append(f"V3: Hour {h} action is discharge but battery_kwh is {b_kwh} (must be > 0)")
            if b_kwh > bat.max_discharge_kwh_per_hour + TOLERANCE:
                violations.append(f"V3: Hour {h} discharge {b_kwh} exceeds max_discharge {bat.max_discharge_kwh_per_hour}")
            charge = 0.0
            discharge = b_kwh
        else:
            violations.append(f"V3: Hour {h} invalid action {action}")
            charge = 0.0
            discharge = 0.0

        # V1. Energy balance
        lhs = grid + solar_used + discharge
        rhs = demand + charge
        if abs(lhs - rhs) > TOLERANCE:
            violations.append(f"V1: Hour {h} energy balance failed: LHS={lhs:.4f} != RHS={rhs:.4f} (diff={abs(lhs-rhs):.4f})")
            
        # V2. Solar usage limit
        if solar_used > eff_solar[h] + TOLERANCE:
            violations.append(f"V2: Hour {h} solar_used {solar_used} > effective_solar {eff_solar[h]:.4f}")
            
        # Battery state update & V4 bounds
        expected_e = prev_e + charge - discharge
        if abs(e_after - expected_e) > TOLERANCE:
            violations.append(f"V4: Hour {h} battery_energy_after {e_after} != expected {expected_e} (diff={abs(e_after-expected_e):.4f})")
            
        if e_after < active_reserve[h] - TOLERANCE:
            violations.append(f"V4: Hour {h} battery_energy_after {e_after} < active reserve {active_reserve[h]}")
        if e_after > bat.capacity_kwh + TOLERANCE:
            violations.append(f"V4: Hour {h} battery_energy_after {e_after} > capacity {bat.capacity_kwh}")

        # V6. Directive limits
        if not can_charge[h] and charge > TOLERANCE:
            violations.append(f"V6: Hour {h} charged {charge} during no_charge_window")
        if not can_discharge[h] and discharge > TOLERANCE:
            violations.append(f"V6: Hour {h} discharged {discharge} during no_discharge_window")
        if grid > grid_caps[h] + TOLERANCE:
            violations.append(f"V6: Hour {h} grid {grid} exceeds max_grid cap {grid_caps[h]}")
            
        prev_e = e_after
        recalc_grid += grid
        recalc_cost += grid * tariff
        if grid > recalc_peak:
            recalc_peak = grid

    # V5. End-of-day battery neutrality
    if abs(prev_e - bat.initial_energy_kwh) > TOLERANCE:
        violations.append(f"V5: Battery neutrality failed: final E={prev_e:.4f} != initial E={bat.initial_energy_kwh:.4f}")

    # V7. Recalculated totals
    if abs(response.total_grid_kwh - recalc_grid) > TOLERANCE:
        violations.append(f"V7: total_grid_kwh {response.total_grid_kwh} != recalculated {recalc_grid:.4f}")
    if abs(response.total_cost_bdt - recalc_cost) > TOLERANCE:
        violations.append(f"V7: total_cost_bdt {response.total_cost_bdt} != recalculated {recalc_cost:.4f}")
    if abs(response.peak_grid_kwh - recalc_peak) > TOLERANCE:
        violations.append(f"V7: peak_grid_kwh {response.peak_grid_kwh} != recalculated {recalc_peak:.4f}")

    return violations
