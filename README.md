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

The on-call engineer gets a phone call, not a dashboard ping. The Managed Agent (Sonic voice, Cartesia-hosted LLM) has six webhook tools into GRIDPILOT: `get_incident_context`, `evaluate_alternative` (runs the simulator forecast live on the call), `get_cost_comparison`, `request_plan_approval`, `reject_plan`, `escalate_incident`. Voice expresses intent; the backend verifies authority (one-time 6-digit code shown only to the operator, hashed, locked after 3 failures, bound to one plan version, expires in 15 min). Calls go only to a registered operator who consented.

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

Human approval is required for anything that costs money, touches a protected customer, or moves cash. Voice approval needs a one-time code bound to one plan version. Visitors get a read-only live view. Calls go only to a consenting, registered operator.
