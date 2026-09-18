# GridWise Build Constitution

You are one of several agents building ONE deliverable: a Python 3.12 FastAPI
service exposing GET /health and POST /optimize-energy, containerized and
deployed. Deadline is hard. Validity beats cleverness. Shipping beats scope.

## PRIME DIRECTIVES
P1. Never return an invalid schedule. A valid expensive plan scores; an
    invalid cheap plan scores zero for that case.
P2. Never return 5xx on a valid request. Never crash. Never leak secrets,
    keys, prompts, or stack traces in responses or logs.
P3. Never edit a file outside the OWNED PATHS listed in your task brief.
    If you need a change elsewhere, STOP and report it to the orchestrator.
P4. Never change lib/contracts.py. It is frozen. Report needed changes.
P5. After every task, run `make verify` and paste the output. If it fails,
    fix and rerun. Do not report success on a failing gate.
P6. Prefer deleting scope over missing a milestone.

## DOMAIN SPEC (canonical — memorize, do not re-derive)

### Directive types (closed set, no others exist)
solar_reduction          -> {"hours":[int], "factor": float}
minimum_battery_reserve  -> {"hours":[int], "minimum_energy_kwh": float}
no_charge_window         -> {"hours":[int]}
no_discharge_window      -> {"hours":[int]}
max_grid_window          -> {"hours":[int], "max_grid_kwh": float}
no_op                    -> null

### Interpretation invariants (I1..I8)
I1. Exactly one directive_interpretation entry per operator note, emitted in
    note_index order 0..N-1. No gaps, no duplicates, no extras.
I2. no_op  <=> applies == false  AND  structured_adjustment == null.
    Every non-no_op directive has applies == true and a full adjustment.
I3. Time windows are START-INCLUSIVE, END-EXCLUSIVE, whole hours.
    "6 PM until 9 PM"->[18,19,20]; "1 PM to 3 PM"->[13,14];
    "11 AM until 1 PM"->[11,12]; "noon until 2 PM"->[12,13];
    "13:00 to 15:00"->[13,14]; "5 PM until 7 PM"->[17,18].
I4. factor is the FRACTION REMAINING, never the fraction lost.
    "80% reduction"->0.2; "drops to 25%"->0.25; "about half"->0.5;
    "one-fifth of normal"->0.2.
I5. Percentage reserves resolve against battery.capacity_kwh.
    "50% of capacity", capacity 200 -> minimum_energy_kwh = 100.
I6. hours are unique integers in [0,23], ascending.
I7. 0<=factor<=1; 0<=minimum_energy_kwh<=capacity_kwh; max_grid_kwh>=0 finite.
I8. Anything not affecting TODAY'S 24h electricity schedule is no_op
    (menus, library hours, seminars, registrations, notices, deadlines).

### Physics invariants (checked every hour h in 0..23) (V1..V7)
V1. grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h]   (±0.01)
V2. 0 <= solar_used[h] <= effective_solar[h]
    where effective_solar[h] = solar_kwh[h] * factor  if h in a
    solar_reduction window, else solar_kwh[h]
V3. charge[h] <= max_charge_kwh_per_hour; discharge[h] <= max_discharge_kwh_per_hour
    battery_action in {charge,discharge,idle}; battery_kwh >= 0;
    battery_kwh == 0 iff action == idle. Never both charge and discharge.
V4. reserve[h] <= battery_energy_after[h] <= capacity_kwh
    reserve[h] = max(battery.minimum_energy_kwh, any active directive reserve)
V5. battery_energy_after[23] == initial_energy_kwh   (±0.01)   [NEUTRALITY]
V6. Directive hard limits hold: charge==0 in no_charge hours;
    discharge==0 in no_discharge hours; grid[h] <= max_grid_kwh in cap hours.
V7. total_grid_kwh, total_cost_bdt, peak_grid_kwh are RECOMPUTED FROM
    hourly_plan, never taken from the solver objective. All values finite,
    non-negative. hourly_plan has exactly 24 entries, hours 0..23 ascending.

### Operational limits
- GET /health returns {"status":"ok"} within 60s of container start, and
  MUST succeed with zero API keys configured and zero network access.
- POST /optimize-energy must answer in <30s; target p95 <= 5s.
- Malformed/unparseable body -> HTTP 400 with a small JSON error object.
- Well-formed but semantically invalid -> 400 or 422, never 500.
- Tolerance everywhere: 0.01 kWh / 0.01 BDT absolute.

## ARCHITECTURE (fixed — do not redesign)
request -> validate(contracts) -> llm_extract(all notes, 1 call)
        -> guardrails(demote failures to no_op) -> build_bounds
        -> solve_lp -> rederive+replay -> self_validate
        -> [if invalid] fallback_trivial_plan -> respond

## FILE MAP (ownership is exclusive)
lib/contracts.py      FROZEN   pydantic request/response models
lib/optimizer.py      AGENT_B  LP build + solve + rederive
lib/validator.py      AGENT_B  replay validator (the private judge)
lib/fallback.py       AGENT_B  trivial always-valid plan
lib/llm.py            AGENT_C  provider calls, schema-constrained
lib/prompt.py         AGENT_C  prompt text
lib/guardrails.py     AGENT_C  validate+repair+demote interpretations
lib/cache.py          AGENT_C  sha256 memo
app/main.py           AGENT_A  FastAPI wiring, handlers, startup warmup
Dockerfile, Makefile  AGENT_A
tools/harness.py      AGENT_D  public-case runner + paraphrase drills
fixtures/*.json       AGENT_D  the 10 public cases
README.md             AGENT_A  (final pass by orchestrator)

## DEFINITION OF DONE (all must be true before submit)
D1. 10/10 public cases: interpretation matches expected (explanation text
    ignored) AND returned plan passes lib/validator.py.
D2. Team cost <= reference cost on >=8/10 cases.
D3. 30 paraphrase drills produce identical directives.
D4. Adversarial suite: 0 uncaught exceptions, 0 5xx.
D5. Live URL passes the full harness from outside the dev machine.
D6. Image pullable from GHCR, multi-arch, starts with no env vars, /health ok.
D7. README clean-room test passes verbatim.
D8. Video <=3:00 recorded and linked.
