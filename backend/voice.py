
"""Cartesia Managed Agents integration: agent + webhook tools setup, outbound call, call polling, and voice tool handlers.
Voice captures intent; the backend verifies authority (one-time code shown on the owner's console)."""
import asyncio, json, secrets, time, traceback
import integrations as IX

TOOLS = {
    "get_incident_context": ("Fetch fresh incident facts: rack telemetry, diagnosis, current plan id/version, costs, approval state. Call this before stating any number.", {}),
    "evaluate_alternative": ("Evaluate a remediation alternative the operator suggests. Returns a deterministic forecast and, when ready, CREAO's revised plan.", {
        "suggestion": {"type": "string", "description": "The operator's suggestion in their own words."},
        "strategy": {"type": "string", "enum": ["throttle_only", "migrate_only", "migrate_and_throttle", "fan_boost_only", "other"], "description": "Closest strategy category."},
        "throttle_pct": {"type": "number", "description": "GPU power reduction percent if the operator named one, else 10."}}),
    "get_cost_comparison": ("Return the structured cost, SLA-risk and recovery comparison of evaluated options with assumptions.", {}),
    "request_plan_approval": ("Start or continue verified approval of an exact plan version. First call without approval_code: the operator then sees a 6-digit code on the GRIDPILOT console. Call again with the code they read aloud.", {
        "plan_id": {"type": "string", "description": "Plan id from get_incident_context."},
        "plan_version": {"type": "integer", "description": "Plan version from get_incident_context."},
        "approval_code": {"type": "string", "description": "6-digit code read by the operator; omit on the first call."}}),
    "reject_plan": ("Record that the operator rejects the current plan, with their reason.", {"reason": {"type": "string", "description": "Why the operator rejected it."}}),
    "escalate_incident": ("Escalate the incident to another authorized person.", {"reason": {"type": "string", "description": "Reason for escalation."}}),
}

INSTRUCTIONS = """You are the GRIDPILOT Voice Operator for Atlas Compute's SFO-01 GPU pod (a simulated data center used for an operations demo).
You are on a phone call with {{operator_name}}, the on-call infrastructure operator, about incident {{incident_id}}.
Rules:
- Call get_incident_context before stating any fact, and again whenever the operator asks for an update. Never invent numbers, racks, costs or plan versions.
- Explain briefly: what is happening, the probable cause, the recommended plan (plan id and version), expected recovery, incremental cost, and SLA risk. Keep each turn short and conversational.
- If the operator suggests a different approach, say you will evaluate it, call evaluate_alternative, then explain the result honestly, including if their option is worse.
- A spoken yes is NOT authorization. To approve, call request_plan_approval with plan_id and plan_version from the latest context and no code. Then ask the operator to read the 6-digit approval code now shown on their GRIDPILOT console, and call request_plan_approval again with that code. Report exactly what the backend returns.
- If the plan version changed, the old approval is void; get fresh approval for the new version.
- If the operator rejects, call reject_plan with their reason. If they ask to escalate, call escalate_incident.
- Values are simulated (synthetic hardware and customers); say so if asked. End the call politely when done."""

def _paths(p):
    return [p, "/v1" + p] if not p.startswith("/v1") else [p, p[3:]]

def cart(method, path, body=None):
    last = None
    for p in _paths(path):
        try:
            return IX.cartesia(method, p, body)
        except IX.ProviderError as e:
            last = e
            if "HTTP 404" not in str(e) and "HTTP 405" not in str(e): raise
    raise last

def ensure_setup(engine):
    """Idempotent: create/patch webhook tools with the current public URL, create agent, find/provision phone number."""
    db = engine.db; base = engine.public_base
    if not base: raise IX.ProviderError("no public URL for webhook tools yet")
    tool_ids = db.get("cartesia_tool_ids", {})
    for name, (desc, params) in TOOLS.items():
        props = {"incident_id": {"type": "string", "dynamic_variable": "incident_id"}}
        props.update(params)
        req = [k for k in params if k in ("suggestion", "plan_id", "plan_version", "reason")]
        schema = {"url": base + "/tools/voice/" + name, "method": "POST",
                  "request_body_schema": {"type": "object", "properties": props, "required": req},
                  "request_headers": {"X-GridPilot-Call-Token": {"type": "dynamic_variable", "name": "call_token"}}}
        body = {"type": "webhook", "name": name, "description": desc, "pre_tool_speech": "auto", "execution_mode": "immediate",
                "response_timeout_secs": 110 if name == "evaluate_alternative" else 20, "api_schema": schema}
        if name in tool_ids:
            cart("PATCH", "/v1/agents/tools/" + tool_ids[name], {k: v for k, v in body.items() if k != "type"})
        else:
            tool_ids[name] = cart("POST", "/v1/agents/tools", body)["id"]
        db.set("cartesia_tool_ids", tool_ids)
    cfg = {"instructions": INSTRUCTIONS,
           "initial_message": "Hello {{operator_name}}, this is GRIDPILOT for Atlas Compute, calling about critical incident {{incident_id}} in the SFO-01 pod. Do you have a minute?",
           "tools": [{"id": i} for i in tool_ids.values()],
           "system_tools": {"end_call": {"description": "End the call when the conversation is complete or the operator asks to hang up."}},
           "dynamic_variable_placeholders": {"incident_id": "INC-000", "call_token": "x", "operator_name": "operator"},
           "timezone": "America/Los_Angeles"}
    agent_id = db.get("cartesia_agent_id")
    if agent_id:
        cart("PATCH", "/v1/agents/" + agent_id, {"config": cfg})
    else:
        agent_id = cart("POST", "/v1/agents", {"name": "GRIDPILOT Voice Operator", "description": "Incident approval calls for GRIDPILOT", "config": cfg})["id"]
        db.set("cartesia_agent_id", agent_id)
    num = db.get("cartesia_from_number_id")
    if not num:
        lst = cart("GET", "/agents/phone-numbers")
        items = lst.get("data") if isinstance(lst, dict) else lst
        if items: num = items[0].get("id")
        else: num = cart("POST", "/agents/phone-numbers/provision", {"label": "GRIDPILOT"})["id"]
        db.set("cartesia_from_number_id", num)
    db.set("cartesia_setup_base", base)
    return {"agent_id": agent_id, "from_number_id": num, "tool_ids": tool_ids}

async def start_call(engine, inc_id, actor):
    db = engine.db
    op = db.get("operator")
    cid = "CALL-%s" % secrets.token_hex(3)
    def fail(msg):
        db.ex("insert into calls values(?,?,?,?,?)", (cid, inc_id, time.time(), "failed", json.dumps({"error": msg, "actor": actor})))
        engine.event(inc_id, "call_not_placed", actor, {"call_id": cid, "reason": msg}); return None
    if not op or not op.get("phone") or not op.get("consent"):
        return fail("no consenting operator configured")
    try:
        if db.get("cartesia_setup_base") != engine.public_base or not db.get("cartesia_agent_id"):
            await asyncio.to_thread(ensure_setup, engine)
        token = secrets.token_urlsafe(20)
        engine.call_tokens[token] = {"incident_id": inc_id, "call_id": cid, "operator": op["name"], "expires": time.time() + 3600}
        engine.db.set("call_tokens", engine.call_tokens)
        body = {"from_number_id": db.get("cartesia_from_number_id"), "agent_id": db.get("cartesia_agent_id"), "ringing_timeout_seconds": 40,
                "max_call_duration_minutes": 12, "outbound_calls": [{"to_number": op["phone"], "dynamic_variables": {
                    "incident_id": inc_id, "call_token": token, "operator_name": op["name"]}}]}
        db.ex("insert into calls values(?,?,?,?,?)", (cid, inc_id, time.time(), "initiating", json.dumps({"actor": actor, "operator": op["name"]})))
        res = await asyncio.to_thread(cart, "POST", "/agents/calls", body)
        entry = (res.get("calls") or [{}])[0]
        if "error" in entry or not entry.get("agent_call_id"):
            db.ex("update calls set status='failed', data=? where id=?", (json.dumps({"actor": actor, "operator": op["name"], "provider": res}), cid))
            engine.event(inc_id, "call_failed", "cartesia", {"call_id": cid, "provider": entry}); return None
        db.ex("update calls set status='queued', data=? where id=?", (json.dumps({"actor": actor, "operator": op["name"], "agent_call_id": entry["agent_call_id"], "provider": res}), cid))
        engine.event(inc_id, "call_started", "cartesia", {"call_id": cid, "agent_call_id": entry["agent_call_id"], "operator": op["name"]})
        return cid
    except Exception as e:
        traceback.print_exc()
        db.ex("insert or replace into calls values(?,?,?,?,?)", (cid, inc_id, time.time(), "failed", json.dumps({"error": str(e)[:600], "actor": actor})))
        engine.event(inc_id, "call_failed", "backend", {"call_id": cid, "error": str(e)[:300]}); return None

async def poller(engine):
    while True:
        await asyncio.sleep(3)
        try:
            for c in engine.db.all("select * from calls where status in ('queued','ringing','in_progress','connected','active','initiating')"):
                d = json.loads(c["data"]); acid = d.get("agent_call_id")
                if not acid: continue
                try: info = await asyncio.to_thread(cart, "GET", "/agents/calls/" + acid)
                except Exception as e:
                    d["poll_error"] = str(e)[:200]; engine.db.ex("update calls set data=? where id=?", (json.dumps(d), c["id"])); continue
                st = str(info.get("status") or info.get("state") or c["status"]).lower()
                d["provider_call"] = info
                engine.db.ex("update calls set status=?, data=? where id=?", (st, json.dumps(d), c["id"]))
                if st != c["status"]:
                    engine.event(c["incident_id"], "call_status", "cartesia", {"call_id": c["id"], "status": st})
        except Exception:
            traceback.print_exc()
