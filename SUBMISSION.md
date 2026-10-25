# Submission notes (CalHacks 13.0)

**Project:** GRIDPILOT  ·  **Live:** https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot  ·  **Repo:** https://github.com/pateldhruv1672/gridpilot

## Hyphenate: The Hyphenated Enterprise
- **Seats:** accounts-payable analyst + cost controller for a GPU cloud, signing off with the human controller.
- **The moment:** a look-alike "we changed banks" invoice, a duplicate bill, a $0.25/GPU-hour overcharge worth only $3 (below materiality, still disputed because it is a price variance), and a credit the vendor has owed since March: all caught, explained with sources, and held for the right person.
- **Proof:** 9/9 vs answer key, 5/5 planted errors, 0 false flags (`tests/test_finance_desk.py`, 12 checks). CREAO AP Analyst agreed with the deterministic matcher 6/6.
- **Defends its decisions:** "Ask why" on any invoice starts a CREAO Finance Explainer agent that reads the contract, meter, vendor history and decision log, then answers with cited sources (for example why a $3 price variance is disputed below the $25 materiality line) and says what would change the call.
- **Try your own scenario:** "+ Test invoice" submits any invoice (bank change, price creep, over-billed usage, unknown vendor or your own) and it goes through the same autonomous pass and controls live.
- **What's real:** ledger, matching, controls, payment runs, audit trail, CREAO runs. **Simulated:** vendors, invoices, bank feed, history.

## Digital twin, predictive maintenance and agent operations
- **Living DCIM:** the twin models energy supply (utility feed, transformer, generator, tariff and carbon), battery storage (500 kWh BESS, two UPS strings), cooling (4 CRAHs, chiller), facility (PDUs, leak, VESDA) and IT & security (core switches, access control), coupled to the thermal simulator. All simulated; sensors are noisy and wear is hidden from the agents.
- **Predictive maintenance:** remaining-useful-life from sensor trends only (OLS on a physics-shaped degradation model, 90% prediction band, P(failure within 5/15 min)). Scored against the hidden ground truth: in the verified run the C02 bearing was flagged 1,101 s before its failure, RUL error 32 s, 98% of predictions within ±25%.
- **Reliability loop (verified live, WO-7101):** prediction → CREAO Reliability Agent evaluated 9 options under the predicted outage → plan (migrate inference_api_07 to R22, raise C01/C03/C04 fans) forecast 84.8 °C vs 91.9 °C doing nothing → operator approved → mitigations committed to git → C02 taken out for maintenance → observed pod peak 79.7 °C, no SLA breach, $4,354 conditional exposure avoided → incident prevented.
- **Autonomous value loops:** Remediation (CREAO), Reliability (CREAO), Finance (CREAO), Energy (BESS peak shaving, autonomous inside a 40% reserve), Security (always escalates to a human). Agent Operations shows the summary KPIs, the roster and an animated React Flow decision trace of every loop: signal → detection → agent run → options evaluated → chosen plan and policy → human → action → verification.

## Ghost Architect (Mana) and per-GPU telemetry
- **Ghost Architect:** a Mana agent (started from the console with `mana.ask`, and hourly by the Mana scheduler node `apps/gridpilot/app/ghost-architect`) reads a live digest of everything GRIDPILOT does (agent runs and durations, tool errors, incidents, work orders, per-GPU telemetry, finance, energy, security) and returns an orchestration-v2 blueprint: observations with evidence, plus proposed agents, schedulers, detectors and policy changes. GRIDPILOT dispatches only what the operator approves: agents become new CREAO agents with read-only ops tools, schedulers and detectors run in the backend, policy changes stay recommendations.
- **Verified run:** the first blueprint (GH-5b8988, 6 proposals) found that C02 was at 28% coil effectiveness while no incident opened (incidents only fire above 88 °C). Approving its detector and its "Cooling Degradation Sentinel" agent dispatched both; the detector fired on C02 within seconds and launched the new CREAO agent, which analysed the slopes of all 128 GPUs behind C02 and filed a finding.
- **Per-GPU telemetry:** all 512 GPUs have an owner, utilization, temperature, board power, memory, clock and throttle state (thermal or power-cap), with history. GPU fleet tab: fleet map (64 racks × 8), per-rack drill-down, utilization distribution, GPUs per workload, hottest GPUs; the 3D twin shows 8 GPU blades per rack.

## Scenarios and live agent reasoning
- **Scenarios tab:** seven runnable situations (cooling failure at peak, a fix that fails after deploy, C04 bearing wear, vendor fraud and over-billing, grid price spike, after-hours intrusion, and a compound storm running them together). Only the faults are scripted; every response comes from the real detectors, CREAO agents, policy, approvals and execution. A live board tracks all 14 participants from recorded events, with each one's latest thought and the fix it produced.
- **Agents think out loud:** every CREAO agent attaches a `thought` to each tool call; GRIDPILOT streams them live in Scenarios, Agent Operations and an overlay on the 3D view, next to a plain-English translation of each call and result.
- **Verified storm run:** within five minutes the watcher escalated the intrusion, the battery discharged through the price spike and stopped at its 40% reserve ($112 saved), the matcher held a lookalike-domain bank change and disputed an over-billed invoice, the AP Analyst made 12 recommendations, the Explainer explained the hold, the predictor flagged C04 (RUL 885 s), the detector opened INC-044 and the Remediation Agent proposed PLN-044, gated by policy for human approval.

## Cartesia
- Voice is the approval channel: a Managed Agent calls the on-call engineer, explains the incident, evaluates their alternative live through a webhook tool, and collects approval with a one-time code verified by the backend.
- Features: Managed Agents, Sonic voice, agent webhook tools with dynamic variables, phone number provisioning, outbound calls, call status/transcript polling.
- Limitations: US numbers only; approval authority is verified by the backend, never by the voice model.

## CREAO
- Hosted agents created through the CREAO API, including specialists created on the fly for special cases. Real run IDs in the README.

## Mana challenge
- Built entirely in Mana: backend as a sandbox daemon with ingress, UI as a live Mana app, keys through Mana's proxy, GitHub sync as a scheduled Mana task, CREAO and Cartesia keys never in code.
- Judges open one link; the live view updates in real time.

## Demo script (2 minutes)
1. Control Room: Demo → degrade C02. Watch R17 heat in the twin; the incident opens on its own.
2. CREAO diagnoses and proposes; show the plan card (cost, SLA exposure, policy, budget impact).
3. Phone rings (Cartesia). Ask "why not just throttle?"; the agent evaluates it live. Read the code to approve.
4. Execution: diff, validation chips, recovery curve. (Optional: arm the fault to show rollback + on-the-fly specialist.)
5. Finance desk: open ACX-88250 (bank change hold), ACX-88230 (price variance), ACX-88213 (March credit applied). Controls & proof tab: 9/9.
