# GRIDPILOT

**An autonomous operations and finance crew for a GPU cloud.** When a rack overheats at 2 a.m., GRIDPILOT diagnoses it, plans a fix with CREAO agents, phones the on-call engineer through a Cartesia voice agent for approval, changes the infrastructure config through git, watches it recover, rolls back if it doesn't, and then the finance desk books the cost, matches the vendor's invoice against the contract and the meter, and catches the overcharge.

**Live app (Mana):** https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot  (sign in to Mana to watch live; approvals and payments stay with the operator)

**One line on the seats it fills:** the night-shift data-center operator, the accounts-payable analyst who checks vendor invoices before payment, and the cost controller who keeps incident spend inside budget, all signing off with a human controller.

## The moment

1. Cooling unit C02 degrades. Rack R17 crosses 88 °C for 3 ticks and an incident opens on its own.
2. The **CREAO Remediation Agent** pulls telemetry, capacity and workloads through GRIDPILOT's tools, tests options against the simulator and proposes a plan. A fan-only fix is policy-auto-allowed, so it executes itself.
3. It doesn't work. After 420 s the backend **rolls the config back**, verifies the restored tree byte for byte, and, because this is a special case, **creates a new CREAO agent on the fly** (Thermal Forensics Specialist) to work out why.
4. The specialist's findings feed a revised plan. Migrating customer workloads costs money and touches a protected SLA, so policy requires a human: the **Cartesia voice agent calls the operator**, answers questions, evaluates alternatives on the call, and collects approval with a one-time code.
5. The approved config is committed through an isolated git worktree with validation (schema, capacity, placement, power bounds, SLA, cooling feasibility), the twin recovers, and the **cost is accrued in a double-entry ledger**.
6. The vendor invoice arrives. The finance desk matches it three ways (contract × meter × invoice), applies a credit the vendor still owes from a March dispute, and holds a look-alike "we changed banks" email until someone calls the number on file.

## Real vs simulated

| Real | Simulated |
|---|---|
| CREAO agent runs (hosted agents created through the CREAO API; run IDs stored) | GPU hardware: 64 racks, 512 GPUs, 4 cooling units |
| Cartesia Managed Agent, webhook tools, phone number, outbound calls | Airflow and thermal physics (approximate 2D advection-diffusion, **not CFD**) |
| Deterministic policy engine and approval verification | Customers, workloads, vendors, invoices, bank feed, company history (Mar to Oct 2026) |
| Git-backed config store: candidates, diffs, snapshots, rollbacks | Planted invoice errors (with a separate answer key) |
| Double-entry ledger, three-way matching, controls, payment runs, audit trail | |

## Proof it works

- **Finance desk vs answer key:** 9/9 invoices decided correctly, 5/5 planted errors caught, 0 false flags. `python tests/test_finance_desk.py` runs 12 checks (score, approval limits, segregation of duties, held bank change, override rule, unbalanced-entry refusal, March credit applied, price-variance short pay, payment run, balanced trial balance).
- **CREAO AP Analyst vs deterministic matcher:** the hosted agent's recommendations agreed with the matcher on 6/6 invoices it reviewed (run `1aa63ee0`). Disagreements raise a controller alert instead of auto-acting.
- **Live incident run (24 Oct 2026):** CREAO remediation run `72f88b96` (8 plan evaluations, auto-allowed fan fix executed) → no recovery → verified rollback → specialist agent created in about 20 s and run `00ba099a` → replan run `4d8082cd` → plan v2 awaiting human approval. 58 audited tool calls.
- **Incident engine offline:** `python tests/test_incident_flow.py` runs detection → plan → refusal without approval → approval → execution → recovery → invoice reconciliation (variance explained by the transfer record). Add `fail` to arm a post-apply fan fault and test rollback.

## Hyphenate track: the finance desk

- **Company:** Atlas Compute, a GPU cloud whose finance team pays for burst GPU capacity, electricity, network transfer and facilities repairs.
- **Sources that disagree:** contract (price), GRIDPILOT's meter (quantity), the vendor invoice, the vendor master (bank details), and the bank feed. The system says which one it trusts and why: contract beats invoice on price, meter beats invoice on quantity, vendor master beats an email on bank details.
- **Time:** a March overbilling dispute left an open credit memo (CM-0311). In October the desk applies it to the earliest eligible invoice.
- **Limits and segregation of duties:** the autonomous AP seat may approve only clean matches up to $250; everything else needs the controller; the requester of PO-7731 cannot approve its invoice; bank-detail changes are held until a callback to the number on file is recorded; the ledger refuses unbalanced entries.
- **Explainable and accountable:** every check cites its source (contract clause, meter ID, subledger), every journal entry records who posted and who approved it, and every decision is in the audit trail.
- **Cost control:** remediation plans show their cost against the remaining burst-capacity budget before approval; execution accrues the cost (Dr 5200 / Cr 2100); budget alerts fire on actuals and run-rate; a post-incident cost report ties accrual, invoice and payment together.

## Cartesia track: voice where it matters

The on-call engineer gets a phone call, not a dashboard ping. The Managed Agent (Sonic voice, Cartesia-hosted LLM) has six webhook tools into GRIDPILOT: `get_incident_context`, `evaluate_alternative` (runs the simulator forecast live on the call), `get_cost_comparison`, `approve_plan`, `reject_plan`, `escalate_incident`. Voice expresses intent; the backend verifies authority (one-time 6-digit code shown only to the operator, hashed, locked after 3 failures, bound to one plan version, expires in 15 min). Calls go only to a registered operator who consented.

## CREAO: autonomous agents, created when needed

- **Remediation Agent** (diagnose, plan, replan, execute approved plans, post-incident summary).
- **Specialists created on the fly** through the CREAO API when a special case appears: Thermal Forensics (post-apply failure), Capacity Placement (validation failure), FinOps Invoice Auditor (unreconciled invoice), SLA Guardian (protected customer in breach). The Remediation Agent can request a brand-new specialist by describing its mission.
- **AP Analyst** for the finance desk.
- Every run gets a scoped tool token: specialists can read and evaluate but never propose or execute; nothing an agent says is trusted until the deterministic backend re-checks it.

## Architecture

```
Mana user_div (React, three.js twin, ECharts) ── WebSocket/REST ──► aiohttp backend (Mana sandbox daemon + ingress)
                                                                     ├─ sim.py         deterministic simulator (seed 42)
                                                                     ├─ engine.py      incident state machine, plans, approvals, execution, rollback
                                                                     ├─ policy.py      auto / approval / forbidden tiers
                                                                     ├─ configstore.py git-backed YAML infrastructure config
                                                                     ├─ finance.py     rate cards, forecasts, incident invoices
                                                                     ├─ finance_desk.py ledger, AP inbox, three-way match, controls, scoring
                                                                     ├─ specialists.py dynamic CREAO agent catalog
                                                                     ├─ integrations.py / voice.py  CREAO and Cartesia adapters
                                                                     └─ server.py      routes, agent tool endpoints, webhooks
```

Secrets never enter this process or this repo: Mana's server-side proxy injects `CREAO_API_KEY`, `CREAO_CLI_KEY` and `CARTESIA_API_KEY` into outbound requests.

## Run it locally

Needs Python 3.10+ and git. No build step: the backend serves the same UI file that runs in Mana.

```
git clone https://github.com/pateldhruv1672/gridpilot.git && cd gridpilot
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python tests/test_finance_desk.py                       # 12/12 checks
python tests/test_incident_flow.py                      # add "fail" to test rollback
GRIDPILOT_LOCAL=1 python backend/server.py              # then open http://127.0.0.1:8787/
```

That gives you the full simulator, 3D control room, incident engine, policy, git config store, finance desk and ledger. Use **Demo → degrade C02** to start the R17 incident.

**With the sponsor integrations (optional):**

1. Copy `.env.example` to `.env` and fill in `CREAO_CLI_KEY` (CREAO Developer Platform key, `cr_sk_…`) and `CARTESIA_API_KEY` (`sk_car_…`). Keys are read from the environment and never written anywhere.
2. CREAO agents and the Cartesia voice agent call back into GRIDPILOT, so expose the server publicly, for example `cloudflared tunnel --url http://127.0.0.1:8787`, and put that URL in `PUBLIC_BASE_URL`.
3. `set -a; source .env; set +a; python backend/server.py`, then open Connections → **Test connections** (creates the Cartesia agent, tools and phone number) and register the operator's US phone number.

Without keys, incidents stay at "CREAO disconnected" rather than faking an agent.

Local mode binds to 127.0.0.1 and treats you as the operator. Inside Mana the backend runs as the sandbox daemon `gridpilot` behind Mana ingress, identity comes from Mana sign-in, and keys go through Mana's server-side proxy.

## Known limitations

- The thermal model is an approximate 2D grid, not CFD; physical numbers are illustrative.
- Only the overheating scenario is implemented end to end; SLA-breach and power-overload incidents are rejected on purpose rather than faked.
- Cartesia-provisioned numbers dial US numbers only.
- Disputes are drafted, not emailed; vendors and the bank are simulated.
- Agent output formats vary between runs; the backend never depends on them, only on tool calls it validates.

## Responsible use

Human approval is required for anything that costs money, touches a protected customer, or moves cash. Voice approval works with no screen: GRIDPILOT itself phones the registered on-call engineer, reads the exact plan version back, and a clear spoken yes approves that version (ambiguous answers never do). The outbound call holds a per-call token, the backend re-checks version, window and live state, and the engineer's words are stored with the approval. Visitors get a read-only live view. Calls go only to a consenting, registered operator.

## SpacetimeDB live state (maincloud/gridpilot)

| Setting | Value |
|---|---|
| Host | https://maincloud.spacetimedb.com |
| Database | gridpilot |
| Module | `stdb/src/index.ts` (TypeScript, spacetimedb 2.13) |
| Config | `spacetime.json` (database, server, module-path and a TypeScript **generate** target → `frontend/module_bindings`) |

- **Tables (public, read-only for clients):** `rack`, `cooling_unit`, `facility_summary`, `thermal_field`, `incident`, `plan`, `audit_event`. Private: `writer`, `admin`.
- **Writes:** one `publish_tick` reducer call per second from the Python backend worker (`backend/stdb.py`) carrying the real simulator snapshot plus any incident / plan / audit changes. Every mutating reducer checks the caller against `writer`; only the database owner can `grant_writer` / `revoke_writer`. Anonymous visitors are refused (`read-only: this identity is not an authorized GridPilot writer`).
- **Worker credential:** a dedicated SpacetimeDB identity whose token lives only in Mana's server-side secrets (`SPACETIMEDB_WORKER_TOKEN`). The backend calls through Mana's symbolic proxy, which injects the token; it never enters the process or the repo.
- **Dashboard:** subscribes with the generated bindings (`tools/embed_bindings.py` bundles them into the user_div). 3D racks, heatmap, cooling units, facility metrics and incident state/plans render from the subscription; the header shows `SPACETIMEDB LIVE · #seq`. Approvals, executions, calls and finance detail still come from the backend until they are migrated.
- **Unchanged:** Python thermal model, CREAO and Cartesia integrations, git configuration store, audit log in the backend DB (mirrored, last 600 events).
- **Regenerate bindings:** `spacetime generate --yes` (reads `spacetime.json`), then `python tools/embed_bindings.py`.
- **Acceptance test (2026-10-24):** two independent clients (the Mana dashboard tab and a separate subscriber) saw identical C02 values at the same publish sequence: repair #98 nominal 90%, #102 95%; degrade #124 degraded 33% / 26.6 °C, #127 31% / 27.2 °C.

## Specialist team, fraud controls and the Audit Watcher (Oct 24 late build)

- **CREAO specialist team on every incident.** When an incident opens, GRIDPILOT spawns a Thermal Forensics specialist (root cause, racks to avoid) and a Capacity Placement specialist (feasible targets) in parallel with the Remediation Agent. Every plan version that needs human approval also gets a Change Management reviewer (changes, blast radius, rollback, verification, required approvals, GO / GO WITH CONDITIONS / NO-GO). Specialists read and evaluate only; their recommended actions are re-checked by the deterministic forecaster and every request, result and evaluation is an audit event. Shown in the incident panel as "CREAO specialist team".
- **Fraudulent cooling invoice scenario** (Scenarios tab, also a "+ Test invoice" preset). A $4,850 invoice imitating CoolTech's PO-7731 repair arrives from a lookalike domain with new bank details. The matcher holds it and cites the vendor master (remit-to and sender domain), contract and usage (PO-7731 is one job, already billed in full on CTS-1107, backed by one completed repair accrual) and separation of duties (the Facilities requester cannot approve; controller sign-off required). Contract pricing is matched per SKU, uncovered lines are held, and a payment above the billed amount is impossible.
- **Audit Watcher agent** (`agents/stdb_watcher.ts`, daemon `gridpilot-audit-watcher`). An independent agent that subscribes to SpacetimeDB anonymously, exactly like a dashboard, and verifies from the replicated data that every execution was preceded by a human approval of the same plan version (or an AUTO_ALLOWED decision), that no agent identity approved anything, that no rack stays critical without an incident, and that the feed is fresh. It proves it cannot write by calling the harmless `probe_write` reducer, which the module rejects. Its findings go to the backend, which records them in the audit log. Build: `NODE_PATH=stdb/node_modules esbuild agents/stdb_watcher.ts --bundle --platform=node --format=esm --outfile=agents/dist/stdb_watcher.mjs`.
- **Who is subscribed.** The module keeps a public `connection` table through its connect/disconnect lifecycle reducers. Click the SPACETIMEDB badge to see the single writer (backend worker identity, listed in the private `writer` table) and every live subscriber (each dashboard, the Audit Watcher).

## Production deployment path

GRIDPILOT keeps the physical layer behind one tool API, so production is the same architecture with real adapters:

1. **Telemetry in:** replace the simulator with DCIM/BMS feeds (cooling units, PDUs over BACnet/Modbus/SNMP), GPU telemetry (DCGM/Redfish) and scheduler state (Kubernetes/Slurm). The detectors, twin and forecaster consume the same snapshot shape.
2. **Actions out:** the git config store becomes the change repository; executors apply approved commits through the scheduler API (migrations), DCGM/nvidia-smi power limits and BMS setpoints, with the existing validation, verification and automatic rollback.
3. **Identity and roles:** map the existing seats (agent, AP agent, controller, on-call operator, owner) to SSO groups; policy limits and separation of duties stay enforced in the backend.
4. **State and audit:** SpacetimeDB remains the live state bus with one authorized writer; the audit log is exported to the company's SIEM and finance decisions to the ERP.
5. **Rollout:** shadow mode first (agents propose, nothing executes), then human-approved execution, then autonomous execution only inside the AUTO_ALLOWED policy envelope.


## Real-time delivery under load

- Forecast simulations (plan evaluation, maintenance what-ifs) run in a forkserver process pool (`backend/forecast_pool.py`, 3 workers) instead of threads. In threads they held the GIL and starved the asyncio loop: during the compound storm the loop managed about one tick every 4 s and dashboards stopped updating. Results are identical to the in-thread versions (verified series-for-series).
- State is pushed as `state_patch` messages containing only the sections that changed since the last push to that client (about 100 KB instead of 500 KB), at most once per second; twin and GPU data stream in ticks. Sends are non-blocking per client, so one slow browser can never stall the simulation loop.


## Sustainability: water and carbon

The energy agent dispatches the site battery carbon-aware: it charges in the midday solar dip, discharges through the evening carbon ramp (also the price peak) and only tops up to protect a 40% ride-through reserve. The twin keeps a ledger against a counterfactual of the same load drawn straight from the grid with no battery, net of 92% round-trip losses:

- **CO₂ avoided** = grid energy x time-of-day carbon intensity, counterfactual minus actual.
- **Water saved** = upstream grid water (1.9 L/kWh x thermal share of the mix), counterfactual minus actual.
- **Site water / WUE**: heat rejected x 2.12 L/kWh (1.59 L/kWh evaporation + blowdown at 4 cycles), reduced at night when the economizer carries part of the load.
- Every plan evaluation also reports its water and CO₂ delta versus doing nothing over the 10-minute forecast, positive or negative.

All simulated; order-of-magnitude, not metered. Assumptions are shown in the UI under "how this is computed".
