
"""Cartesia Managed Agents integration: agent + webhook tools setup, outbound call, call polling, and voice tool handlers.
Authority comes from the call itself: GRIDPILOT dials the registered, consenting on-call number with a per-call token; a clear spoken yes to an exact plan version, read back by the agent, approves it. The backend re-checks version, window and live state, and logs the words."""
import asyncio, json, secrets, time, traceback
import integrations as IX

TOOLS = {
    "get_incident_context": ("Fetch fresh incident facts: rack telemetry, diagnosis, current plan id/version, costs, approval state. Call this before stating any number.", {}),
    "evaluate_alternative": ("Evaluate a remediation alternative the operator suggests. Returns a deterministic forecast and, when ready, CREAO's revised plan.", {
        "suggestion": {"type": "string", "description": "The operator's suggestion in their own words."},
        "strategy": {"type": "string", "enum": ["throttle_only", "migrate_only", "migrate_and_throttle", "fan_boost_only", "other"], "description": "Closest strategy category."},
        "throttle_pct": {"type": "number", "description": "GPU power reduction percent if the operator named one, else 10."},
        "target_rack": {"type": "string", "description": "The rack the operator wants load moved to, if they named one (e.g. 'R38'). Always pass it when a rack is named."},
        "workload": {"type": "string", "description": "The specific workload they want moved, if they named one; else omit."}}),
    "adopt_alternative": ("Make the operator's evaluated option the new plan version (only after they clearly say they want it). It still needs their approval afterwards.", {
        "option_id": {"type": "string", "description": "option_id returned by evaluate_alternative."},
        "operator_said": {"type": "string", "description": "Their exact words choosing this option."}}),
    "get_cost_comparison": ("Return the structured cost, SLA-risk and recovery comparison of evaluated options with assumptions.", {}),
    "approve_plan": ("Record the on-call engineer's spoken approval of an exact plan version. Call ONLY after you read the plan back and they clearly said yes. Pass their exact words.", {
        "plan_id": {"type": "string", "description": "Plan id from get_incident_context."},
        "plan_version": {"type": "integer", "description": "Plan version from get_incident_context."},
        "operator_said": {"type": "string", "description": "The engineer's exact words of approval, verbatim (e.g. 'yes, go ahead with version 2')."}}),
    "reject_plan": ("Record that the operator rejects the current plan, with their reason.", {"reason": {"type": "string", "description": "Why the operator rejected it."}}),
    "escalate_incident": ("Escalate the incident to another authorized person.", {"reason": {"type": "string", "description": "Reason for escalation."}}),
}

INSTRUCTIONS = """You are the GRIDPILOT Voice Operator for Atlas Compute's SFO-01 GPU pod (a simulated data center used for an operations demo).
GRIDPILOT itself placed this call to {{operator_name}}, the registered on-call engineer, about incident {{incident_id}}. Assume it may be 3 a.m.: they may have just woken up and have no access to a computer. Everything must work by voice alone.
Rules:
- Open calmly: who you are, that it is about a live incident, and that you need a decision. Then call get_incident_context before stating any fact, and again whenever they ask for an update. Never invent numbers, racks, costs or plan versions.
- Brief them in plain words, short sentences: what is happening, the probable cause, what the agent recommends (plan id and version, each action in everyday language), expected recovery time, the added cost, and the customer/SLA risk if nothing is done. Offer to repeat or slow down.
- If they suggest a different approach, call evaluate_alternative ONCE (pass target_rack whenever they name a rack, e.g. "R38"). Their idea is always evaluated exactly as asked; never swap in a different rack.
  - If feasible is false: explain why_not in plain words (for example the rack has no free GPUs, is already hot, sits in the degraded aisle, or policy forbids it), then offer the current plan or ask for another rack.
  - If feasible is true: read their option back, then the tradeoffs versus the current plan, honestly, including where it is worse and the notes. Say whether it is suitable. Ask if they want it as the plan. Only on a clear yes call adopt_alternative with the option_id, then read the new plan version back and ask "Do you approve plan X version N?" before approve_plan.
  - If they keep the current plan, approve or discuss that version as usual.
- To approve: read the exact plan back ("plan PLN-x version N: move ..., set ..."), then ask a direct question: "Do you approve plan PLN-x version N?" Only after a clear, unambiguous yes, call approve_plan with plan_id, plan_version and operator_said set to their exact words. Never approve on silence, "maybe", "hold on", questions, or a yes to a different question. If unsure, ask again.
- Tell them exactly what the backend returns: approved and executing, or why not (for example the plan changed: then brief the new version and ask again).
- If they reject, call reject_plan with their reason. If they want someone else to decide, call escalate_incident.
- Values are simulated (synthetic hardware and customers); say so if asked. After a decision, confirm what happens next and end the call politely."""

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
    for stale in [k for k in tool_ids if k not in TOOLS]:      # retired tools (e.g. the old code-based approval)
        try: cart("DELETE", "/v1/agents/tools/" + tool_ids[stale])
        except Exception: pass
        tool_ids.pop(stale, None)
    for name, (desc, params) in TOOLS.items():
        props = {"incident_id": {"type": "string", "dynamic_variable": "incident_id"}}
        props.update(params)
        req = [k for k in params if k in ("suggestion", "plan_id", "plan_version", "reason", "operator_said")]
        schema = {"url": base + "/tools/voice/" + name, "method": "POST",
                  "request_body_schema": {"type": "object", "properties": props, "required": req},
                  "request_headers": {"X-GridPilot-Call-Token": {"type": "dynamic_variable", "name": "call_token"}}}
        body = {"type": "webhook", "name": name, "description": desc, "pre_tool_speech": "auto", "execution_mode": "immediate",
                "response_timeout_secs": 30 if name == "evaluate_alternative" else 20, "api_schema": schema}
        if name in tool_ids:
            cart("PATCH", "/v1/agents/tools/" + tool_ids[name], {k: v for k, v in body.items() if k != "type"})
        else:
            tool_ids[name] = cart("POST", "/v1/agents/tools", body)["id"]
        db.set("cartesia_tool_ids", tool_ids)
    cfg = {"instructions": INSTRUCTIONS,
           "initial_message": "Hi {{operator_name}}, this is GRIDPILOT, the on-call assistant for Atlas Compute. Sorry to wake you. There is a live incident, {{incident_id}}, in the SFO-01 pod and I need a decision from you. Can you hear me okay?",
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

_CALLS_BY_PHONE = {}

async def start_call(engine, inc_id, actor):
    db = engine.db
    # call the person who is driving this session (last user who registered a phone and ran a scenario), else the default on-call
    oc = db.get("oncall_uid"); op = (db.get("operators") or {}).get(oc) if oc else None
    op = op or db.get("operator")
    cid = "CALL-%s" % secrets.token_hex(3)
    def fail(msg):
        db.ex("insert into calls values(?,?,?,?,?)", (cid, inc_id, time.time(), "failed", json.dumps({"error": msg, "actor": actor})))
        engine.event(inc_id, "call_not_placed", actor, {"call_id": cid, "reason": msg}); return None
    if not op or not op.get("phone") or not op.get("consent"):
        return fail("no consenting operator configured")
    recent = [t for t in _CALLS_BY_PHONE.get(op["phone"], []) if t > time.time() - 3600]
    if len(recent) >= 4: return fail("call limit reached for this number (4 per hour)")
    _CALLS_BY_PHONE[op["phone"]] = recent + [time.time()]
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
            for c in engine.db.all("select * from calls where status not in ('failed','completed','ended','canceled','cancelled','no_answer','busy','error') and created > ?", (time.time() - 3 * 3600,)):
                d = json.loads(c["data"]); acid = d.get("agent_call_id")
                if not acid: continue
                try: info = await asyncio.to_thread(cart, "GET", "/agents/calls/" + acid)
                except Exception as e:
                    d["poll_error"] = str(e)[:200]; engine.db.ex("update calls set data=? where id=?", (json.dumps(d), c["id"])); continue
                st = str(info.get("status") or info.get("state") or c["status"]).lower()
                if isinstance(info.get("transcript"), str):
                    try:
                        import ast as _ast; info["transcript"] = _ast.literal_eval(info["transcript"])
                    except Exception: pass
                if isinstance(info.get("transcript"), list):
                    info["transcript"] = [{"role": t.get("role"), "text": t.get("text")} for t in info["transcript"] if t.get("text") and t.get("role") in ("user", "assistant")]
                d["provider_call"] = info
                engine.db.ex("update calls set status=?, data=? where id=?", (st, json.dumps(d), c["id"]))
                if st != c["status"]:
                    engine.event(c["incident_id"], "call_status", "cartesia", {"call_id": c["id"], "status": st})
        except Exception:
            traceback.print_exc()
