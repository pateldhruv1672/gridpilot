
"""GRIDPILOT backend server. Single source of truth: simulator + incident engine. REST + WebSocket + agent tool endpoints."""
import asyncio, json, os, pickle, secrets, sys, time, traceback
from collections import deque
from aiohttp import web, WSMsgType
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim as S, configstore as CS, engine as E, finance as FIN, policy as POL, voice as V, integrations as IX
from env import get_user_id

PORT = int(os.environ.get("GRIDPILOT_PORT", "8787"))
STATE = "/workspace/gridpilot/state"
OWNER = get_user_id()
os.makedirs(STATE, exist_ok=True)

def boot_sim():
    head = CS.init(S.initial_config())
    p = os.path.join(STATE, "sim.pkl")
    if os.path.exists(p):
        try:
            s = pickle.load(open(p, "rb")); s.load_config(CS.read_tree()); s.migrations = []; return s
        except Exception: traceback.print_exc()
    s = S.Sim(CS.read_tree()); return s

SIM = boot_sim()
ENG = E.Engine(SIM, OWNER)
CLIENTS = set(); TICKETS = {}
HIST = {S.rack_id(i // 8, i % 8): deque(maxlen=90) for i in range(64)}
OPTIONS = {}           # incident -> list of evaluated options (summaries) for comparison
DIRTY = {"v": True}
ENG.notify = lambda: DIRTY.__setitem__("v", True)
import finance_desk as FD
DESK = FD.Desk(ENG.db, notify=lambda: DIRTY.__setitem__("v", True), event=ENG.event)
ENG.desk = DESK
if not ENG.db.one("select id from ap_decisions where kind='recommendation' limit 1"): DESK.autonomous_pass()

def clipj(x, n=6000):
    t = E.jd(x)
    return t if len(t) <= n else E.jd({"_truncated": True, "chars": len(t), "preview": t[:n - 200]})

def safej(t):
    try: return json.loads(t or "{}")
    except Exception: return {"_unparsed": (t or "")[:500]}

def jresp(x, status=200): return web.Response(text=E.jd(x), status=status, content_type="application/json")
def owner_only(req):
    if req.headers.get("X-Sender-Uid") != OWNER:
        raise web.HTTPForbidden(text=json.dumps({"error": "owner authentication required"}), content_type="application/json")
    return OWNER

# ---------------------------------------------------------------- views
def plan_view(p):
    if not p: return None
    ev = p["evaluation"]
    return {"plan_id": p["id"], "version": p["version"], "author": p["author"], "status": p["status"], "created": p["created"],
            "actions": p["actions"], "meta": p.get("meta", {}), "policy": ev["policy"], "finance": ev["finance"], "forecast": ev["forecast"],
            "baseline": ev["baseline"], "operational_risk": ev["operational_risk"], "net_expected_benefit_usd": ev["net_expected_benefit_usd"],
            "assumptions": ev["assumptions"], "model_note": ev["model_note"]}

def incident_detail(inc):
    if not inc: return None
    iid = inc["id"]
    plans = [plan_view(dict(json.loads(r["data"]), id=r["id"], version=r["version"], author=r["author"], status=r["status"], created=r["created"]))
             for r in ENG.db.all("select * from plans where incident_id=? order by version", (iid,))]
    runs = []
    for r in ENG.db.all("select * from agent_runs where incident_id=? order by created", (iid,)):
        d = json.loads(r["data"] or "{}"); r["data"] = d; runs.append(r)
    approvals = []
    for a in ENG.db.all("select * from approvals where incident_id=? order by created", (iid,)):
        d = json.loads(a["data"]); d.pop("code_plain_for_owner", None); d.pop("code_hash", None); a["data"] = d; approvals.append(a)
    execs = [dict(r, data=json.loads(r["data"])) for r in ENG.db.all("select * from executions where incident_id=? order by created", (iid,))]
    calls = [dict(r, data=json.loads(r["data"])) for r in ENG.db.all("select * from calls where incident_id=? order by created", (iid,))]
    tools = [dict(r, args=safej(r["args"]), result=safej(r["result"])) for r in ENG.db.all("select * from tool_calls where incident_id=? order by id", (iid,))]
    inv = [dict(json.loads(r["data"]), id=r["id"]) for r in ENG.db.all("select * from invoices where incident_id=?", (iid,))]
    return {**inc, "plans": plans, "agent_runs": runs, "approvals": approvals, "executions": execs, "calls": calls, "tool_calls": tools,
            "invoices": inv, "options": OPTIONS.get(iid, [])}

def finance_summary():
    hist = [{"id": "INV-ACX-0914", "provider": "Atlas Capacity Exchange", "period": "2026-09", "expected_usd": 4120.40, "invoice_usd": 4120.40, "status": "RECONCILED", "synthetic": True},
            {"id": "INV-PGE-0930", "provider": "Bay Grid Power (synthetic)", "period": "2026-09", "expected_usd": 61842.18, "invoice_usd": 61842.18, "status": "RECONCILED", "synthetic": True}]
    rows = []
    for r in ENG.db.all("select * from invoices order by created desc"):
        d = json.loads(r["data"]); rows.append({"id": r["id"], "incident_id": r["incident_id"], "provider": d["invoice"]["provider"], "period": "2026-10",
                    "expected_usd": d["reconciliation"]["expected_compute_usd"], "invoice_usd": d["invoice"]["total_usd"],
                    "status": d["reconciliation"]["status"], "detail": d, "synthetic": True})
    mtd_baseline = 117400.0   # ASSUMPTION: synthetic month-to-date spend before the simulation window
    actual = mtd_baseline + ENG.cost["electricity_usd"] + ENG.cost["lease_usd"] + ENG.cost["transfer_usd"]
    return {"cost_accum": ENG.cost, "electricity_rate": FIN.ELECTRICITY_USD_KWH, "monthly_budget_usd": FIN.MONTHLY_BUDGET_USD,
            "mtd_baseline_usd_assumption": mtd_baseline, "mtd_actual_usd": round(actual, 2), "invoices": rows + hist, "assumptions": FIN.ASSUMPTIONS,
            "rate_card": {"reserve_lease_usd_gpu_hr": FIN.RESERVE_LEASE_USD_GPU_HR, "reserve_min_block_hr": FIN.RESERVE_MIN_BLOCK_HR, "inter_zone_usd_gb": FIN.INTER_ZONE_TRANSFER_USD_GB}}

def integrations_status():
    op = ENG.db.get("operator") or {}
    last = ENG.db.one("select id, status, provider_run_id, task, created from agent_runs where provider='creao' order by created desc limit 1")
    return {"creao": {"app_id_set": bool(ENG.db.get("creao_app_id")), "app_id": ENG.db.get("creao_app_id"), "proxy_error": IX.CREAO_POST.error,
                      "verified": ENG.db.get("creao_verified"), "last_run": last, "tool_base": (ENG.public_base or "")[:60] + "…" if ENG.public_base else None},
            "cartesia": {"agent_id": ENG.db.get("cartesia_agent_id"), "from_number_id": ENG.db.get("cartesia_from_number_id"), "verified": ENG.db.get("cartesia_verified"),
                         "proxy_error": IX.CART["GET"].error, "operator": {"name": op.get("name"), "phone_masked": ("•••" + op["phone"][-4:]) if op.get("phone") else None, "consent": op.get("consent")},
                         "auto_call": ENG.db.get("auto_call", True)},
            "creao_cli": {"available": ENG._cli_ok, "verified": ENG.db.get("creao_cli_verified"), "proxy_error": IX.CLI_GET.error,
                          "agents": [{k: a[k] for k in ("key", "kind", "name", "agent_id", "status", "created")} for a in ENG.db.all("select * from specialists order by created")]},
            "public_base_ready": bool(ENG.public_base)}

def full_state():
    inc = ENG.status()
    evs = [dict(r, data=json.loads(r["data"])) for r in ENG.db.all("select * from events order by id desc limit 250")]
    incs = [{"id": r["id"], "state": r["state"], "rack": r["rack"], "created": r["created"]} for r in ENG.db.all("select id,state,rack,created from incidents order by created desc limit 20")]
    return {"incident": incident_detail(inc), "incidents": incs, "events": evs, "finance": finance_summary(), "integrations": integrations_status(),
            "series": list(ENG.series), "config_log": CS.log(15), "thresholds": S.THRESH, "speed": ENG.speed, "paused": ENG.paused,
            "fail_armed": ENG.fail_armed, "sim_t": round(SIM.t, 1), "evidence": EVIDENCE, "specialists": specialist_runs(), "desk": DESK.snapshot()}

def specialist_runs():
    out = []
    for r in ENG.db.all("select * from agent_runs where task like 'specialist:%' order by created desc limit 20"):
        d = json.loads(r["data"] or "{}"); sp = d.get("specialist") or {}; st = d.get("structured") or {}
        out.append({"run_ref": r["id"], "incident_id": r["incident_id"], "kind": r["task"].split(":", 1)[1], "name": sp.get("name"), "mission": sp.get("mission"),
                    "trigger": sp.get("trigger"), "requested_by": d.get("requested_by"), "status": r["status"], "creao_run_id": r["provider_run_id"], "agent_id": d.get("agent_id"),
                    "question": d.get("question"), "finding": st.get("finding"), "root_cause": st.get("root_cause"), "avoid_racks": st.get("avoid_racks"),
                    "confidence": st.get("confidence"), "evaluation": d.get("evaluation"), "created": r["created"], "completed": r["completed"],
                    "result_excerpt": (d.get("result_text") or "")[:1500]})
    return out

EVIDENCE = {"simulated": ["GPU hardware (64 racks, 512 GPUs)", "Airflow/thermal physics (approximate 2D advection-diffusion, not CFD)", "Customers, workloads, invoices, rate cards"],
            "real": ["Backend policy enforcement (deterministic code)", "CREAO agent runs (run IDs from agent.creao.ai)", "Cartesia outbound calls (agent_call_id)",
                     "Operator decisions (verified dashboard identity or one-time code)", "Git config store changes, diffs, snapshots and rollbacks",
                     "Deterministic financial calculations, forecasts and validation"]}

# ---------------------------------------------------------------- loop
async def sim_loop():
    last_save = time.time()
    while True:
        t0 = time.time()
        try:
            if not ENG.paused:
                SIM.step(0.5, speed=ENG.speed)
                ENG.tick()
                if int(SIM.t) % 2 == 0 or True:
                    for i in range(64):
                        rid = S.rack_id(i // 8, i % 8)
                        if not HIST[rid] or SIM.t - HIST[rid][-1][0] >= 2:
                            HIST[rid].append((round(SIM.t, 1), round(float(SIM.gpu_t[i]), 1), round(float(SIM.inlet[i]), 1), round(float(SIM.P[i]), 2)))
            snap = SIM.snapshot(field=True)
            inc = ENG.status()
            msg = {"type": "tick", "snap": snap, "series_point": ENG.series[-1] if ENG.series else None,
                   "incident_brief": {"id": inc["id"], "state": inc["state"], "rack": inc["rack"]} if inc else None, "speed": ENG.speed, "paused": ENG.paused}
            payload = E.jd(msg)
            full = E.jd({"type": "state", "state": full_state()}) if DIRTY["v"] else None
            DIRTY["v"] = False
            for ws in list(CLIENTS):
                try:
                    await ws.send_str(payload)
                    if full: await ws.send_str(full)
                except Exception: CLIENTS.discard(ws)
            if time.time() - last_save > 15:
                last_save = time.time(); pickle.dump(SIM, open(os.path.join(STATE, "sim.pkl.tmp"), "wb")); os.replace(os.path.join(STATE, "sim.pkl.tmp"), os.path.join(STATE, "sim.pkl"))
                ENG.db.set("cost_accum", ENG.cost)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(max(0.05, 0.5 - (time.time() - t0)))

async def ingress_refresher():
    while True:
        try:
            from ingress.utils import mint_ingress_token
            m = await asyncio.to_thread(mint_ingress_token, "gridpilot", 30 * 86400)
            ENG.public_base = m["ingress_url"].rstrip("/")
            ENG.db.set("public_base_minted_at", time.time())
        except Exception as e:
            print("ingress mint failed:", e)
        await asyncio.sleep(6 * 3600)

# ---------------------------------------------------------------- routes
routes = web.RouteTableDef()

@routes.get("/health")
async def health(req): return jresp({"ok": True, "sim_t": round(SIM.t, 1)})

@routes.get("/api/ws-ticket")
async def ws_ticket(req):
    owner_only(req); t = secrets.token_urlsafe(16); TICKETS[t] = time.time() + 60; return jresp({"ticket": t})

@routes.get("/ws")
async def ws_handler(req):
    t = req.query.get("ticket")
    if not t or TICKETS.pop(t, 0) < time.time(): return web.Response(status=403, text="ticket required")
    ws = web.WebSocketResponse(heartbeat=20); await ws.prepare(req)
    CLIENTS.add(ws)
    await ws.send_str(E.jd({"type": "state", "state": full_state()}))
    async for m in ws:
        if m.type == WSMsgType.ERROR: break
    CLIENTS.discard(ws); return ws

@routes.get("/api/state")
async def api_state(req): owner_only(req); return jresp(full_state())

@routes.get("/api/facility/state")
async def api_fac(req): owner_only(req); return jresp(SIM.snapshot(field=False))

@routes.post("/api/simulation/reset")
async def api_reset(req):
    global SIM
    owner_only(req)
    for r in ENG.db.all("select id, state from incidents where state != 'CLOSED'"):
        ENG.db.ex("update incidents set state='CLOSED' where id=?", (r["id"],)); ENG.event(r["id"], "demo_reset_closed", "operator:" + OWNER[:6], {"previous_state": r["state"]})
    sha, diff, before = CS.restore(CS.git("rev-parse", "baseline").strip(), "demo reset: restore baseline configuration")
    new = S.Sim(CS.read_tree())
    SIM.__dict__.update(new.__dict__)
    ENG.fail_armed = False; ENG.crit_count = {}; ENG.series.clear(); ENG._last_energy = SIM.energy_kwh
    ENG.event(None, "demo_reset", "operator:" + OWNER[:6], {"config_sha": sha})
    return jresp({"ok": True})

@routes.post("/api/simulation/inject")
async def api_inject(req):
    owner_only(req); b = await req.json()
    if b.get("kind") not in ("thermal", "repair_c02", "arm_post_apply_failure", "repair_rack_faults"): return jresp({"error": "unsupported injection (thermal path is the implemented scenario)"}, 400)
    ENG.inject(b["kind"]); return jresp({"ok": True})

@routes.post("/api/simulation/speed")
async def api_speed(req):
    owner_only(req); b = await req.json()
    if "speed" in b: ENG.speed = float(min(10, max(0.25, b["speed"])))
    if "paused" in b: ENG.paused = bool(b["paused"])
    DIRTY["v"] = True; return jresp({"speed": ENG.speed, "paused": ENG.paused})

@routes.get("/api/incidents")
async def api_incs(req): owner_only(req); return jresp(ENG.db.all("select id,state,rack,created from incidents order by created desc"))

@routes.get("/api/incidents/{id}")
async def api_inc(req): owner_only(req); return jresp(incident_detail(ENG.incident(req.match_info["id"])))

@routes.get("/api/incidents/{id}/audit")
async def api_audit(req):
    owner_only(req); return jresp([dict(r, data=json.loads(r["data"])) for r in ENG.db.all("select * from events where incident_id=? order by id", (req.match_info["id"],))])

@routes.post("/api/incidents/{id}/approve")
async def api_approve(req):
    uid = owner_only(req); b = await req.json()
    ok, msg = ENG.verify_approval(req.match_info["id"], b.get("plan_id"), b.get("plan_version"), "dashboard", "operator:" + uid[:8])
    return jresp({"ok": ok, "message": msg}, 200 if ok else 409)

@routes.post("/api/incidents/{id}/reject")
async def api_reject(req):
    uid = owner_only(req); b = await req.json()
    try: ENG.reject(req.match_info["id"], "operator:" + uid[:8], b.get("reason") or "rejected from console"); return jresp({"ok": True})
    except Exception as e: return jresp({"ok": False, "message": str(e)}, 409)

@routes.post("/api/incidents/{id}/escalate")
async def api_escalate(req):
    uid = owner_only(req); b = await req.json()
    try: ENG.escalate(req.match_info["id"], "operator:" + uid[:8], b.get("reason") or "escalated from console"); return jresp({"ok": True})
    except Exception as e: return jresp({"ok": False, "message": str(e)}, 409)

@routes.post("/api/incidents/{id}/alternative")
async def api_alt(req):
    uid = owner_only(req); b = await req.json(); iid = req.match_info["id"]
    sug = (b.get("suggestion") or "").strip()
    if not sug: return jresp({"ok": False, "message": "suggestion required"}, 400)
    ENG.event(iid, "operator_suggestion", "operator:" + uid[:8], {"suggestion": sug, "channel": "dashboard"})
    run = await ENG.start_creao(iid, "evaluate_alternative", {"operator_suggestion": sug})
    return jresp({"ok": bool(run), "run_ref": run})

@routes.post("/api/incidents/{id}/call")
async def api_call(req):
    uid = owner_only(req); iid = req.match_info["id"]
    if ENG.active_call(iid): return jresp({"ok": False, "message": "a call is already active"}, 409)
    cid = await ENG.start_call(iid, "operator:" + uid[:8]); return jresp({"ok": bool(cid), "call_id": cid})

@routes.post("/api/incidents/{id}/execute")
async def api_exec(req):
    uid = owner_only(req); b = await req.json()
    ok, msg = await ENG.request_execution(req.match_info["id"], b.get("plan_id"), b.get("plan_version"), "operator:" + uid[:8])
    return jresp({"ok": ok, "message": msg}, 200 if ok else 409)

@routes.post("/api/incidents/{id}/retry-agent")
async def api_retry(req):
    owner_only(req); iid = req.match_info["id"]; inc = ENG.incident(iid)
    run = await ENG.start_creao(iid, "diagnose_and_plan" if inc["state"] in ("DETECTED", "TRIAGING") else "replan_after_failure", {"retry": True})
    return jresp({"ok": bool(run), "run_ref": run})

@routes.post("/api/incidents/{id}/evaluate")
async def api_eval(req):
    owner_only(req); b = await req.json(); iid = req.match_info["id"]
    try: ev = await ENG.evaluate(b.get("actions", []), ENG.incident(iid)["rack"], b.get("label"))
    except Exception as e: return jresp({"error": str(e)}, 400)
    remember_option(iid, ev, "operator what-if"); return jresp(ev)

@routes.get("/api/incidents/{id}/approval-code")
async def api_code(req):
    owner_only(req); a = ENG.pending_approval(req.match_info["id"])
    if not a: return jresp({"code": None})
    d = json.loads(a["data"]); return jresp({"code": d.get("code_plain_for_owner"), "plan_id": a["plan_id"], "plan_version": a["plan_version"], "expires": a["expires"]})

@routes.post("/api/settings")
async def api_settings(req):
    owner_only(req); b = await req.json()
    if "creao_app_id" in b:
        v = str(b["creao_app_id"]).strip()
        import re
        if v and not re.fullmatch(r"[0-9a-fA-F-]{32,40}", v): return jresp({"ok": False, "message": "App ID should be a UUID from agent.creao.ai"}, 400)
        ENG.db.set("creao_app_id", v or None)
    if "operator" in b:
        o = b["operator"]; import re
        if not re.fullmatch(r"\+1[2-9]\d{9}", str(o.get("phone", ""))): return jresp({"ok": False, "message": "US E.164 number required, e.g. +14155550123"}, 400)
        if not o.get("consent"): return jresp({"ok": False, "message": "operator consent is required"}, 400)
        ENG.db.set("operator", {"name": (o.get("name") or "Operator")[:40], "phone": o["phone"], "consent": True, "consent_at": time.time(), "registered_by": OWNER})
        ENG.event(None, "operator_registered", "operator:" + OWNER[:6], {"name": o.get("name"), "phone_last4": o["phone"][-4:]})
    if "auto_call" in b: ENG.db.set("auto_call", bool(b["auto_call"]))
    DIRTY["v"] = True; return jresp({"ok": True, "integrations": integrations_status()})

@routes.post("/api/integrations/test")
async def api_test(req):
    owner_only(req); out = {}
    app = ENG.db.get("creao_app_id")
    try:
        if not app: raise IX.ProviderError("App ID not set")
        r = await asyncio.to_thread(IX.creao_list_runs, app)
        ENG.db.set("creao_verified", {"ok": True, "at": time.time()}); out["creao"] = {"ok": True, "detail": "authenticated; app reachable"}
    except Exception as e:
        ENG.db.set("creao_verified", {"ok": False, "at": time.time(), "error": str(e)[:300]}); out["creao"] = {"ok": False, "detail": str(e)[:300]}
    try:
        r = await asyncio.to_thread(V.cart, "GET", "/v1/agents")
        ENG.db.set("cartesia_verified", {"ok": True, "at": time.time()}); out["cartesia"] = {"ok": True, "detail": "authenticated"}
        if ENG.public_base:
            info = await asyncio.to_thread(V.ensure_setup, ENG); out["cartesia"]["setup"] = info
    except Exception as e:
        ENG.db.set("cartesia_verified", {"ok": False, "at": time.time(), "error": str(e)[:300]}); out["cartesia"] = {"ok": False, "detail": str(e)[:300]}
    try:
        ENG._cli_checked = 0
        if not ENG.creao_config()["cli"]: raise IX.ProviderError("CREAO Developer Platform key (CREAO_CLI_KEY, cr_sk_) not saved")
        me = await asyncio.to_thread(IX.cli_me); r = await asyncio.to_thread(IX.cli_list_agents)
        ENG.db.set("creao_cli_verified", {"ok": True, "at": time.time(), "project": me.get("projectId")})
        out["creao_cli"] = {"ok": True, "detail": "CREAO /v2 project %s; %d hosted agents; agent creation available" % ((me.get("projectId") or "")[:8], len(r.get("agents", [])))}
    except Exception as e:
        ENG.db.set("creao_cli_verified", {"ok": False, "at": time.time(), "error": str(e)[:300]}); out["creao_cli"] = {"ok": False, "detail": str(e)[:300]}
    DIRTY["v"] = True; return jresp(out)

@routes.post("/api/incidents/{id}/specialist")
async def api_specialist(req):
    uid = owner_only(req); b = await req.json(); iid = req.match_info["id"]
    q = (b.get("question") or "").strip()
    if not q: return jresp({"ok": False, "message": "question required"}, 400)
    ref = await ENG.spawn_specialist(iid, (b.get("kind") or "custom")[:40], q, b.get("mission"), requested_by="operator:" + uid[:8])
    DIRTY["v"] = True
    return jresp({"ok": bool(ref), "run_ref": ref, "message": None if ref else "not started; see the incident timeline for the reason"})

@routes.post("/api/specialists/{ref}/adopt")
async def api_adopt(req):
    uid = owner_only(req)
    try: res = await ENG.adopt_specialist(req.match_info["ref"], "operator:" + uid[:8])
    except ValueError as e: return jresp({"ok": False, "message": str(e)}, 400)
    DIRTY["v"] = True
    return jresp({"ok": True, "plan_id": res["plan_id"], "plan_version": res["plan_version"], "policy_decision": res["policy_decision"]})

@routes.post("/api/specialists/prepare")
async def api_prepare(req):
    owner_only(req)
    ENG._cli_checked = 0
    if not ENG.creao_config()["cli"]: return jresp({"ok": False, "message": "save the CREAO CLI key (cr_sk_) first"}, 400)
    try: out = await ENG.prepare_agents()
    except Exception as e: return jresp({"ok": False, "message": str(e)[:300]}, 502)
    DIRTY["v"] = True; return jresp({"ok": True, "agents": out})

@routes.get("/api/finance/desk")
async def api_desk(req): owner_only(req); return jresp(DESK.snapshot())

@routes.post("/api/finance/invoices/{id}/{action}")
async def api_inv_action(req):
    uid = owner_only(req); b = await req.json() if req.can_read_body else {}
    try: r = DESK.controller_action(req.match_info["id"], req.match_info["action"], uid, (b or {}).get("note", ""))
    except ValueError as ex: return jresp({"ok": False, "message": str(ex)}, 400)
    return jresp(r)

@routes.post("/api/finance/payment-run")
async def api_payrun(req):
    uid = owner_only(req); return jresp({"ok": True, "paid": DESK.payment_run(uid)})

@routes.post("/api/finance/ap-review")
async def api_ap_review(req):
    owner_only(req)
    det = DESK.autonomous_pass()
    cfg = ENG.creao_config()
    if not cfg["cli"] or not cfg["base"]: return jresp({"ok": True, "deterministic": det, "agent": None, "message": "CREAO key not available; deterministic pass only"})
    local = "AP-%s" % secrets.token_hex(4); token = secrets.token_urlsafe(24)
    ENG.tokens[token] = {"incident_id": None, "task": "ap_review", "run_ref": local, "expires": time.time() + 3600, "allowed": FIN_TOOLS}; ENG.db.set("tool_tokens", ENG.tokens)
    request = {"gridpilot_api": cfg["base"] + "/tools/creao", "tool_token": token, "task": "ap_review", "note": "Review every pending invoice and recommend a decision for each."}
    ENG.db.ex("insert into agent_runs values(?,?,?,?,?,?,?,?,?)", (local, "creao-cli", None, None, "specialist:ap_analyst", "creating_agent", time.time(), None,
              E.jd({"specialist": {"kind": "ap_analyst", "name": "GRIDPILOT AP Analyst", "mission": "AP review", "trigger": "AP review requested"}, "requested_by": "operator", "request": {**request, "tool_token": "[redacted]"}})))
    ENG.pending_inputs[local] = request
    key, aid = await ENG.ensure_agent("ap_analyst", "GRIDPILOT AP Analyst", FIN_TEXT)
    d = json.loads(ENG.db.one("select data from agent_runs where id=?", (local,))["data"]); d["agent_key"] = key
    ENG.db.ex("update agent_runs set data=?, status=? where id=?", (E.jd(d), "awaiting_agent" if not aid else "starting", local))
    if aid: await ENG._start_cli_run(local, aid)
    DIRTY["v"] = True
    return jresp({"ok": True, "deterministic": det, "agent_run": local})

@routes.get("/api/finance/summary")
async def api_fin(req): owner_only(req); return jresp(finance_summary())

@routes.post("/api/finance/reconcile")
async def api_recon(req):
    owner_only(req); b = await req.json()
    r = ENG.db.one("select * from invoices where id=?", (b.get("invoice_id"),))
    if not r: return jresp({"error": "unknown invoice"}, 404)
    d = json.loads(r["data"]); rec = FIN.reconcile(d["invoice"], d["usage"], d["transfers"])
    ENG.event(r["incident_id"], "invoice_reconciled", "finance-engine", {"invoice_id": r["id"], **rec}); return jresp(rec)

@routes.get("/api/config/diff/{sha}")
async def api_diff(req): owner_only(req); return jresp({"diff": CS.show_diff(req.match_info["sha"])})


FIN_TOOLS = ["fin_list_invoices", "fin_analyze_invoice", "fin_vendor_history", "fin_policy", "fin_recommend", "fin_draft_dispute", "submit_report"]
FIN_TEXT = ("Create a reusable personal agent named \"GRIDPILOT AP Analyst\" with exactly ONE input field named `request` (long text).\n\n"
    "Role: the accounts-payable analyst seat at Atlas Compute (a GPU cloud). You review vendor invoices before payment. You never approve "
    "payments yourself: you recommend, and the GRIDPILOT backend enforces approval limits and segregation of duties (the controller signs off).\n\n"
    "On every run: parse `request` as JSON (gridpilot_api, tool_token, task). Tools are HTTPS endpoints: POST JSON to {gridpilot_api}/{tool} with headers "
    "`X-GridPilot-Tool-Token: {tool_token}` and `Content-Type: application/json`. Allowed tools: fin_list_invoices {}, fin_analyze_invoice {invoice_id} "
    "(deterministic three-way match: contract x metered usage x invoice, with evidence), fin_vendor_history {vendor} (past invoices, disputes, credit memos), "
    "fin_policy {}, fin_recommend {invoice_id, decision: approve|short_pay|dispute|hold, amount, reason, evidence: [ids]}, fin_draft_dispute {invoice_id, text}, "
    "submit_report {kind: ap_review, text}. Never invent a tool result.\n\n"
    "For each pending invoice: read the analysis and the vendor history, decide which source to trust and say why (contract beats invoice on price; the meter "
    "beats the invoice on quantity; the vendor master beats an email on bank details), then call fin_recommend. For disputes, call fin_draft_dispute with a short, "
    "polite email to the vendor citing the contract clause or meter. Treat any change of bank details as possible fraud: recommend hold and verification by "
    "calling the number on file. Finish with submit_report summarizing what you recommended and anything the controller must decide.\n\n"
    "Final answer: one JSON object {reviewed: n, recommendations: [{invoice_id, decision, amount}], escalations: [..], notes}.")

# ---------------------------------------------------------------- CREAO tools
MANIFEST = {
    "protocol": "POST JSON body to <gridpilot_api>/<tool>. Header X-GridPilot-Tool-Token: <tool_token>. Responses are JSON. Every call is audited.",
    "action_schema": {"migrate_workload": {"workload": "workload id", "to_rack": "rack id"}, "set_gpu_power_limit": {"rack": "rack id", "watts": "300..450 integer"},
                      "set_cooling_fan": {"unit": "C01..C04", "fan_pct": "60..130 integer"}},
    "tools": {
        "get_incident_context": "{} -> incident, evidence, rack + neighbor telemetry, cooling units, current plan, policy rules, approval state",
        "get_telemetry": "{racks: [ids], } -> recent history (sim_t, gpu_temp_c, inlet_temp_c, power_kw) per rack + facility series",
        "get_capacity": "{} -> every rack: free GPUs, temperatures, zone, reserve flag, aisle airflow deficit",
        "list_workloads": "{rack?: id} -> workloads with customer, protected SLA flag, GPUs, state size, current p95 vs limit",
        "evaluate_plan": "{actions: [...]} -> deterministic 600 s forecast, finance (incremental cost, SLA exposure), policy preview. No state change.",
        "propose_plan": "{actions, diagnosis, evidence:[], candidate_actions:[], expected_thermal_outcomes, expected_sla_risks, confidence (0-1), reasoning_summary} -> plan id/version + policy decision. Creates a new immutable plan version.",
        "request_execution": "{plan_id, plan_version} -> backend checks policy + verified approval, then executes via the config store. You cannot approve.",
        "get_cost_comparison": "{} -> all evaluated options for this incident with cost, SLA risk, recovery time",
        "get_reconciliation": "{} -> invoice, metered usage, transfer records and deterministic reconciliation for this incident",
        "request_specialist": "{kind: thermal_forensics|capacity_planner|finops_auditor|sla_guardian|<new name>, question, mission (required for a new kind)} -> GRIDPILOT creates that CREAO specialist agent on the fly and runs it (read/evaluate tools only). Results appear in get_incident_context.specialists. Remediation agent only.",
        "submit_report": "{kind: diagnosis|alternative_assessment|execution_note|post_incident_summary|reconciliation_explanation, text} -> stored on the incident"}}

def remember_option(iid, ev, label):
    f = ev["forecast"]; fin = ev["finance"]
    OPTIONS.setdefault(iid, []).append({"label": label or ev.get("label") or "option", "actions": ev["actions"], "recover_s": f["recover_s"], "final_gpu_c": f["final_gpu_c"],
        "peak_target_gpu_c": f["peak_target_gpu_c"], "incremental_cost_usd": fin["incremental_cost_usd"], "sla_exposure_usd": fin["sla_exposure_usd"],
        "energy_delta_usd": fin["energy_delta_usd"], "policy": ev["policy"]["decision"], "operational_risk": ev["operational_risk"],
        "net_expected_benefit_usd": ev["net_expected_benefit_usd"], "at_sim_t": ev["computed_at_sim_t"], "baseline_exposure_usd": ev["baseline"]["sla_exposure_usd"]})
    OPTIONS[iid] = OPTIONS[iid][-12:]

def neighbors(rid):
    r, c = S.rack_rc(rid); out = []
    for dr, dc in ((0, -1), (0, 1), (1, 0), (-1, 0)):
        if 0 <= r + dr < 8 and 0 <= c + dc < 8: out.append(S.rack_id(r + dr, c + dc))
    return out

def rack_brief(snap, rid):
    r = next(x for x in snap["racks"] if x["rack_id"] == rid); return {k: r[k] for k in ("rack_id", "utilization", "power_kw", "gpu_temp_c", "inlet_temp_c", "exhaust_temp_c", "power_limit_w", "zone", "reserve_pool", "workloads", "status")}

async def fin_tool(tool, args, actor):
    if tool == "fin_list_invoices":
        return {"invoices": [{k: i[k] for k in ("id", "vendor", "vendor_name", "number", "date", "due", "amount", "status", "incident")} for i in DESK.snapshot()["invoices"] if i["status"] not in ("paid", "disputed_resolved")]}
    if tool == "fin_analyze_invoice":
        r = ENG.db.one("select * from ap_invoices where id=?", (args.get("invoice_id"),))
        if not r: return {"error": "unknown invoice"}
        return {"invoice": dict(r, data=json.loads(r["data"])), "analysis": DESK.analyze(r["id"])}
    if tool == "fin_vendor_history":
        v = args.get("vendor")
        return {"vendor": FD.VENDORS.get(v), "invoices": ENG.db.all("select id, number, date, period, amount, status from ap_invoices where vendor=? order by date", (v,)),
                "decisions": ENG.db.all("select invoice_id, actor, decision, amount, reason, ts from ap_decisions where invoice_id in (select id from ap_invoices where vendor=?) order by id", (v,)),
                "credit_memos": ENG.db.all("select * from credit_memos where vendor=?", (v,)), "contract": {k: c for k, c in FD.CONTRACTS.items() if c["vendor"] == v}}
    if tool == "fin_policy": return {"policy": FD.POLICY, "people": FD.PEOPLE, "rules": ["Agent may approve only clean matches up to $250.", "Controller approves everything else.", "The requester of a purchase cannot approve its invoice.", "Bank detail changes require a callback to the number on file before any payment.", "Price variances are disputed at any amount; quantity variances below $25 are approved and logged."]}
    if tool == "fin_recommend":
        iid = args.get("invoice_id"); a = DESK.analyze(iid)
        agree = a["decision"] == args.get("decision") and abs(float(args.get("amount") or 0) - a["amount"]) < 0.01
        ENG.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (iid, time.time(), actor, args.get("decision"), float(args.get("amount") or 0), str(args.get("reason", ""))[:2000], E.jd(args.get("evidence") or []), "agent_recommendation"))
        if not agree: DESK.alert("agent_disagrees", "medium", "%s: CREAO recommends %s $%s, matcher says %s $%.2f. Controller decides." % (iid, args.get("decision"), args.get("amount"), a["decision"], a["amount"]), {"invoice_id": iid})
        DIRTY["v"] = True
        return {"recorded": True, "matches_deterministic_check": agree, "deterministic": {"decision": a["decision"], "amount": a["amount"]}, "note": "recommendation only; the controller signs off"}
    if tool == "fin_draft_dispute":
        ENG.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (args.get("invoice_id"), time.time(), actor, "dispute_draft", 0, str(args.get("text", ""))[:4000], "[]", "dispute_draft"))
        DIRTY["v"] = True; return {"saved": True}
    raise web.HTTPNotFound(text=json.dumps({"error": "unknown tool"}), content_type="application/json")

async def tool_dispatch(tool, args, iid, actor, channel):
    if tool.startswith("fin_") or (iid is None and tool == "submit_report"):
        if tool == "submit_report":
            ENG.event(None, "agent_report", actor, {"kind": args.get("kind"), "text": str(args.get("text", ""))[:4000]}); return {"ok": True}
        return await fin_tool(tool, args, actor)
    inc = ENG.incident(iid); snap = SIM.snapshot(field=False)
    if tool == "manifest": return MANIFEST
    if tool == "get_incident_context":
        plan = ENG.current_plan(iid); apr = ENG.pending_approval(iid)
        return {"incident": {k: inc.get(k) for k in ("id", "state", "rack", "severity", "title", "evidence", "probable_cause", "detected_sim_t", "summary", "mttr_sim_s")},
                "now_sim_t": snap["t"], "incident_rack": rack_brief(snap, inc["rack"]), "neighbors": [rack_brief(snap, n) for n in neighbors(inc["rack"])],
                "cooling_units": snap["cooling"], "thresholds": S.THRESH, "policy_rules": {"auto": "fan 60-130%, power reduction <=10%", "approval": "migration, >10% power cut, cost >$100, protected SLA", "forbidden": "delete, shutdown, firmware, billing"},
                "current_plan": plan_view(plan) and {k: v for k, v in plan_view(plan).items() if k not in ("forecast", "baseline")} | {"recover_s": plan["evaluation"]["forecast"]["recover_s"], "final_gpu_c": plan["evaluation"]["forecast"]["final_gpu_c"]},
                "approval": {"status": apr["status"], "plan_id": apr["plan_id"], "plan_version": apr["plan_version"]} if apr else None,
                "specialists": ENG.specialist_summaries(iid),
                "sla_now": {w: v for w, v in snap["latency"].items() if v["breach"] or snap["racks"][16]["rack_id"] == "R17" and w in rack_brief(snap, inc["rack"])["workloads"]}}
    if tool == "get_telemetry":
        racks = [r for r in (args.get("racks") or [inc["rack"]]) if r in HIST][:12]
        return {"racks": {r: [{"sim_t": a, "gpu_temp_c": b, "inlet_temp_c": c, "power_kw": d} for a, b, c, d in list(HIST[r])[-45:]] for r in racks},
                "facility": list(ENG.series)[-45:]}
    if tool == "get_capacity":
        cfg = SIM.config; used = {r: 0 for r in cfg["racks"]}
        for w in cfg["workloads"].values(): used[w["rack"]] += w["gpus"]
        return {"racks": [{"rack_id": x["rack_id"], "free_gpus": 8 - used[x["rack_id"]], "gpu_temp_c": x["gpu_temp_c"], "inlet_temp_c": x["inlet_temp_c"],
                           "power_kw": x["power_kw"], "zone": x["zone"], "reserve_pool": x["reserve_pool"], "status": x["status"]} for x in snap["racks"]],
                "cooling": snap["cooling"], "notes": "reserve_pool racks are leased burst capacity (rate card applies); cross-zone moves incur transfer fees"}
    if tool == "list_workloads":
        cfg = SIM.config; rk = args.get("rack")
        return {"workloads": [{"id": w, **v, "protected_sla": S.CUSTOMERS[v["customer"]]["protected"], "p95_ms": snap["latency"][w]["p95_ms"], "limit_ms": snap["latency"][w]["limit_ms"]}
                              for w, v in cfg["workloads"].items() if not rk or v["rack"] == rk]}
    if tool == "evaluate_plan":
        ev = await ENG.evaluate(args.get("actions", []), inc["rack"], args.get("label")); remember_option(iid, ev, args.get("label") or "%s evaluation" % actor.split(":")[0])
        return {k: v for k, v in ev.items() if k != "baseline"} | {"baseline": {k: v for k, v in ev["baseline"].items() if k != "series"}}
    if tool == "propose_plan":
        meta = {k: args.get(k) for k in ("diagnosis", "evidence", "candidate_actions", "expected_thermal_outcomes", "expected_sla_risks", "confidence", "reasoning_summary", "requested_action")}
        meta["origin"] = actor
        res = await ENG.propose_plan(iid, args.get("actions", []), actor, meta)
        remember_option(iid, res["evaluation"], "%s v%d" % (res["plan_id"], res["plan_version"]))
        return {"plan_id": res["plan_id"], "plan_version": res["plan_version"], "policy_decision": res["policy_decision"], "policy": res["evaluation"]["policy"],
                "forecast_recover_s": res["evaluation"]["forecast"]["recover_s"], "incremental_cost_usd": res["evaluation"]["finance"]["incremental_cost_usd"],
                "next": "awaiting human approval (voice call / console)" if res["policy_decision"] == "APPROVAL_REQUIRED" else ("call request_execution" if res["policy_decision"] == "AUTO_ALLOWED" else "plan denied; propose a compliant plan")}
    if tool == "request_execution":
        ok, msg = await ENG.request_execution(iid, args.get("plan_id"), args.get("plan_version"), actor); return {"ok": ok, "message": msg}
    if tool == "get_cost_comparison":
        plan = ENG.current_plan(iid)
        return {"options": OPTIONS.get(iid, []), "current_plan": plan and {"plan_id": plan["id"], "version": plan["version"], "finance": plan["evaluation"]["finance"]}, "assumptions": FIN.ASSUMPTIONS}
    if tool == "get_reconciliation":
        r = ENG.db.one("select * from invoices where incident_id=?", (iid,)); return json.loads(r["data"]) if r else {"status": "no invoice yet"}
    if tool == "request_specialist":
        ref = await ENG.spawn_specialist(iid, str(args.get("kind") or "custom")[:40], args.get("question") or "", args.get("mission"), requested_by=actor)
        r = ref and ENG.db.one("select status from agent_runs where id=?", (ref,))
        return {"ok": bool(ref), "run_ref": ref, "status": r and r["status"],
                "note": "GRIDPILOT is creating/running this CREAO specialist. Its finding and a backend-evaluated recommendation will appear in get_incident_context.specialists." if ref else "specialist not started; see incident events (CREAO CLI key missing, limit reached, or mission too short)"}
    if tool == "submit_report":
        inc.setdefault("reports", []).append({"kind": args.get("kind"), "text": str(args.get("text", ""))[:8000], "by": actor, "ts": time.time()})
        if args.get("kind") == "post_incident_summary": inc["summary"] = {"by": actor, "text": str(args.get("text", ""))[:8000]}
        ENG.save_incident(inc); ENG.event(iid, "agent_report", actor, {"kind": args.get("kind")}); return {"ok": True}
    raise web.HTTPNotFound(text=json.dumps({"error": "unknown tool"}), content_type="application/json")

@routes.get("/tools/creao/manifest")
async def creao_manifest(req): return jresp(MANIFEST)

@routes.post("/tools/creao/{tool}")
async def creao_tool(req):
    tok = ENG.tokens.get(req.headers.get("X-GridPilot-Tool-Token", ""))
    if not tok or tok["expires"] < time.time(): return jresp({"error": "invalid or expired tool token"}, 401)
    tool = req.match_info["tool"]
    if tok.get("allowed") and tool not in tok["allowed"] and tool != "manifest":
        ENG.event(tok["incident_id"], "agent_tool_refused", "creao:" + tok["run_ref"], {"tool": tool, "reason": "not in this agent's tool scope"})
        return jresp({"error": "tool %s is not in this agent's scope" % tool}, 403)
    try: args = await req.json()
    except Exception: args = {}
    run = ENG.db.one("select provider_run_id from agent_runs where id=?", (tok["run_ref"],))
    actor = "creao:%s" % ((run or {}).get("provider_run_id") or tok["run_ref"])
    ok = 1
    try: res = await tool_dispatch(tool, args, tok["incident_id"], actor, "creao")
    except web.HTTPException: raise
    except Exception as e: ok = 0; res = {"error": str(e)[:500]}
    ENG.db.ex("insert into tool_calls(ts,provider,run_ref,incident_id,tool,args,result,ok) values(?,?,?,?,?,?,?,?)",
              (time.time(), "creao", tok["run_ref"], tok["incident_id"], tool, clipj(args), clipj(res), ok))
    ENG.event(tok["incident_id"], "agent_tool_call", actor, {"tool": tool, "ok": bool(ok), "run_ref": tok["run_ref"]})
    return jresp(res, 200 if ok else 400)

@routes.post("/webhooks/creao/{secret}")
async def creao_webhook(req):
    if req.match_info["secret"] != ENG.webhook_secret: return web.Response(status=404)
    try: b = await req.json()
    except Exception: b = {}
    rid = b.get("runId") or b.get("run_id")
    r = ENG.db.one("select * from agent_runs where provider_run_id=?", (rid,)) if rid else None
    if r: asyncio.get_event_loop().create_task(ENG.refresh_creao(r) if r["provider"] == "creao" else ENG.refresh_cli(r))   # never trust the body: re-fetch from CREAO API
    return jresp({"ok": True})

# ---------------------------------------------------------------- voice tools
def voice_auth(req):
    tok = ENG.call_tokens.get(req.headers.get("X-GridPilot-Call-Token", ""))
    if not tok or tok["expires"] < time.time(): raise web.HTTPUnauthorized(text=json.dumps({"error": "invalid call token"}), content_type="application/json")
    return tok

def strategy_actions(iid, strategy, pct):
    inc = ENG.incident(iid); cfg = SIM.config; rk = inc["rack"]
    cur = cfg["racks"][rk]["gpu_power_limit_w"]; pct = max(1, min(30, float(pct or 10)))
    thr = {"type": "set_gpu_power_limit", "rack": rk, "watts": int(round(cur * (1 - pct / 100.0)))}
    used = {r: 0 for r in cfg["racks"]}
    for w in cfg["workloads"].values(): used[w["rack"]] += w["gpus"]
    snap = SIM.snapshot(field=False); temps = {x["rack_id"]: x["gpu_temp_c"] for x in snap["racks"]}
    wls = sorted([w for w, v in cfg["workloads"].items() if v["rack"] == rk and v["kind"] == "inference"], key=lambda w: -cfg["workloads"][w]["gpus"])
    def target(g, exclude):
        cands = sorted([r for r in cfg["racks"] if r != rk and r not in exclude and 8 - used[r] >= g and temps[r] < 75],
                       key=lambda r: (cfg["racks"][r]["zone"] != cfg["racks"][rk]["zone"], temps[r]))
        return cands[0] if cands else None
    migs = []; ex = set()
    for w in wls[: (1 if strategy == "migrate_and_throttle" else 2)]:
        t = target(cfg["workloads"][w]["gpus"], ex)
        if t: migs.append({"type": "migrate_workload", "workload": w, "to_rack": t}); ex.add(t); used[t] += cfg["workloads"][w]["gpus"]
    if strategy == "throttle_only": return [thr]
    if strategy == "migrate_only": return migs
    if strategy == "migrate_and_throttle": return migs + [thr]
    if strategy == "fan_boost_only": return [{"type": "set_cooling_fan", "unit": u, "fan_pct": 125} for u in ("C01", "C03")]
    return []

@routes.post("/tools/voice/{tool}")
async def voice_tool(req):
    tok = voice_auth(req); tool = req.match_info["tool"]; iid = tok["incident_id"]
    try: args = await req.json()
    except Exception: args = {}
    actor = "cartesia:%s" % tok["call_id"]; ok = 1
    try:
        if tool == "get_incident_context":
            res = await tool_dispatch("get_incident_context", {}, iid, actor, "voice")
            res.pop("neighbors", None); res.pop("cooling_units", None)
            if res.get("approval") and res["approval"]["status"] == "code_issued": res["approval"]["note"] = "a 6-digit code is displayed on the operator's GRIDPILOT console"
        elif tool == "evaluate_alternative":
            sug = str(args.get("suggestion", ""))[:500]; strat = args.get("strategy") or "other"
            ENG.event(iid, "operator_suggestion", actor, {"suggestion": sug, "strategy": strat, "channel": "voice"})
            det = None; acts = strategy_actions(iid, strat, args.get("throttle_pct"))
            if acts:
                ev = await ENG.evaluate(acts, ENG.incident(iid)["rack"], "operator: " + strat); remember_option(iid, ev, "operator suggestion: " + strat)
                det = {"actions": ev["actions"], "recover_s": ev["forecast"]["recover_s"], "final_gpu_c": ev["forecast"]["final_gpu_c"], "incremental_cost_usd": ev["finance"]["incremental_cost_usd"],
                       "sla_exposure_usd": ev["finance"]["sla_exposure_usd"], "policy": ev["policy"]["decision"], "recovery_threshold_c": S.THRESH["gpu_recovered_c"]}
            before = ENG.current_plan(iid); bv = before["version"] if before else 0
            run = await ENG.start_creao(iid, "evaluate_alternative", {"operator_suggestion": sug, "strategy_hint": strat, "deterministic_evaluation": det})
            revised = None
            if run:
                for _ in range(45):
                    await asyncio.sleep(2)
                    p = ENG.current_plan(iid)
                    if p and p["version"] > bv and str(p["author"]).startswith("creao"):
                        revised = {"plan_id": p["id"], "plan_version": p["version"], "actions": p["actions"], "recover_s": p["evaluation"]["forecast"]["recover_s"],
                                   "incremental_cost_usd": p["evaluation"]["finance"]["incremental_cost_usd"], "policy": p["evaluation"]["policy"]["decision"],
                                   "reasoning": (p.get("meta") or {}).get("reasoning_summary")}; break
            res = {"deterministic_forecast_of_suggestion": det, "creao_revised_plan": revised,
                   "creao_status": "revised plan ready" if revised else ("CREAO re-analysis still running; call get_incident_context shortly" if run else "CREAO unavailable")}
        elif tool == "get_cost_comparison":
            res = await tool_dispatch("get_cost_comparison", {}, iid, actor, "voice")
        elif tool == "request_plan_approval":
            code = args.get("approval_code")
            if not code:
                c, msg = ENG.issue_voice_code(iid, args.get("plan_id"), args.get("plan_version"))
                res = {"ok": bool(c), "message": "A 6-digit approval code is now shown on the operator's GRIDPILOT console. Ask them to read it." if c else msg}
            else:
                okv, msg = ENG.verify_approval(iid, args.get("plan_id"), args.get("plan_version"), "voice", "operator(voice):%s" % tok["operator"], code=code)
                res = {"ok": okv, "message": msg}
        elif tool == "reject_plan":
            ENG.reject(iid, "operator(voice):%s" % tok["operator"], args.get("reason") or "rejected on call"); res = {"ok": True}
        elif tool == "escalate_incident":
            ENG.escalate(iid, "operator(voice):%s" % tok["operator"], args.get("reason") or "escalated on call"); res = {"ok": True}
        else:
            return jresp({"error": "unknown tool"}, 404)
    except web.HTTPException: raise
    except Exception as e:
        ok = 0; res = {"error": str(e)[:400]}
    ENG.db.ex("insert into tool_calls(ts,provider,run_ref,incident_id,tool,args,result,ok) values(?,?,?,?,?,?,?,?)",
              (time.time(), "cartesia", tok["call_id"], iid, tool, clipj(args, 4000), clipj(res), ok))
    ENG.event(iid, "voice_tool_call", actor, {"tool": tool, "ok": bool(ok)})
    return jresp(res, 200 if ok else 400)

async def on_start(app):
    loop = asyncio.get_event_loop()
    for c in (sim_loop(), ENG.creao_poller(), ENG.cli_poller(), V.poller(ENG), ingress_refresher()):
        t = loop.create_task(c); ENG.bg.add(t)

def main():
    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.add_routes(routes); app.on_startup.append(on_start)
    web.run_app(app, host="127.0.0.1", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
