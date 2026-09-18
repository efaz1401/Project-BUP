---
name: lp-optimizer-spec
description: Use when implementing or debugging the GridWise 24-hour
  scheduling optimizer, LP formulation, or plan re-derivation.
---
Use PuLP or SciPy linprog (HiGHS). Variables per hour h: g[h],s[h],c[h],d[h], all >= 0.

Upper bounds come from a precomputed bounds table, not from if-statements
inside the model:
  s[h] <= effective_solar[h]
  c[h] <= 0 if h in no_charge else max_charge
  d[h] <= 0 if h in no_discharge else max_discharge
  g[h] <= cap if h in max_grid else infinity

Constraints:
  g[h]+s[h]+d[h] == demand[h]+c[h]
  E[h] = initial + sum_{k<=h}(c[k]-d[k]);  reserve[h] <= E[h] <= capacity
  E[23] == initial

Objective: minimize sum(g[h]*tariff[h]) + 1e-6*sum(c[h]+d[h])
The epsilon term is MANDATORY. Without it the LP admits degenerate optima
where one hour both charges and discharges, which the response schema
cannot express.

After solving, in this exact order:
1. round c[h],d[h] to 6 dp; if both > 0, net them to a single direction
2. rederive s[h] = min(eff_solar[h], demand[h]+c[h]-d[h]); clamp >= 0
3. rederive g[h] = demand[h]+c[h]-d[h]-s[h]; clamp tiny negatives to 0
4. forward-replay E[h] from initial; snap |E[23]-initial|<1e-6 to exact
5. compute totals from the plan only
6. run lib/validator.py on the result

Infeasibility cascade: all directives -> drop one at a time -> drop all ->
fallback_trivial_plan. Log which tier fired. Never raise to the caller.

Trivial fallback: every hour idle, s[h]=min(eff_solar[h],demand[h]),
g[h]=demand[h]-s[h]. Satisfies neutrality and all bounds automatically.
