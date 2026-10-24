# GRIDPILOT — Autonomous AI Data Center Operations

## Master Build Specification for Mana

### 1. Product vision

Build **GRIDPILOT**, a visually stunning, interactive autonomous operations control room for a fictional GPU cloud provider named Atlas Compute.

The product simulates an enterprise AI data center with GPU racks, cooling infrastructure, airflow dynamics, electricity consumption, hosted workloads, SLAs, and financial records.

Autonomous agents should detect infrastructure anomalies, diagnose failures, evaluate remediation strategies, request human authorization when necessary, modify actual configuration files using Factory Droid, validate and apply those changes to the simulator, and reconcile resulting financial costs.

A Cartesia voice agent should make a real outbound phone call to an operator, explain the incident, answer questions, receive alternative remediation suggestions, re-evaluate the strategy, and obtain verified approval.

This is a **working simulated infrastructure environment with real autonomous workflows**, not a static dashboard or scripted slideshow.

**Primary principle:** Simulate the physical environment. Make the operational workflow, agent calls, permission checks, config changes, financial calculations, and audit logs real.

---

## 2. Visual direction — extremely high priority

The UI must resemble a premium enterprise infrastructure command center mixed with a futuristic digital twin.

Think:

- NVIDIA-style GPU infrastructure monitoring
- Modern aerospace mission control
- A high-end cloud observability platform
- Interactive thermal digital twins
- An industrial control center with cinematic lighting

Do not build a conventional SaaS dashboard with generic cards everywhere.

### Design language

- Background: deep obsidian navy `#070B13`
- Main panels: `#101826` and `#141E2D`
- Primary accent: electric cyan `#43D9F5`
- Healthy: luminous mint green `#4FE0A5`
- Warning: vivid amber `#FFBB55`
- Critical: coral red `#FF596E`
- Cool airflow: icy cyan/blue
- Hot airflow: orange/red
- Typography: Geist or Inter, with IBM Plex Mono for telemetry
- Subtle grid overlays, thin borders, atmospheric depth and precise motion
- Glowing rack edges, soft volumetric lights, subtle reflections
- Clean typography and compact operational density
- Restrained glass overlays rather than excessive glassmorphism

The center visualization should be visually dramatic but technically readable.

Target 1440×900 desktop first, with responsive fallback.

Maintain high contrast, readable labels, keyboard-accessible actions, and reduced-motion support.

---

# 3. MAIN SCREEN — LIVE DATA CENTER CONTROL ROOM

This is the landing screen and the most important part of the product.

## Layout

**Top command bar — approximately 64px**

Display:

GRIDPILOT logo | ATLAS COMPUTE | SFO-01 | SIMULATION LIVE

On the right:

- System health
- Incident severity
- Current simulation time
- Autonomous mode
- Operator identity
- Demo controls

Below the bar, show four compact live metrics:

- Total IT power and facility power
- GPU utilization
- Power Usage Effectiveness (PUE)
- Active SLA risk / estimated financial exposure

**Center-left — approximately 65% of available content width**

A massive interactive 3D/isometric data-center visualization.

**Right — approximately 35%**

Live incident command center, AI agent activity, remediation proposal and approval status.

**Bottom — expandable operations strip**

Temperature charts, power curves, airflow metrics, incident timeline, workload transfer activity and financial impact.

The 3D scene should occupy most of the visual attention.

---

# 4. THE DATA CENTER DIGITAL TWIN

## Facility geometry

Simulate 64 GPU racks arranged in eight rows of eight, representing one GPU compute pod.

Each rack contains eight simulated GPUs.

Include:

- GPU racks with individual rack IDs
- Cold aisles
- Hot aisles
- Cooling supply units
- Hot-air return paths
- Overhead cooling or power channels
- Floor grid and row labels
- Workload indicators
- Power and temperature status lighting

Visually model the rack cabinets using reusable 3D geometry.

Use React Three Fiber / Three.js where supported, with instanced geometry for performance.

Each rack should be individually clickable and respond to simulator state.

### Rack appearance

Normal:
Dark metallic enclosure, subtle cyan outline, small green status indicator.

Elevated temperature:
Amber outline with stronger thermal glow.

Critical:
Pulsating red edge lighting, animated exhaust plume, warning marker.

Recovering:
Amber-to-green transition as simulated temperatures fall.

Selected rack:
Bright outline, floating label, camera focus animation and corresponding detailed telemetry.

### Camera controls

- Orbit
- Zoom
- Pan
- Reset view
- Focus selected rack
- Top-down heatmap view
- Isometric operations view

Avoid expensive cinematic camera movements that interfere with usability.

---

# 5. ANIMATED AIRFLOW SYSTEM — SIGNATURE FEATURE

**Airflow is mandatory, not optional decoration.**

The data center needs a live, spatial visualization of cold air entering racks and hot air leaving them.

### Normal airflow

Cold cyan particles emerge from cooling supply vents.

They travel through cold aisles toward rack intake faces.

Air passes through racks and emerges as warm orange particles into hot aisles.

Hot exhaust returns toward cooling units.

Use directional particle trails, ribbon-like flows and faint volumetric effects.

### Thermal incident

When cooling unit C02 loses effectiveness:

1. Nearby cold-air streams become slower or weaker.
2. Rack inlet temperature starts increasing.
3. Hot air begins recirculating near R17.
4. Orange/red particles concentrate around the affected rack.
5. A thermal plume grows above the rack.
6. Adjacent racks show increased inlet temperature.
7. A localized temperature heatmap expands across the floor.

The failure should visually propagate through the scene instead of simply changing one rack to red.

### After remediation

When workloads are moved away from R17:

- Animated workload transfer paths appear between racks.
- R17 GPU utilization decreases.
- Power draw decreases.
- Heat generation decreases.
- The thermal plume gradually fades.
- Neighboring rack temperatures stabilize.
- The heatmap moves from red to amber to green.

Cooling recovery should not be instantaneous; it should follow the simulation's time evolution.

### Visualization modes

Provide an interactive toolbar:

**Thermal | Airflow | Power | Workload | Combined**

Thermal:
Continuous heatmap overlay around rack locations. Label the metric clearly as inlet air, exhaust air or GPU temperature.

Airflow:
Particle trajectories, airflow velocity vectors, recirculation zones and cooling supply paths.

Power:
Rack colors represent power consumption.

Workload:
Rack utilization and workload placement, with animated migration connections.

Combined:
Heatmap plus selected airflow particles and incident highlights.

Also provide:

- Airflow intensity slider
- Particle density setting
- Pause animation
- Toggle rack labels
- 2D / 3D view
- Simulation speed selector

### Important technical requirement

Airflow must be driven by the simulator's cooling and airflow state.

Do not generate random particles unrelated to system behavior.

Use a simplified 2D velocity field for the physical model and render that field inside the 3D scene. Interpolate particle positions and colors in the browser.

Support blue-to-orange particle coloration based on local simulated air temperature.

Use instanced particles / shaders if available. If WebGL performance is poor, use an animated Canvas 2D airflow layer instead.

Do not attempt full computational fluid dynamics.

Label the model as an approximate thermal/airflow simulation, not engineering-grade CFD.

---

# 6. SIMULATION ENGINE

Build a deterministic backend simulation using a fixed random seed.

Simulate:

- 64 GPU racks
- 512 GPUs
- Four cooling supply units
- Rack-level utilization and power
- GPU hotspot temperature
- Rack inlet air temperature
- Rack exhaust air temperature
- Simplified airflow/cooling efficiency
- Customer workload placement
- SLA latency and availability indicators
- Energy consumption and operating costs

Each rack maintains:

`rack_id, gpu_count, utilization, power_kw, gpu_temp_c, inlet_temp_c, exhaust_temp_c, airflow, cooling_efficiency, workloads, status`

Cooling effectiveness should influence nearby inlet-air temperatures.

Power and GPU utilization should contribute to generated heat.

Heat should spread between adjacent zones with a lightweight diffusion/advection approximation.

Hotspot temperature and inlet-air temperature must remain separate metrics.

Example normal R17:

- GPU hotspot: 72°C
- Inlet air: 24°C
- Exhaust air: 38°C
- Utilization: 82%
- Power: 9.4 kW

During incident:

- GPU hotspot: 91°C
- Inlet air: 33°C
- Exhaust air: 49°C
- Utilization: 95%
- Power: 11.1 kW

Use configurable simulated thresholds and document them as demo assumptions.

For example:

- Thermal warning: GPU hotspot above 82°C
- Thermal critical: GPU hotspot above 88°C
- SLA breach: measured simulated p95 latency exceeds customer limit
- Power alert: rack power exceeds configured safety threshold

Run simulation calculations server-side on a fixed tick, approximately 0.5–1 seconds.

Stream state updates to the UI over WebSocket or Server-Sent Events.

The frontend renders smooth motion at display frame rate through interpolation.

**The backend is the single source of truth.** Refreshing the page must not reset the incident.

---

# 7. AUTONOMOUS INCIDENT WORKFLOW

Implement the following state machine:

NORMAL → DETECTED → TRIAGING → PLAN_PROPOSED → POLICY_CHECKED

From POLICY_CHECKED:

- Safe authorized action → EXECUTING
- Approval required → AWAITING_APPROVAL
- Forbidden action → DENIED / ESCALATED

After approval:

EXECUTING → VALIDATING → MONITORING_RECOVERY

Then:

- Recovery succeeds → RESOLVED → FINANCIAL_RECONCILIATION → CLOSED
- Validation fails before application → REPLAN
- Recovery fails after application → ROLLING_BACK → REPLAN / ESCALATED

Each state transition must produce a persistent audit event.

Do not allow a model to change incident state directly without a backend validation step.

The incident should be initiated by deterministic anomaly detection rather than an LLM polling the dashboard.

Duplicate telemetry events should not create duplicate incidents or phone calls.

---

# 8. CREAO — AUTONOMOUS INCIDENT COMMANDER

Use CREAO for substantive autonomous incident analysis and coordination.

Create a real CREAO agent using the CREAO agent platform.

Trigger its saved agent through the supported API when the incident backend creates an incident.

CREAO responsibilities:

1. Read incident context and recent telemetry.
2. Diagnose likely contributing factors.
3. Evaluate candidate remediation strategies.
4. Request structured financial analysis.
5. Produce an actionable remediation proposal.
6. Explain uncertainty and tradeoffs.
7. Re-evaluate alternatives suggested by the human operator.
8. Summarize the incident once resolved.

The CREAO agent must perform real work.

Do not fake its output with predetermined chat text.

### Suggested structured result

`incident_id`

`diagnosis`

`evidence`

`candidate_actions`

`expected_thermal_outcomes`

`expected_sla_risks`

`requested_action`

`confidence`

`reasoning_summary`

`requires_approval`

The backend should validate this response before using it.

CREAO must not have unrestricted direct access to mutate the simulator's configuration or approve its own actions.

Use its documented run API and preserve real run IDs for evidence.

Store API keys server-side.

If CREAO is slow, show the actual pending state and continue displaying deterministic telemetry. Never fabricate a completed agent run.

---

# 9. FINOPS AND FINANCIAL REASONING

Create a dedicated financial analysis workflow integrated with CREAO and the backend's deterministic calculations.

Its job is to compare possible remediation strategies using:

- Incremental GPU compute cost
- Electricity cost
- Estimated SLA penalty exposure
- Cross-zone migration fees
- Hosting invoices
- Budget-versus-actual spend
- Operating cost forecasts
- Workload demand and capacity

The AI explains and compares alternatives, while the actual arithmetic is performed in code.

### Example

Thermal incident R17.

Option A:
Do nothing. High predicted SLA exposure.

Option B:
Throttle rack. Low incremental cost but slow thermal recovery and possible latency degradation.

Option C:
Migrate selected workloads. Higher incremental compute cost but lower projected SLA exposure.

Display a decision table with predicted recovery time, cost, SLA risk and operational risk.

**Financial correctness rules:**

- Projected avoided losses are estimates, not booked revenue.
- SLA exposure is conditional, not an automatically realized penalty.
- Record only actual simulated incurred costs in accounting records.
- Distinguish simulated actuals, forecast values and assumptions.
- Never treat hypothetical avoided loss as cash or profit.
- Reconciliation must explain invoice differences using underlying metering and pricing evidence.

### Reconciliation example

Expected metered compute cost: $172

Additional cross-zone transfer fee: $12

Actual synthetic provider invoice: $184

The system detects an apparent $12 difference, locates the transfer charge and explains why the invoice reconciles.

Tie every cost to incident IDs and usage records.

---

# 10. POLICY ENGINE — ENFORCE APPROVAL BOUNDARIES

The policy engine must be deterministic backend code, not an LLM prompt.

Example permissions:

**AUTO-ALLOWED**

- Increase fan speed within allowed range.
- Apply GPU throttling up to 10%.
- Restart a simulated noncritical workload under policy.
- Request telemetry and diagnosis.

**HUMAN APPROVAL REQUIRED**

- Workload migration across racks.
- More than 10% GPU power reduction.
- Actions with incremental cost exceeding $100.
- Actions affecting protected customer SLAs.

**FORBIDDEN**

- Destructive deletion.
- Rack-wide shutdown outside explicit emergency policy.
- Firmware changes.
- Unapproved billing modifications.

Every proposed action must pass authorization checks regardless of whether it originated in CREAO, Cartesia, Factory or the dashboard.

Record policy rule, decision, actor, timestamp and plan version.

The agent proposing an action must never approve it on its own behalf.

---

# 11. CARTESIA — REAL OUTBOUND INCIDENT CALL

Use Cartesia **Managed Agents**, not a pre-recorded speech file.

When the incident needs human approval:

1. Backend creates an approval request for a specific versioned plan.
2. Backend initiates an outbound phone call to a configured consenting operator.
3. Cartesia agent explains the current incident.
4. Operator can ask questions or suggest alternative strategies.
5. Cartesia accesses backend incident tools to obtain fresh information.
6. Alternative suggestions are sent to the planning workflow.
7. Updated technical and financial evaluations are returned.
8. Cartesia explains the revised plan.
9. Operator accepts, rejects or requests escalation.
10. Backend verifies operator authorization and records the exact decision.

### Essential voice tools

`get_incident_context(incident_id)`

Fetch fresh incident details.

`evaluate_alternative(incident_id, suggestion)`

Request a new remediation evaluation.

`get_cost_comparison(incident_id)`

Return structured costs, risk and assumptions.

`request_plan_approval(incident_id, plan_id, plan_version)`

Start or continue a verified approval procedure.

`escalate_incident(incident_id, reason)`

Escalate to another authorized person.

### Example real conversation

AI:
"Critical thermal anomaly on rack R17. GPU temperature is 91 degrees. I recommend moving two inference workloads to healthy racks. May I proceed?"

Human:
"Don't migrate everything. Can we throttle R17 instead?"

AI:
"I'll evaluate that approach."

AI calls the backend's evaluation tool and receives a new result.

AI:
"A ten-percent throttle reduces power consumption, but predicted temperature remains above our recovery threshold. A partial migration combined with throttling has lower SLA risk and an estimated incremental cost of $184. Would you like to approve that revised plan?"

Human:
"Approved."

The backend verifies the operator and the approval applies only to that exact plan version.

If the plan materially changes afterward, require fresh approval.

### Security

Do not treat a spoken "yes" as sufficient authorization by itself.

For the prototype, use a verified operator session with a one-time approval code or a corresponding dashboard approval confirmation.

Voice captures intent; the backend verifies authority.

No arbitrary phone dialing. Call only configured, consenting demo operators.

Support no-answer, interruption, rejection, expired approval and disconnected-call cases.

If telephony is unavailable, offer a clearly labeled Cartesia browser-conversation fallback, not a fake successful call.

Show live call state and transcript in the dashboard when legitimately available.

---

# 12. FACTORY DROID — REAL CONFIGURATION REMEDIATION

Factory Droid is the execution agent for repository-backed infrastructure configuration.

Create a local Git repository or working directory containing simulated infrastructure configuration files.

For example:

`infrastructure/racks/R17.yaml`

`infrastructure/scheduler/workloads.yaml`

`infrastructure/cooling/policies.yaml`

`infrastructure/policies/approval-rules.yaml`

Use the Factory Droid TypeScript SDK or documented headless execution from a trusted Node.js backend worker.

Factory responsibilities:

1. Inspect the relevant configuration files.
2. Review the authorized remediation plan.
3. Modify only approved configuration paths.
4. Produce an actual diff.
5. Run schema and safety validation tests.
6. Return result and execution logs.
7. Produce the candidate configuration snapshot.

The backend must then independently verify the diff against the approved plan and policy before applying it to the simulator.

Do not give Factory unrestricted permission to modify the production host.

Use a temporary sandbox or isolated Git worktree.

### Configuration diff example

Before:

`R17.gpu_power_limit_w = 450`

`inference_api_07.rack = R17`

After:

`R17.gpu_power_limit_w = 405`

`inference_api_07.rack = R14`

Show the actual resulting diff in the dashboard.

### Validation

- YAML/JSON schema
- Rack capacity
- Workload placement constraints
- GPU power boundaries
- SLA restrictions
- Cooling feasibility
- Approved plan version consistency

Only apply a validated candidate.

### Rollback

Take a configuration snapshot before application.

After application, watch real simulator outcomes.

If temperature fails to recover, another rack becomes overloaded, or a safety constraint is violated:

- Abort further migration.
- Restore the previous valid snapshot.
- Verify restoration.
- Update the simulator.
- Show the exact rollback diff.
- Record the failed attempt and its evidence.
- Ask CREAO to prepare a revised plan.

Make rollback an actual state change, not simply a green "rollback complete" message.

---

# 13. INCIDENT COMMAND PANEL

The right panel should show a beautiful, live operational workflow.

Top:

**SEV-1 · INC-042 · GPU THERMAL ANOMALY**

Below:

- Detected at
- Rack
- Current temperature
- Probable cause
- SLA risk
- Estimated financial exposure

Then an animated agent execution timeline:

DETECTED

DIAGNOSING

FINANCIAL EVALUATION

PLAN READY

AWAITING HUMAN

APPROVED

FACTORY EXECUTION

VALIDATION

RECOVERY

The current step should pulse subtly.

Completed steps should have small success indicators.

Failed steps should remain visible.

### Remediation proposal card

Show:

- Proposed action
- Source/target racks
- Expected cooling improvement
- Expected incremental cost
- Risk level
- Approval requirement
- Plan version

Buttons:

**View Diff**

**Approve**

**Reject**

**Request Alternative**

**Call Operator**

Buttons must execute real state changes and enforce permissions.

---

# 14. VOICE INCIDENT OVERLAY

When Cartesia initiates a call, display a refined floating call panel above the control room.

Show:

- Operator being contacted
- Ringing / connected / ended status
- Call duration
- Voice agent status
- Active incident ID
- Live transcript or available transcript events
- Pending approval plan version
- Verified approval state

When the operator requests a new strategy, visually animate:

**Human suggestion → CREAO re-analysis → Finance evaluation → Revised plan**

The operator's input should visibly influence the proposed remediation.

The phone UI should feel integrated with mission control, not like an unrelated chatbot.

---

# 15. FINANCIAL OPERATIONS VIEW

Create a second full-screen tab named **FinOps**.

Visual style must match the control room.

### Main financial charts

- GPU spend over simulated time
- Electricity expenditure
- Forecasted infrastructure cost
- Energy consumption
- Cost per GPU-hour
- SLA exposure
- Remediation costs
- Budget-versus-actual variance

Show incident-linked financial effects.

Use attractive line charts, stacked cost-area charts and compact metric comparisons.

### Incident economics

Selected incident:

- Alternative remediation costs
- Predicted financial exposure
- Actual simulated charges incurred
- Net expected benefit
- Confidence / assumptions
- Workload cost attribution

### Invoice reconciliation

List synthetic invoices.

Selecting an invoice opens its:

- Provider
- Metered usage
- Rate card
- Expected cost
- Invoice charge
- Variance
- Evidence
- Reconciliation status

A real finance-analysis agent should be able to explain discrepancies, but the numerical reconciliation must be deterministic.

---

# 16. AGENT ACTIVITY AND AUDIT VIEW

Create an **Agent Operations** tab.

Include three columns:

### Left: Active agents

CREAO Incident Commander

Finance Analyst

Cartesia Voice Operator

Factory Droid

Show each agent's current real state, latest run and last result.

### Center: Execution graph

Visual sequence of completed and pending operations.

Examples:

Telemetry detected anomaly

CREAO diagnosed cooling failure

Finance evaluated three alternatives

Policy blocked autonomous migration

Cartesia contacted human operator

Operator suggested throttling

CREAO produced revised strategy

Operator verified approval

Factory generated configuration changes

Tests passed

Backend applied changes

Recovery monitoring completed

### Right: Evidence inspector

For selected execution:

- Incident ID
- Input event
- Source evidence
- Agent run ID
- Action/tool call
- Plan version
- Policy rule
- Approval identity
- Config diff
- Validation output
- Outcome
- Timestamp

Never display invented tool runs or pretend simulated console text is a real agent response.

---

# 17. DEMO SCENARIO — R17 THERMAL INCIDENT

Provide a controlled demo launcher.

**Reset Demo**

**Start Thermal Incident**

**Inject SLA Breach**

**Inject Power Overload**

The thermal scenario is the required fully implemented path.

### Act 1 — Healthy data center

All racks normal.

Cyan airflow particles stream through cold aisles.

Hot air returns through exhaust aisles.

Heatmap mostly green/cyan.

GPU utilization, power and financial metrics update continuously.

### Act 2 — Cooling failure

User clicks **Inject Thermal Incident**.

Cooling unit C02 effectiveness drops.

Airflow weakens in the affected area.

Hot-air recirculation appears near R17.

GPU hotspot temperature increases to the configured critical range.

The heatmap expands around R17.

A critical incident triggers automatically.

### Act 3 — Autonomous investigation

CREAO receives incident context.

It diagnoses probable cooling degradation.

It evaluates possible actions.

Finance estimates cost and SLA exposure.

A structured remediation proposal appears.

### Act 4 — Policy escalation

Backend determines the proposed migration requires approval.

The system enters AWAITING_APPROVAL.

Cartesia calls the operator.

The operator asks about throttling instead.

CREAO evaluates that alternative and returns an updated recommendation.

Finance updates its cost comparison.

Cartesia explains the revised plan.

### Act 5 — Verified approval

The operator authorizes the revised plan.

The backend validates identity, plan version and policy.

Dashboard displays verified authorization.

### Act 6 — Real remediation

Factory Droid modifies the simulated infrastructure configuration.

Show its actual tool activity and actual diff.

Run deterministic validation.

Apply the validated configuration.

Animate workload movement from R17 to the selected healthy racks.

### Act 7 — Recovery and optional failure

The simulator updates utilization, power and temperatures.

Airflow and thermal visualization show actual gradual recovery.

Include a selectable failure-injection branch where a post-apply safety check fails, causing a genuine snapshot rollback and replanning.

### Act 8 — Financial closeout

Update actual simulated costs.

Reconcile an invoice that contains a cross-zone transfer charge.

Link the invoice, original incident, remediation and approval in the audit trail.

Show incident resolution, measured simulated MTTR, incurred remediation costs and projected SLA exposure avoided.

Label baseline comparisons clearly as assumptions.

---

# 18. IMPLEMENTATION STACK

Preferred:

**Frontend**
- Next.js / React
- TypeScript
- Tailwind CSS
- React Three Fiber
- Three.js / Drei
- Framer Motion or Motion
- Recharts for telemetry and finances
- Lucide icons

**Backend**
- Node.js / TypeScript
- REST endpoints
- WebSocket or SSE for live state
- Simulation engine
- Policy and approval engine
- Agent job orchestration
- Factory worker
- Cartesia integration

**Persistence**
- SQLite for fast local development
- Prisma or Drizzle
- Persistent incident events
- Agent runs
- Approval records
- Config snapshots
- Workloads
- Invoices
- Reconciliation findings

Keep the simulation and incident services modular.

### Suggested APIs

`POST /api/simulation/reset`

`POST /api/simulation/inject`

`GET /api/facility/state`

`GET /api/incidents`

`GET /api/incidents/:id`

`POST /api/incidents/:id/evaluate`

`POST /api/incidents/:id/approval-request`

`POST /api/incidents/:id/approve`

`POST /api/incidents/:id/reject`

`POST /api/incidents/:id/call`

`POST /api/incidents/:id/execute`

`POST /api/incidents/:id/rollback`

`GET /api/incidents/:id/audit`

`GET /api/finance/summary`

`POST /api/finance/reconcile`

`GET /api/events/stream`

These are proposed GRIDPILOT backend endpoints, not claimed sponsor API endpoints.

All sponsor API calls must follow their own official current documentation.

---

# 19. DATA CONSISTENCY REQUIREMENTS

Maintain a single persistent state per incident.

Use:

- Unique incident IDs
- Event IDs
- Plan IDs and immutable plan versions
- Operator IDs
- Agent execution IDs
- Configuration snapshot IDs
- Simulation tick timestamps
- Approval expiration times

Reject stale approval requests.

Prevent duplicate remediation execution.

Recalculate policy after every plan revision.

No frontend component may directly change authoritative simulation or approval state.

Every visible KPI must derive from a real simulator or financial data model.

No randomly changing numbers unrelated to the scenario.

---

# 20. PROGRESSIVE BUILD PLAN

Implement in this exact order.

### P0 — Visual digital twin

Build the control room, 64-rack scene, animated airflow, cooling zones, heatmap and live telemetry.

This must look exceptional.

### P1 — Physical simulation

Implement thermal and cooling dynamics, incident injection and recovery visualization.

### P2 — End-to-end incident backend

Implement event detection, persistent state machine, policy rules and audit log.

### P3 — CREAO integration

Connect an actual agent run and display its real diagnosis and recommendations.

### P4 — Factory execution

Connect Factory Droid to actual sandboxed configuration files, validate diffs and apply safe changes.

### P5 — Cartesia calling

Implement outbound phone call, incident Q&A, alternative suggestions and verified approval.

### P6 — FinOps

Implement cost comparisons, forecast updates and synthetic invoice reconciliation.

### P7 — Failure demonstration

Add reproducible rollback, denial and disconnected-operator paths.

Do not sacrifice the functioning main scenario in pursuit of optional features.

---

# 21. DEFINITION OF DONE

The finished app must demonstrate:

1. A beautiful live 3D data center with working animated airflow.
2. Cooling failure changes the actual airflow and thermal simulation.
3. The incident is triggered without a human prompting the AI.
4. CREAO performs real diagnosis/planning work.
5. Finance meaningfully influences the remediation decision.
6. Permission rules prevent unauthorized actions.
7. Cartesia makes a real call to a human operator.
8. The human can ask questions and propose alternative remediation.
9. The revised plan can be verified and approved.
10. Factory Droid performs real configuration modifications.
11. Actual changes and validation results are visible.
12. The simulator reacts to those changes.
13. Rollback is real and can be demonstrated.
14. Finance reconciles incurred synthetic costs with synthetic invoice evidence.
15. Every action is recorded in a persistent audit trail.

Add a **System Evidence** panel that clearly distinguishes:

- Simulated GPU hardware
- Simulated airflow/thermal physics
- Synthetic customers and invoices
- Real backend policy enforcement
- Real CREAO runs
- Real Factory sessions and configuration changes
- Real Cartesia calls
- Real operator decisions
- Real deterministic calculations and validation

---

# 22. CRITICAL INSTRUCTIONS TO MANA

Do not build a static mockup.

Do not make decorative buttons that have no effect.

Do not substitute a scripted timeline for live agent execution.

Do not fabricate sponsor API responses.

Do not expose API secrets in the frontend.

Do not allow the LLM to approve or execute unauthorized operations.

Do not make the airflow a generic looping particle effect disconnected from cooling state.

Do not implement full CFD.

Do not add unnecessary features before the main incident scenario works.

Use official documentation and verify exact SDK/API contracts before implementing each sponsor integration.

If a sponsor API cannot be accessed, preserve the interface, show an explicit disconnected state and make the local simulator demonstrable. Never silently claim integration success.

The intended user experience is:

**Watch the data center breathe → see heat propagate → watch AI investigate → receive a real phone call → collaborate on the plan → authorize remediation → see workloads move → watch temperatures recover → inspect the financial and audit consequences.**

The combination of visual clarity, genuine autonomous action, human control and financial accountability is the core of GRIDPILOT.

let me know which api keys are required and how to add them, do not mock actual tools behavior

---

# ARCHITECTURE UPDATE (2026-10-24, supersedes Factory sections 12, 18 Factory worker, and every Factory mention)

- Factory AI and the Factory Droid SDK are removed entirely.
- CREAO AI is the primary autonomous operations platform: diagnosis, remediation planning, financial analysis,
  operator-requested replanning, post-incident explanations. A CREAO Remediation Agent uses structured backend tools
  (HTTPS, per-run scoped capability token) to inspect simulator state, propose safe configuration changes and request
  execution after verified approval. Store CREAO run IDs and results. No fabricated execution logs.
- CREAO key is app-scoped (capi_): POST https://agent.creao.ai/api/v1/apps/{appId}/runs {inputs, webhookUrl}; poll GET .../runs/{runId}.
- Remediation execution lives in our backend: versioned configuration store (git), deterministic policy validation,
  capacity checks, real before/after diffs, simulator updates, recovery monitoring, rollback snapshots.
- Cartesia Managed Agents remain for live outbound calls, Q&A, alternatives and approval requests. All sensitive actions pass backend authorization.
- Visuals (3D twin, heatmaps, airflow, propagation, migration animations, agent panel, FinOps, audit) unchanged.
- Three components: CREAO (intelligence/coordination), Cartesia (human intervention), GRIDPILOT backend + digital twin.
- Priority: one fully functional autonomous R17 overheating incident end to end before secondary incidents.
