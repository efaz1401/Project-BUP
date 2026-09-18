# GridWise — Smart Campus Energy Optimization Service
**BUP CSE Fest 2026 · AI Hackathon · Online Preliminary Round**

---

## 1. What It Is
GridWise is a high-performance, containerized Python 3.12 FastAPI microservice that solves the Smart Campus Energy Optimization Challenge. It combines language-model reasoning with deterministic mathematical optimization to interpret natural-language campus operator notes and generate valid, cost-minimizing 24-hour campus energy schedules.

The service exposes two endpoints:
- `GET /health` — Zero-dependency readiness probe returning `{"status": "ok"}` in < 1ms.
- `POST /optimize-energy` — LLM interpretation + high-speed linear programming dispatch over a 24-hour horizon.

---

## 2. Architecture & Pipeline

```
HTTP POST /optimize-energy
          │
          ▼
┌────────────────────────────────────────────────────────┐
│ 1. Schema Validation & Normalization (lib/contracts.py) │
│    Strict Pydantic v2 validation (HTTP 400 on error)   │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 2. Operator-Note Interpretation & Cache (lib/llm.py)   │
│    SHA-256 memo cache -> Gemini 2.5 Flash / Groq LLM    │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 3. Deterministic Guardrails & Repair (lib/guardrails) │
│    Demotes bad types to no_op; normalizes hours/factors│
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 4. Mathematical LP Optimization (lib/optimizer.py)     │
│    SciPy HiGHS continuous solver with epsilon cycling  │
│    penalty; exact battery neutrality & bounds          │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 5. Plan Re-derivation & Self-Audit (lib/validator.py)  │
│    Round to 6 dp, net flows, replay SoC, assert V1..V7 │
│    Infeasibility cascade fallback if internal failure  │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
HTTP 200 JSON Response (p95 < 20ms)
```

---

## 3. Quickstart from Clean Clone

### Prerequisites
- Python 3.12+
- `pip`

```bash
# 1. Clone repository
git clone <repository_url>
cd gridwise

# 2. Create and activate virtual environment
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The service is now ready at `http://127.0.0.1:8080`.

---

## 4. Environment Variables

Only environment variable **names** are documented; secret values are never committed or logged:

| Variable Name | Required | Default / Description |
| :--- | :--- | :--- |
| `PORT` | Optional | `8080` (Service HTTP port) |
| `GEMINI_API_KEY` | Optional* | API key for Google Gemini 2.5 Flash / 1.5 Flash |
| `GOOGLE_API_KEY` | Optional* | Fallback key for Google Gemini models |
| `GROQ_API_KEY` | Optional* | API key for Groq (Llama 3.3 70B / 3.1 8B) |
| `OPENAI_API_KEY` | Optional* | API key for OpenAI (gpt-4o-mini) |
| `OFFLINE_FALLBACK` | Optional | `1` (Forces keyless / non-LLM execution via deterministic heuristic. **For local development only.** Must NOT be set during the judging window — the LLM must remain in the operator-note interpretation path per the rubric.) |

*\*Note on LLM keys: The service features an automated multi-provider cascade (Gemini → Groq → OpenAI). At least one provider key is **required for judging**: the language model must be in the operator-note interpretation path (see Participant Guide §04 and §09). If no API keys are provided and `OFFLINE_FALLBACK` is not set, the service still returns a controlled `no_op` per note (with `applies=false`, `structured_adjustment=null`) instead of crashing — so the `/optimize-energy` endpoint stays stable, but interpretation quality degrades. `OFFLINE_FALLBACK=1` explicitly forces the deterministic heuristic path even when keys are present; it exists for local development and reproducibility work, never for the graded judging window.*

---

## 5. Model & Generative AI Role
- **Model Choice:** Google Gemini 2.5 Flash / Groq Llama 3.3 70B / OpenAI GPT-4o-mini.
- **Role:** Generative interpretation of 1–3 unstructured natural-language operator notes into structured, machine-checkable operational directives.
- **Prompt Architecture:** Explanation-first schema forcing chain-of-thought semantic reasoning before committing to directive enums, hour spans, and numeric values.
- **Unit Resolution:** Request `battery.capacity_kwh` is explicitly injected into prompt context so percentage-of-capacity reserves (e.g. "50% of capacity") are translated directly to absolute kWh values.

---

## 6. Deterministic Guardrails & Sanitization
Every LLM output is strictly validated by `lib/guardrails.py` before passing to the optimizer:
1. **No Entry Dropping:** Entries are never omitted. Failing or unrecognizable directives are demoted to `no_op` with `applies = false` and `structured_adjustment = null`.
2. **Hour Window Normalization:** Start-inclusive and end-exclusive whole hours are strictly sorted ascending in `[0, 23]`.
3. **Factor Semantics:** Solar reduction factor represents usable fraction *remaining* ($0.0$ to $1.0$). A note stating "80% reduction" is normalized to $1.0 - 0.8 = 0.2$.
4. **Input Sanitization & Injection Defense:** Arbitrary operator notes (including adversarial instructions, SQL/prompt injection phrases, or 2000+ character strings) are safely filtered or demoted without affecting solver constraints.

---

## 7. Optimization Solver Formulation
- **Solver Engine:** `scipy.optimize.linprog(method='highs')`.
- **Variables (96 continuous):** Hourly grid purchase $g[h]$, solar utilized $s[h]$, battery charge $c[h]$, and battery discharge $d[h]$ for $h \in [0..23]$.
- **Objective Function:**
  $$\min \sum_{h=0}^{23} \Big( g[h] \times \text{tariff}[h] \Big) + 10^{-6} \sum_{h=0}^{23} (c[h] + d[h])$$
  *The $10^{-6}$ epsilon penalty strictly eliminates degenerate solutions with simultaneous charging and discharging.*
- **Physical Invariants Enforced:**
  - Hourly energy balance: $g[h] + s[h] + d[h] = \text{demand}[h] + c[h]$
  - Active battery reserve bounds: $\max(\text{base\_min}, \text{directive\_min}[h]) \le E[h] \le \text{capacity}$
  - Battery charge/discharge rate caps: $c[h] \le \text{max\_charge}$, $d[h] \le \text{max\_discharge}$
  - End-of-day battery neutrality: $E[23] = E_{\text{initial}}$
  - Solar curtailment: $0 \le s[h] \le \text{effective\_solar}[h]$
  - Directive windows: $c[h]=0$ during `no_charge`, $d[h]=0$ during `no_discharge`, $g[h] \le \text{cap}$ during `max_grid`.
- **Performance:** Complete LP formulation and solve executes in $\approx 2$ ms.

---

## 8. Verification & Test Suite

Run the full verification suite against the active service:

```bash
# On Linux/macOS:
make verify

# On Windows (PowerShell):
.\tools\verify.ps1 -Url http://127.0.0.1:8080
```

The test harness executes:
1. **Public Cases (10/10):** Validates all 10 canonical public scenarios; confirms 0 constraint violations and optimal cost equality.
2. **Paraphrase Drills (34/34):** Tests unseen phrasing, clock formats, Bengali-English loanwords, and percentages.
3. **Adversarial Suite:** Injects malformed JSON, out-of-range hours, prompt injection attacks, negative demands, and missing fields; asserts zero 5xx responses.
4. **Soak Benchmark:** Executes 20 back-to-back requests measuring p95 latency ($< 20$ ms).

---

## 9. API Usage Examples

### Health Endpoint
```bash
curl -s http://127.0.0.1:8080/health
```
**Expected Response:**
```json
{"status":"ok"}
```

### Optimize Energy Endpoint
```bash
curl -s -X POST http://127.0.0.1:8080/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next month's registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

**Expected Response Snippet:**
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
      "explanation": "Solar output reduced during window (factor 0.25)."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Unrelated campus operational note; does not affect today's energy schedule."
    }
  ],
  "hourly_plan": [...],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365,
  "peak_grid_kwh": 175,
  "plan_summary": "Optimized schedule (Tier 0 (Full directives)): shifted battery storage to offset high tariffs, applied active directives, and maintained end-of-day battery neutrality."
}
```

---

## 10. Docker Build & Deployment

### Build Multi-Platform Container (recommended)
```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<your-org>/gridwise:latest --push .
```

For local single-platform testing only:
```bash
docker build -t gridwise:latest .
```

### Pull & Run (Fallback Image)
```bash
docker pull ghcr.io/<your-org>/gridwise:latest
docker run -d --name gridwise -p 8080:8080 \
  -e OFFLINE_FALLBACK=1 \
  ghcr.io/<your-org>/gridwise:latest
```

### Run Locally Without Registry
```bash
docker run -d --name gridwise -p 8080:8080 gridwise:latest
```

Verify the running container:
```bash
curl http://127.0.0.1:8080/health
```

---

## 11. Dependencies & Credits
- **Web Framework:** [FastAPI](https://fastapi.tiangolo.com/) & [Uvicorn](https://www.uvicorn.org/)
- **Data Validation:** [Pydantic v2](https://docs.pydantic.dev/)
- **Optimization:** [SciPy](https://scipy.org/) (`scipy.optimize.linprog` with HiGHS dual-simplex/interior-point)
- **Scientific Computing:** [NumPy](https://numpy.org/)
- **HTTP Networking:** [Requests](https://requests.readthedocs.io/)

---

## 12. Known Limitations & Edge Cases
1. **Sub-hour granularity:** The specification defines a 24-hour horizon in 1-hour blocks. Finer temporal resolutions (e.g. 15-minute or 5-minute SCADA intervals) are not supported by the contest problem definition.
2. **Grid export:** Solar net metering and grid electricity export are explicitly disabled by the contest rules. Excess solar above demand + charging capacity is curtailed.

---

## 13. Security & Secret Handling
- No secrets, tokens, API keys, `.env` files, or passwords are committed to the repository or baked into Docker layers.
- Production error handlers catch unhandled exceptions and emit a sanitized `{"error": "internal error"}` payload without exposing stack traces or environment variables.
