
"""Deterministic policy engine. Never an LLM. Every action from any origin passes through evaluate_plan()."""
FAN_RANGE = (60, 130)
POWER_RANGE_W = (300, 450)
COST_APPROVAL_USD = 100.0
ALLOWED_TYPES = {"migrate_workload", "set_gpu_power_limit", "set_cooling_fan", "restart_noncritical_workload"}
FORBIDDEN_PREFIXES = ("delete", "shutdown", "firmware", "billing", "drop", "wipe")
RANK = {"AUTO_ALLOWED": 0, "APPROVAL_REQUIRED": 1, "FORBIDDEN": 2}

def evaluate_action(a, cfg, protected_customers):
    t = str(a.get("type", ""))
    if t.startswith(FORBIDDEN_PREFIXES) or t not in ALLOWED_TYPES:
        return "FORBIDDEN", "P-F1 forbidden or unknown action type '%s'" % t
    if t == "set_cooling_fan":
        u = a.get("unit"); p = a.get("fan_pct")
        if u not in cfg["cooling"]: return "FORBIDDEN", "P-F2 unknown cooling unit %s" % u
        if not isinstance(p, (int, float)) or not (FAN_RANGE[0] <= p <= FAN_RANGE[1]):
            return "FORBIDDEN", "P-F3 fan_pct %s outside allowed range %s" % (p, FAN_RANGE)
        return "AUTO_ALLOWED", "P-A1 fan speed within allowed range"
    if t == "set_gpu_power_limit":
        r = a.get("rack"); w = a.get("watts")
        if r not in cfg["racks"]: return "FORBIDDEN", "P-F2 unknown rack %s" % r
        if not isinstance(w, (int, float)) or not (POWER_RANGE_W[0] <= w <= POWER_RANGE_W[1]):
            return "FORBIDDEN", "P-F4 power limit %sW outside hardware bounds %s" % (w, POWER_RANGE_W)
        cur = cfg["racks"][r]["gpu_power_limit_w"]
        red = (cur - w) / cur
        if red > 0.10 + 1e-9: return "APPROVAL_REQUIRED", "P-R2 GPU power reduction %.0f%% exceeds 10%%" % (red * 100)
        return "AUTO_ALLOWED", "P-A2 GPU power reduction %.0f%% within 10%%" % (max(0, red) * 100)
    if t == "migrate_workload":
        wid = a.get("workload")
        if wid not in cfg["workloads"]: return "FORBIDDEN", "P-F2 unknown workload %s" % wid
        return "APPROVAL_REQUIRED", "P-R1 workload migration across racks requires human approval"
    if t == "restart_noncritical_workload":
        wid = a.get("workload")
        if wid not in cfg["workloads"]: return "FORBIDDEN", "P-F2 unknown workload %s" % wid
        if cfg["workloads"][wid]["customer"] in protected_customers:
            return "APPROVAL_REQUIRED", "P-R4 restart affects protected SLA customer"
        return "AUTO_ALLOWED", "P-A3 noncritical workload restart"

def evaluate_plan(actions, cfg, incremental_cost_usd, protected_customers):
    results = []
    for a in actions:
        d, rule = evaluate_action(a, cfg, protected_customers)
        results.append({"action": a, "decision": d, "rule": rule})
    plan_rules = []
    if incremental_cost_usd > COST_APPROVAL_USD:
        plan_rules.append({"decision": "APPROVAL_REQUIRED", "rule": "P-R3 incremental cost $%.2f exceeds $%.0f" % (incremental_cost_usd, COST_APPROVAL_USD)})
    touched = set()
    for a in actions:
        if a.get("workload") in cfg["workloads"]: touched.add(a["workload"])
        if a.get("type") == "set_gpu_power_limit" and a.get("rack") in cfg["racks"]:
            touched |= {w for w, v in cfg["workloads"].items() if v["rack"] == a["rack"]}
    prot = sorted(w for w in touched if cfg["workloads"][w]["customer"] in protected_customers)
    if prot:
        plan_rules.append({"decision": "APPROVAL_REQUIRED", "rule": "P-R4 affects protected SLA workloads: %s" % ", ".join(prot)})
    overall = "AUTO_ALLOWED"
    for r in results + plan_rules:
        if RANK[r["decision"]] > RANK[overall]: overall = r["decision"]
    if not actions: overall, plan_rules = "FORBIDDEN", plan_rules + [{"decision": "FORBIDDEN", "rule": "P-F0 empty plan"}]
    return {"decision": overall, "actions": results, "plan_rules": plan_rules}
