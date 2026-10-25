"""Dynamic CREAO specialist agents.

Special cases (deterministic triggers in the engine, a request from the Remediation Agent, or the owner) get a purpose-built
CREAO agent. The backend creates it through the CREAO CLI (POST /v1/agents, natural-language spec), waits for the creation run
to return an agent_id, caches it (one agent per specialist kind + brief hash, reused afterwards), then runs it with a scoped
tool token. Specialists only read and evaluate: they cannot propose, approve or execute. Their recommended actions are
re-evaluated deterministically by the backend; a qualifying recommendation becomes a normal plan version that still goes
through policy and human approval."""
import hashlib, json, re

READ_TOOLS = ["get_incident_context", "get_telemetry", "get_capacity", "list_workloads", "evaluate_plan", "get_cost_comparison",
              "get_reconciliation", "submit_report"]
MAIN_TOOLS = READ_TOOLS + ["propose_plan", "request_execution", "request_specialist"]
MAX_PER_INCIDENT = 8

CATALOG = {
    "thermal_forensics": {
        "name": "GRIDPILOT Thermal Forensics Specialist",
        "mission": "Determine the root cause of a rack hotspot, both when an incident opens and when a remediation failed after it was applied. "
                   "Separate airflow causes (cooling unit degradation, recirculation, aisle airflow deficit) from rack-level hardware causes "
                   "(fan module or heat-sink fault: a rack whose GPU-to-inlet delta is abnormal for its power). Identify racks that must be "
                   "avoided as migration targets, and recommend an action set that addresses the actual cause.",
        "trigger": "every incident at open; again after a post-apply failure and verified rollback"},
    "capacity_planner": {
        "name": "GRIDPILOT Capacity Placement Specialist",
        "mission": "Find feasible placements for the workloads that must move off a hot rack, both when an incident opens and when a plan failed "
                   "capacity, placement or cooling-feasibility validation. Use get_capacity and list_workloads to find racks with enough free GPUs, "
                   "healthy inlet temperature and a healthy aisle, prefer the same zone to avoid transfer fees, keep protected-SLA workloads on the "
                   "healthiest racks, and recommend an action set.",
        "trigger": "every incident at open; again after a validation failure before application"},
    "change_management": {
        "name": "GRIDPILOT Change Management Reviewer",
        "mission": "Review a proposed remediation plan as a change request before a human approves it. Use get_incident_context to read the plan "
                   "version awaiting approval. List every change it makes, its blast radius (racks, workloads, protected customers, power and cooling), "
                   "the pre-checks to run, the exact rollback (revert to the git config snapshot taken before apply), the verification criteria that "
                   "prove success, and which approvals policy requires. Give a verdict of GO, GO WITH CONDITIONS or NO-GO with reasons in finding. "
                   "Do not invent new infrastructure actions; leave recommended_actions empty unless the plan must change to be safe.",
        "trigger": "every plan version that needs human approval"},
    "finops_auditor": {
        "name": "GRIDPILOT FinOps Invoice Auditor",
        "mission": "Audit an incident invoice whose reconciliation did not match. Compare invoice lines with metered usage and transfer "
                   "records from get_reconciliation, attribute every dollar of variance to a specific record or line, flag lines with no "
                   "supporting record as disputable, and state the amount to dispute. Do not recommend infrastructure actions.",
        "trigger": "invoice reconciliation variance not explained"},
    "sla_guardian": {
        "name": "GRIDPILOT SLA Guardian",
        "mission": "Assess customer impact for protected-SLA workloads during an incident: current p95 latency versus limit, breach risk "
                   "under the current plan and alternatives (use evaluate_plan), and the lowest-risk action set for protected customers. "
                   "Also draft a short customer status note (text only; nothing is sent).",
        "trigger": "protected-SLA workload in breach"},
}

OUTPUT = ("Final answer: one JSON object with keys specialist, incident_id, finding, root_cause, evidence (list of strings citing tool "
          "results), avoid_racks (list), recommended_actions (list of actions, may be empty), expected_outcome, confidence (0 to 1), "
          "risks, notes.")

def spec_for(kind, mission=None):
    if kind in CATALOG: return dict(CATALOG[kind], kind=kind)
    m = re.sub(r"\s+", " ", (mission or "")).strip()[:1500]
    if len(m) < 40: raise ValueError("custom specialist needs a mission of at least 40 characters")
    slug = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-")[:32] or "custom"
    return {"kind": "custom-" + slug if not slug.startswith("custom") else slug, "name": "GRIDPILOT Specialist: " + slug.replace("-", " ").title(),
            "mission": m, "trigger": "requested on the fly"}

def creation_text(spec, tools):
    return ("Create a reusable personal agent named \"%s\" with exactly ONE input field named `request` (long text).\n\n"
            "Mission: %s\n\n"
            "On every run: parse `request` as JSON. It contains incident_id, question, gridpilot_api (a base URL), tool_token and context. "
            "Tools are HTTPS endpoints: POST JSON to {gridpilot_api}/{tool} with headers `X-GridPilot-Tool-Token: {tool_token}` and "
            "`Content-Type: application/json`; GET {gridpilot_api}/manifest lists them. Call them with Python requests or curl. "
            "Allowed tools: %s. Never invent a tool result; if a call fails, report the error. You cannot propose, approve or execute "
            "changes: the GRIDPILOT backend re-evaluates your recommendation and humans approve. Use only rack, workload and unit IDs "
            "returned by the tools. Actions: migrate_workload {workload, to_rack}, set_gpu_power_limit {rack, watts 300 to 450}, "
            "set_cooling_fan {unit, fan_pct 60 to 130}. Before finishing, call submit_report with kind diagnosis and your finding.\n\n%s"
            ) % (spec["name"], spec["mission"], ", ".join(tools), OUTPUT)

def brief_hash(spec, tools):
    return hashlib.sha256(creation_text(spec, tools).encode()).hexdigest()[:16]

def parse_result(text):
    if not text: return None, ["empty result"]
    e = text.rfind("}")
    for i in [i for i, ch in enumerate(text) if ch == "{"][:60]:
        try:
            obj = json.loads(text[i:e + 1])
            if isinstance(obj, dict):
                errs = ["missing field %s" % k for k in ("finding", "recommended_actions", "confidence") if k not in obj]
                return obj, errs
        except Exception: continue
    return None, ["no JSON object in final output"]
