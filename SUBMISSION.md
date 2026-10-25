# Submission notes (CalHacks 13.0)

**Project:** GRIDPILOT  ·  **Live:** https://www.mana.space/home/NhYNhCrTGxgLBbOLkQ6QkRCEdhD3/apps/gridpilot  ·  **Repo:** https://github.com/pateldhruv1672/gridpilot

## Hyphenate: The Hyphenated Enterprise
- **Seats:** accounts-payable analyst + cost controller for a GPU cloud, signing off with the human controller.
- **The moment:** a look-alike "we changed banks" invoice, a duplicate bill, a $0.25/GPU-hour overcharge worth only $3 (below materiality, still disputed because it is a price variance), and a credit the vendor has owed since March: all caught, explained with sources, and held for the right person.
- **Proof:** 9/9 vs answer key, 5/5 planted errors, 0 false flags (`tests/test_finance_desk.py`, 12 checks). CREAO AP Analyst agreed with the deterministic matcher 6/6.
- **Defends its decisions:** "Ask why" on any invoice starts a CREAO Finance Explainer agent that reads the contract, meter, vendor history and decision log, then answers with cited sources (for example why a $3 price variance is disputed below the $25 materiality line) and says what would change the call.
- **Try your own scenario:** "+ Test invoice" submits any invoice (bank change, price creep, over-billed usage, unknown vendor or your own) and it goes through the same autonomous pass and controls live.
- **What's real:** ledger, matching, controls, payment runs, audit trail, CREAO runs. **Simulated:** vendors, invoices, bank feed, history.

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
