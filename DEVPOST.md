# GRIDPILOT

**The autonomous on-call crew for a GPU cloud: it runs the data center at 3 a.m. and closes the books on what that night cost.**

![A rack overheats and GRIDPILOT opens the incident on its own](docs/media/gif-thermal-incident.gif)

> **Seats it fills (one line):** at Atlas Compute, GRIDPILOT is the infrastructure on-call first responder (detect, diagnose, plan, execute only after sign-off) *and* the accounts-payable analyst plus cost controller for the vendors that night touched (GPU capacity, power, network, facilities repairs).

| | |
|---|---|
| **Live app (inside Mana, open it and operate)** | https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot |
| **Source** | https://github.com/pateldhruv1672/gridpilot |
| **Core CREAO agent** | `GRIDPILOT Remediation Agent` · agent ID **`b01e1b42-3b5a-44a4-ad3a-4b5736622db8`** |
| **CREAO run share link** | **TODO: paste the agent.creao.ai share link for run `50315dd0-e715-4511-bab5-7c5fa1be637a` here** |
| **Mana build chat** | https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot?chatId=00179287000977150b6f964a5b74736aa39 (**TODO: set to public with Share before submitting**) |
| **Tracks** | Hyphenate · CREAO · Cartesia · SpacetimeDB · Mana |

---

## The moment

**1. It catches the fraud a busy controller would miss.** A $4,850 "CRAH repair" invoice arrives from CoolTech Services, requested by someone in Facilities, against PO-7731. Every field looks right. GRIDPILOT's AP seat holds it anyway: PO-7731 was already billed in full on CTS-1107, and the person who requested the purchase cannot be the one who approves it (segregation of duties). The hold reason cites the vendor master, the contract and the usage record it checked. A look-alike "we changed banks, please pay the new account" email on a $10 Northbeam invoice gets the same treatment: held until someone calls the number on the vendor master, not the one in the email.

![Held invoice with sources cited](docs/media/05-finance-held-invoice.png)

**2. It recovers when its own fix fails.** C02 degrades, rack R17 crosses 88 °C, and the incident opens with no human in the loop. The CREAO Remediation Agent pulls telemetry, capacity and workloads through GRIDPILOT's tools, simulates options, and proposes. A fan-only fix is auto-allowed by policy, so it runs. When the rack does not recover, the backend **rolls the config back from git**, verifies it byte for byte, and **creates a new CREAO specialist on the fly** to work out why.

**3. It phones a human when it needs one, and lets them push back.** Migrating customer workloads costs money and touches a protected SLA, so policy requires a person. The Cartesia voice agent calls the on-call engineer, explains the incident in plain words, and when the engineer says *"what if we move it to R38 instead?"* it simulates exactly that, explains the tradeoffs honestly (cross-zone transfer charge, a workload that will not fit), and, if the engineer still wants it, turns their idea into a new plan version they approve by voice.

![The engineer's R38 option is applied and the effect is measured against the forecast](docs/media/gif-operator-option-r38.gif)

*Measured, not claimed: R17 89.8 → 50.2 °C after the move, beating the simulator's forecast by 2.0 °C. Every rack the plan touched is labelled with its before and after.*

**4. It defends every call when asked.** Ask "why was NBT-22930 held?" and a CREAO Finance Explainer answers with the contract clause, meter reading and vendor-master record it used, or says plainly when the evidence does not support the decision.

---

## What it does

| Seat | What GRIDPILOT does on its own | What it never does alone |
|---|---|---|
| **On-call infrastructure engineer** | Detects thermal anomalies, diagnoses with CREAO agents, simulates options, applies auto-allowed fixes, validates, rolls back, writes the post-incident summary | Move customer workloads, spend over $100, touch a protected SLA, take cooling out of service |
| **Reliability engineer** | Predicts fan-bearing, UPS, chiller and PDU wear with remaining-useful-life bands, opens predictive work orders, plans pre-positioning around the maintenance window | Take an asset out of service |
| **AP analyst** | Three-way matches every invoice (contract × meter × invoice), applies credits from months ago, drafts disputes, approves clean invoices under $250 | Approve above its limit, pay a held invoice, change vendor bank details |
| **Cost controller** | Shows each plan's cost against the burst budget before approval, accrues on execution, reconciles the vendor invoice at closeout, runs payment batches | Approve a plan or a payment it requested |
| **Energy manager** | Dispatches the site battery carbon-aware (solar-dip charging, evening-ramp discharge), reports CO₂ avoided and water saved against a no-battery counterfactual | Drop below a 40% ride-through reserve |

![Control room: 512 GPUs, live thermal field, incident panel with the CREAO specialist team](docs/media/01-control-room.png)

---

## Proof it works

**What is real, what is simulated, and how we know.**

| Real | Simulated (and labelled so in the UI) |
|---|---|
| CREAO agents created and run through the CREAO API; every run, tool call and result stored | 64 racks, 512 GPUs, 4 cooling units (2D thermal/airflow model, not CFD) |
| Cartesia Managed Agent, webhook tools, provisioned number, real outbound phone calls | Customers, vendors, invoices, bank feed and company history (March to October 2026) |
| Deterministic policy engine and approval verification | Planted invoice errors, with a separate answer key |
| Git-backed infrastructure config: candidates, diffs, validation, rollbacks | Grid carbon and water intensity curves |
| Double-entry ledger, three-way match, controls, payment runs, audit trail | |
| SpacetimeDB maincloud database streaming facility state at 1 Hz | |

**Answer key and tests** (`python tests/test_finance_desk.py`, `python tests/test_incident_flow.py`, both pass on the current commit):
- **9/9** invoices decided correctly, **5/5** planted errors caught, **0** false flags on clean invoices.
- 12/12 control checks: approval limits, segregation of duties, held bank-detail change, callback required before release, override required to pay a disputed line, ledger refuses an unbalanced entry, March credit memo applied in October, price variance short-paid at contract, payment run never pays a held invoice, trial balance balances.
- Incident engine end to end offline: detection → plan → refusal without approval → approval → git commit → recovery → invoice reconciled (a $9.00 variance explained by the transfer record). Add `fail` to arm a post-apply fault and watch the rollback.

**Live system counters (from the running deployment, 25 Oct 2026):**
- **8 incidents** detected and closed end to end; mean detect → CREAO plan **5 m 41 s**; INC-049 resolved in 688 s (simulated clock).
- **89 CREAO runs** (86 completed) across **23 CREAO agents** that GRIDPILOT created through the API, **1,212 audited agent tool calls**, **2,318 audit events**.
- **8 Cartesia calls** placed to the on-call engineer, **5 plan approvals given by voice**; 13 human approvals in total, **0 actions executed without the approval policy required**.
- **Finance agreement:** the CREAO AP Analyst agreed with the deterministic matcher on **82 of 88** decisions; every disagreement raised a controller alert instead of acting.
- **Predictive maintenance:** 380 predictions scored, **96.6%** of outcomes inside the 90% RUL band, C04 bearing failure called **791 s** ahead, 0 unpredicted failures. Offline backtest over 9 seeded wear cases: band coverage 80 to 93%, no false alarms.
- **Measured impact:** INC-045 (voice-approved) took R17 to 64.3 °C and its invoice reconciled to the cent ($92.40).

![Agent Operations: the INC-049 loop, every option the agent simulated and why it lost](docs/media/06-agent-operations.png)

---

## How we built it

```
Mana user_div (React · three.js twin · ECharts · React Flow)
   │  WebSocket state patches + REST            ▲ subscribes (read-only)
   ▼                                            │
aiohttp backend (Mana sandbox daemon + ingress) ──publish_tick 1 Hz──► SpacetimeDB maincloud/gridpilot
   ├─ sim.py / twin.py / gpus.py   simulator, digital twin, 512-GPU telemetry
   ├─ engine.py                    incident state machine, plans, approvals, execution, rollback
   ├─ policy.py                    auto-allowed / approval required / forbidden
   ├─ configstore.py               git-backed YAML infrastructure config
   ├─ finance.py, finance_desk.py  rate cards, ledger, AP inbox, three-way match, controls
   ├─ predict.py, reliability.py   RUL prediction, predictive work orders
   ├─ specialists.py, ghost.py     CREAO agents on demand, Mana Ghost Architect
   └─ integrations.py, voice.py    CREAO and Cartesia adapters (keys injected server-side)
```

- **Agents propose, the backend disposes.** No model output is trusted. Every action passes through the deterministic policy engine, the approval is bound to one exact plan version, and the backend re-checks the version, the approval window and live telemetry before it commits to git.
- **Least privilege.** Each CREAO run gets a scoped tool token: specialists can read and simulate but never propose or execute. The AP seat can approve only clean matches under $250.
- **Accountable.** Every tool call, approval (with the approver's words, for voice), commit and journal entry is in the audit trail, and the Agent Operations tab turns any incident, work order or invoice into a readable trace.

---

## Hyphenate track: an autonomous finance corner

**Company:** Atlas Compute, a GPU cloud. **Corner:** accounts payable plus cost control for the vendors operations depends on.

- **Sources that disagree:** the contract (price), GRIDPILOT's meter (quantity), the vendor invoice, the vendor master (bank details) and the bank feed. The desk states which source wins and why: contract beats invoice on price, meter beats invoice on quantity, vendor master beats an email on bank details.
- **Time:** a March overbilling dispute left credit memo CM-0311 open; in October the desk applies it to the earliest eligible invoice (ACX-88213 paid $46.20).
- **Working with others:** the controller approves, holds, disputes or overrides from the inbox (now split into *New · to approve*, *On hold · verify first* and *Disputes*; approved invoices move to their own tab). Releasing a hold requires a recorded callback; paying a disputed line requires a written override reason.
- **Limits:** approval limits, segregation of duties (requester ≠ approver), held bank changes, a ledger that refuses unbalanced entries.
- **Explainable and accountable:** every decision cites its evidence, and the CREAO Finance Explainer defends or concedes when questioned.
- **Bring your own scenario:** judges can submit their own invoice with "+ Test invoice" and watch the matcher, the AP Analyst agent and the controls handle it live.

## CREAO track

- **Core agent:** `GRIDPILOT Remediation Agent`, ID **`b01e1b42-3b5a-44a4-ad3a-4b5736622db8`**. It does the core work: diagnose, plan, replan after failure, execute approved plans, write the post-incident summary.
- **Example run:** `50315dd0-e715-4511-bab5-7c5fa1be637a` (INC-049 diagnose and plan: 8 evaluated options, proposal PLN-049 v1). Conversation `47a80d8e-4cba-401c-834d-23ff3bca181a`. **Share link: TODO, paste from agent.creao.ai.**
- **Agents created on demand:** when a case needs expertise the playbook lacks, GRIDPILOT creates a new CREAO agent through the API (Thermal Forensics, Capacity Placement, Change Management, FinOps Invoice Auditor, SLA Guardian, Reliability, AP Analyst, Finance Explainer). 23 agents so far, each with a scoped tool token.
- **Specialist team on every incident:** thermal forensics and capacity planning start when an incident opens; a change-management reviewer reviews each plan version that needs approval.

## Cartesia track: the phone call is the interface

At 3 a.m. the engineer has a phone, not a laptop. Voice is how GRIDPILOT gets a decision.

- **Cartesia features used:** Managed Agents (`/v1/agents`), webhook tools (`/v1/agents/tools`, 7 tools into GRIDPILOT: `get_incident_context`, `evaluate_alternative`, `adopt_alternative`, `get_cost_comparison`, `approve_plan`, `reject_plan`, `escalate_incident`), dynamic variables (`incident_id`, `call_token`, `operator_name`), provisioned phone number, outbound calls (`/agents/calls`), call status and transcript polling, the `end_call` system tool.
- **The flow:** policy requires a human → GRIDPILOT dials the registered, consenting on-call number → the agent briefs (cause, plan, recovery time, cost, SLA risk) → the engineer asks questions or proposes an alternative, which is simulated live on the call → the agent reads the exact plan version back and asks "Do you approve plan PLN-x version N?" → only a clear yes approves, and their exact words are stored with the approval.
- **Flexible, honest:** if the engineer names a rack, that rack is evaluated exactly. If it cannot work (no free GPUs, already hot, in the degraded aisle, forbidden by policy) the agent says why; if it can, it compares honestly with the recommendation and, on request, makes it the plan.
- **Responsible:** every user registers their own number with explicit consent (or keeps the default on-call), numbers are stored only on the backend and shown masked, at most 4 calls per hour per number, no duplicate calls while one is live, approval authority comes from GRIDPILOT's own outbound call and is re-verified by the backend.
- **Known limitations:** the provisioned number dials US numbers only; the voice agent depends on a public callback URL; transcripts arrive after the call ends.

## SpacetimeDB track: one writer, many subscribers

- **Module** `stdb/src/index.ts` on **maincloud/gridpilot**: public tables `rack`, `cooling_unit`, `facility_summary`, `thermal_field`, `incident`, `plan`, `audit_event`, `connection`; private `writer` and `admin`.
- **Real-time core:** the backend calls the `publish_tick` reducer once per second with the simulator snapshot plus incident, plan and audit changes. Every browser (and any judge's browser) renders the 3D twin, heatmap, cooling units, facility metrics and incident state straight from its subscription.
- **Access by design:** every mutating reducer checks the caller against `writer`; only the owner can grant or revoke. Anonymous clients are rejected.
- **An agent that only subscribes:** the Audit Watcher (`agents/stdb_watcher.ts`) subscribes anonymously, verifies each approval chain from the audit stream, and proves it cannot write (its write probe is rejected).
- **Honest scope:** approvals and the finance ledger still live in the backend database (mirrored into `audit_event`); moving them into reducers is the next step.

## Mana: built in Mana, and running inside it

- **Where Mana helped:** the entire product was specified, written, deployed and debugged in one Mana chat (https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot?chatId=00179287000977150b6f964a5b74736aa39). Mana ran the backend as a supervised sandbox daemon behind its ingress, kept every key server-side through its symbolic proxy, gave us a live React surface we iterated on in place, tested every change in its own browser against a throwaway copy, and synced the code to GitHub every 10 minutes with a scheduled task.
- **Mana as an agent inside the product:** the Ghost Architect is a Mana agent. Every hour (and on demand) GRIDPILOT asks a fresh Mana chat to read the system digest and propose orchestration v2: new agents, schedulers and detectors, which only dispatch after operator approval. Example Ghost Architect chat: https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot?chatId=001792923093257132f0b7faf494ca0bd5f (**TODO: set to public**). "Explain with Mana" turns any incident trace into a walkthrough for someone who was not watching.
- **The moment Mana changed what we built:** on a live call, the voice agent ignored the engineer's "move it to R38". In one debugging turn Mana read the call's tool log, found that the voice tool dropped the rack name, and shipped a fix: exact-rack evaluation, feasibility reasoning, operator-adopted plan versions and a duplicate-call guard. It was tested on a copy and the Cartesia agent re-registered, all within the same chat.
- **Bonus: it lives in Mana.** The app is a Mana page any signed-in judge can open and operate: run scenarios, approve plans, release invoices, take the voice call (after registering a phone).

![Ghost Architect: a Mana agent proposing orchestration v2](docs/media/11-ghost-architect.png)

---

## More screens

| | |
|---|---|
| ![Predictive maintenance](docs/media/gif-bearing-wear.gif) *A CRAH fan bearing wears: the 3D unit turns amber, then red, with vibration and remaining useful life on its label.* | ![Twin](docs/media/07-twin-reliability.png) *Digital twin and predictive work orders.* |
| ![Sustainability](docs/media/08-sustainability.png) *CO₂ avoided and water saved by carbon-aware battery dispatch, with the assumptions.* | ![Load](docs/media/09-load-gpus.png) *Where GPUs, power and heat sit, per rack and per GPU.* |
| ![Scenarios](docs/media/10-scenarios.png) *Scenarios, including the fraudulent cooling invoice and a compound storm.* | ![Approvals](docs/media/12-approval-center.png) *Approval center: every decision that needs a human, and why.* |

## Production path

The simulator is the only piece that would be replaced: telemetry from DCIM/BMS (Redfish, SNMP, Modbus), the scheduler from Slurm or Kubernetes, invoices from the AP inbox and the ERP, the bank feed from the bank. Policy, approvals, the git-backed config store, the ledger, the agents and the voice flow stay as they are. See the README for the full path, including SSO-backed approver identity and per-site policy.

## What we learned

- Agents are good at exploring options and explaining them; they should never be the thing that says yes. Binding approval to an exact plan version and re-checking live state before commit removed a whole class of failure.
- The most useful voice feature was not the briefing, it was letting the engineer disagree and getting a real answer back.
- Honest counterfactuals matter: our first battery policy saved money but emitted more CO₂ than no battery at all. The numbers caught it, so we fixed the policy instead of the chart.

## Built with

Python (aiohttp, numpy), React, three.js, ECharts, React Flow, SpacetimeDB (TypeScript module, maincloud), CREAO Developer Platform API, Cartesia Managed Agents, git, SQLite, Mana (sandbox, ingress, symbolic proxy, scheduled tasks, agents).
