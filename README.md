# GRIDPILOT

Autonomous AI data center operations for a simulated GPU cloud (Atlas Compute, pod SFO-01).

- **Digital twin**: 64 racks / 512 GPUs, 4 cooling units, an approximate 2D airflow + thermal model (not CFD), rendered in three.js with live heatmaps, airflow particles, thermal plumes and workload migration arcs.
- **CREAO**: the Remediation Agent diagnoses, plans, compares costs and replans by calling GRIDPILOT's HTTPS tools with a per-run scoped token. Run IDs and results are stored.
- **Cartesia**: a Managed Agent calls the registered operator, answers questions, evaluates alternatives and collects approval intent. Authority is verified by the backend (one-time code or authenticated console approval).
- **Backend**: deterministic detection, persistent incident state machine, policy engine, versioned git config store with isolated worktree candidates, validation, real diffs, recovery monitoring, rollback snapshots, finance model and invoice reconciliation, full audit log.

## Layout

| Path | What |
|---|---|
| `backend/sim.py` | Simulator (deterministic, seed 42) |
| `backend/engine.py` | Incident engine: state machine, plans, approvals, execution, rollback, finance closeout |
| `backend/policy.py` | Deterministic policy tiers (auto / approval / forbidden) |
| `backend/finance.py` | Rate cards, forecasts, invoices, reconciliation |
| `backend/configstore.py` | Git-backed infrastructure config (YAML) |
| `backend/integrations.py`, `backend/voice.py` | CREAO and Cartesia adapters (keys injected server-side) |
| `backend/server.py` | aiohttp REST + WebSocket + agent tool endpoints |
| `frontend/GridpilotApp.jsx` | Control room UI (Mana user_div source, synced from the live app) |
| `docs/SPEC.md` | Build specification and architecture update |
| `docs/CREAO_AGENT.md` | Brief for creating the CREAO Remediation Agent |

## Run

```
pip install aiohttp numpy pyyaml requests
cd backend && GRIDPILOT_PORT=8787 python server.py
```

In Mana it runs as the supervised daemon `gridpilot` behind ingress; secrets stay in Mana's credential vault and reach providers through its symbolic proxy, so this repository contains no keys.

Simulated: hardware, physics, customers, invoices. Real: policy enforcement, CREAO runs, Cartesia calls, operator decisions, git config changes, deterministic calculations.
