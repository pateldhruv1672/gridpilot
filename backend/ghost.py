"""Ghost Architect: a Mana agent that watches all of GRIDPILOT and proposes the next version of its own agentic orchestration.

Observation is continuous and deterministic (this module keeps the digest). Analysis is done by a Mana agent (mana.ask from the
UI or from the scheduled MPM node apps/gridpilot/app/ghost-architect), which returns a JSON blueprint. The operator approves
each proposal; GRIDPILOT then really dispatches it:
  agent      -> a new CREAO agent created from a generated brief, read-only ops tools, findings go to the operator
  scheduler  -> a recurring backend job that runs a v2 agent (or the AP review) on a wall-clock interval
  detector   -> a deterministic threshold rule evaluated every tick on GPU / rack / asset / facility metrics
  policy     -> recorded as an engineering recommendation (never auto-applied: policy stays human-written code)
"""
import json, re, time, hashlib, traceback
import numpy as np

METRICS = {
    "gpu_temp_c": "per GPU temperature", "gpu_util": "per GPU utilization 0..1", "gpu_power_w": "per GPU board power",
    "rack_gpu_temp_c": "rack GPU temperature", "rack_inlet_c": "rack inlet air", "rack_power_kw": "rack power",
    "crah_vibration_mm_s": "CRAH fan vibration", "crah_effectiveness_pct": "CRAH coil effectiveness",
    "pue": "facility PUE", "grid_kw": "grid import", "bess_soc_pct": "battery state of charge", "ups_runtime_min": "UPS runtime",
    "idle_gpus": "idle GPUs in the pod", "throttled_gpus": "GPUs throttling",
}
OPS_TOOLS = ["ops_digest", "ops_gpu_fleet", "ops_events", "ops_trace", "ops_capacity", "ops_findings", "ops_submit_finding"]
MAX_V2_RUNS_PER_HOUR = 8

def slug(s): return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:40] or "agent"

def extract_json(text):
    if not text: return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cands = [m.group(1)] if m else []
    i = text.find("{"); j = text.rfind("}")
    if i >= 0 and j > i: cands.append(text[i:j + 1])
    for c in cands:
        try: return json.loads(c)
        except Exception: continue
    return None

class Ghost:
    def __init__(self, ns):
        self.ns = ns; self.db = ns["ENG"].db
        self.db.c.executescript("""
        create table if not exists ghost_runs(id text primary key, created real, source text, status text, data text);
        create table if not exists orch(id text primary key, kind text, name text, status text, created real, data text);
        create table if not exists findings(id integer primary key autoincrement, ts real, source text, severity text, title text, data text);
        """); self.db.c.commit()
        self.det_state = {}; self.last_sched_check = 0; self.v2_runs = []

    # ------------------------------------------------------------ observation
    def digest(self):
        ns = self.ns; ENG = ns["ENG"]; db = self.db; now = time.time()
        summ = ns["agent_summary"]()
        tools = db.all("select tool, count(*) n, sum(ok) ok from tool_calls group by tool order by n desc")
        errs = db.all("select tool, substr(result,1,160) r from tool_calls where ok=0 or result like '%\"error\"%' order by id desc limit 12")
        runs = db.all("select task, status, created, completed from agent_runs where provider='creao-cli' order by created desc limit 25")
        dur = {}
        for r in runs:
            if r["completed"]: dur.setdefault(r["task"], []).append(r["completed"] - r["created"])
        evc = db.all("select type, count(*) n from events where ts > ? group by type order by n desc limit 30", (now - 6 * 3600,))
        incs = []
        for i in db.all("select id, state, rack, created from incidents order by created desc limit 6"):
            tl = db.all("select json_extract(data,'$.to') s, ts from events where incident_id=? and type='state_transition' order by id", (i["id"],))
            incs.append({"id": i["id"], "state": i["state"], "rack": i["rack"], "path": [t["s"] for t in tl][-12:]})
        wos = [{"id": w["id"], "asset": w["asset"], "state": w["state"], "outcome": w.get("outcome"), "options_evaluated": len(w.get("options") or []),
                "rul_at_prediction": (w.get("prediction") or {}).get("rul_s")} for w in ns["REL"].list(5)]
        fl = ns["FLEET"].stats() if ns["FLEET"].last is not None else {}
        tw = ns["TWIN"]; preds = ns["PRED"].preds
        desk = ns["DESK"].snapshot()
        inv = {}
        for x in desk["invoices"]: inv[x["status"]] = inv.get(x["status"], 0) + 1
        return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sim_t": round(ENG.sim.t, 1),
                "orchestration_v1": [{"name": a["name"], "kind": a["kind"], "loop": a["loop"], "role": a["role"], "status": a["status"], "runs": a["count"]} for a in summ["roster"]],
                "orchestration_v2_active": [{"id": o["id"], "kind": o["kind"], "name": o["name"], "status": o["status"], "runs": o.get("runs", 0), "spec": o.get("spec")} for o in self.active()],
                "kpis": summ["kpis"],
                "creao_runs_recent": [{"task": r["task"], "status": r["status"], "duration_s": round(r["completed"] - r["created"]) if r["completed"] else None} for r in runs[:15]],
                "creao_mean_duration_s": {k: round(sum(v) / len(v)) for k, v in dur.items()},
                "tool_usage": [{"tool": t["tool"], "calls": t["n"], "ok": t["ok"]} for t in tools[:20]],
                "tool_errors_recent": [{"tool": e["tool"], "result": e["r"]} for e in errs],
                "event_counts_6h": {e["type"]: e["n"] for e in evc},
                "incidents_recent": incs, "work_orders_recent": wos,
                "predictive": {"proof": {k: v for k, v in ns["PRED"].proof().items() if k != "episodes"}, "at_risk": {a: {"risk": p["risk"], "rul_s": p.get("rul_s")} for a, p in preds.items() if p.get("risk") not in ("nominal", "learning")}},
                "gpu_fleet": {k: fl.get(k) for k in ("allocated", "idle", "mean_util_alloc", "fleet_util", "throttled_thermal", "throttled_power", "max_temp", "board_power_kw", "stranded_idle_on_hot_racks", "util_hist", "hottest")},
                "gpu_workloads_low_util": sorted([{"workload": w, **v} for w, v in fl.get("workloads", {}).items() if v["mean_util"] < 0.35], key=lambda x: x["mean_util"])[:8],
                "facility": {"subsystems": [{"id": s_["id"], "status": s_["status"], "kpi": s_["kpi"]} for s_ in tw.subsystem_health(preds)], "bess": {k: tw.bess[k] for k in ("soc", "mode", "saved_usd")}, "security_events": tw.security[-4:]},
                "finance": {"invoice_status": inv, "score": desk.get("score"), "alerts": [a.get("text") for a in desk.get("alerts", [])[:6]]},
                "findings_recent": [{"source": f["source"], "severity": f["severity"], "title": f["title"]} for f in db.all("select * from findings order by id desc limit 8")],
                "capabilities": {"dispatchable_kinds": ["agent", "scheduler", "detector"], "recommendation_only_kinds": ["policy"], "detector_metrics": METRICS,
                                 "agent_tools": OPS_TOOLS, "limits": {"max_v2_agent_runs_per_hour": MAX_V2_RUNS_PER_HOUR, "scheduler_min_interval_s": 900, "creao_writes_per_hour": 30}}}

    def prompt(self, local_api=None, owner=None):
        d = self.digest()
        return ("You are the GRIDPILOT Ghost Architect. GRIDPILOT is an autonomous data-center operations system (simulated GPU pod Atlas Compute SFO-01) "
                "run by agentic loops: CREAO agents for remediation, reliability and finance, a Cartesia voice operator, deterministic detectors, a policy engine "
                "and a git config executor. You silently observe everything it does. Your job now: analyse how the current orchestration (v1) performs, find "
                "gaps (missed or slow anomaly detection, wasted GPU capacity, slow or failing agents, finance risks, energy, security, scheduling), and design "
                "ORCHESTRATION V2: new agents, schedulers and deterministic detectors that GRIDPILOT can dispatch after the operator approves.\n\n"
                + (("You may read more live data yourself with Python requests: GET %s/api/state, %s/api/ghost/digest, %s/api/trace/<incident|wo|finance>/<id>, "
                    "%s/api/gpus/rack/<R01..R64>, always with header X-Sender-Uid: %s. Read-only; do not POST anything.\n\n") % (local_api, local_api, local_api, local_api, owner) if local_api else "")
                + "Ground every observation in the numbers below (cite them). Do not invent capabilities: agents you propose get ONLY these read-only tools: "
                + ", ".join(OPS_TOOLS) + " (fleet digest, per-GPU telemetry, events, decision traces, capacity, findings, submit a finding). Detectors may use only these metrics: "
                + json.dumps(METRICS) + ".\n\nReturn ONLY one JSON object in a ```json block with this shape:\n"
                '{"version_label": "v2", "summary": "2-3 sentences", "observations": [{"finding": "...", "evidence": "numbers from the digest", "severity": "info|warning|critical", "area": "detection|gpu|agents|finance|energy|security|reliability"}], '
                '"proposals": [{"kind": "agent", "name": "Short Name", "loop": "Remediation|Reliability|Finance|Energy|Security|Capacity", "mission": "what it watches and works out (2-4 sentences)", '
                '"trigger": {"type": "schedule|event|detector", "every_s": 1800, "events": ["failure_predicted"], "detector": "name of a proposed detector"}, "rationale": "why, citing evidence", "expected_value": "...", "risk": "..."}, '
                '{"kind": "scheduler", "name": "...", "target": "agent:<name of a proposed or existing v2 agent> | ap_review", "every_s": 1800, "rationale": "...", "expected_value": "...", "risk": "..."}, '
                '{"kind": "detector", "name": "...", "metric": "one of the metric keys", "scope": "gpu|rack|asset|facility", "op": ">|<", "threshold": 86, "for_ticks": 6, '
                '"action": "finding | run_agent:<proposed agent name>", "severity": "warning|critical", "rationale": "...", "expected_value": "...", "risk": "..."}, '
                '{"kind": "policy", "name": "...", "change": "...", "rationale": "...", "risk": "..."}]}\n'
                "Propose 3 to 6 items, highest value first, at least one detector and at least one agent. Keep v1 intact; v2 adds to it. Never propose actions that "
                "change infrastructure without the existing human approval path.\n\nDIGEST (live, deterministic):\n" + json.dumps(d, default=str)[:24000])

    # ------------------------------------------------------------ proposals
    def ingest(self, raw, source, meta=None):
        obj = extract_json(raw) if isinstance(raw, str) else raw
        rid = "GH-%s" % hashlib.sha1(("%s%s" % (time.time(), source)).encode()).hexdigest()[:6]
        if not isinstance(obj, dict):
            self.db.ex("insert into ghost_runs values(?,?,?,?,?)", (rid, time.time(), source, "unparsed", json.dumps({"raw": (raw or "")[:6000], **(meta or {})})))
            return {"ok": False, "id": rid, "message": "the ghost's answer had no JSON blueprint"}
        props = []
        for k, p in enumerate((obj.get("proposals") or [])[:6]):
            v = self.validate(p); v["pid"] = "%s-P%d" % (rid, k + 1); v["status"] = "proposed"; props.append(v)
        data = {"summary": str(obj.get("summary", ""))[:1200], "version_label": str(obj.get("version_label", "v2"))[:12],
                "observations": [{"finding": str(o.get("finding", ""))[:400], "evidence": str(o.get("evidence", ""))[:400], "severity": o.get("severity", "info") if o.get("severity") in ("info", "warning", "critical") else "info", "area": str(o.get("area", ""))[:20]} for o in (obj.get("observations") or [])[:10]],
                "proposals": props, **(meta or {})}
        self.db.ex("insert into ghost_runs values(?,?,?,?,?)", (rid, time.time(), source, "proposed", json.dumps(data)))
        self.ns["ENG"].event(None, "ghost_blueprint", "mana:ghost-architect", {"run": rid, "source": source, "proposals": len(props), "observations": len(data["observations"]), "chat_id": (meta or {}).get("chat_id")})
        self.ns["notify"](); return {"ok": True, "id": rid, "proposals": len(props)}

    def validate(self, p):
        k = p.get("kind"); out = {"kind": k, "name": str(p.get("name", "unnamed"))[:60], "rationale": str(p.get("rationale", ""))[:800], "expected_value": str(p.get("expected_value", ""))[:300], "risk": str(p.get("risk", ""))[:300], "problems": []}
        if k == "agent":
            tr = p.get("trigger") or {}
            out.update({"loop": str(p.get("loop", "Capacity"))[:20], "mission": str(p.get("mission", ""))[:1200], "trigger": {"type": tr.get("type") if tr.get("type") in ("schedule", "event", "detector") else "schedule",
                        "every_s": max(900, int(tr.get("every_s") or 1800)), "events": [str(e)[:40] for e in (tr.get("events") or [])][:5], "detector": str(tr.get("detector") or "")[:60]}})
            if len(out["mission"]) < 30: out["problems"].append("mission too short")
        elif k == "scheduler":
            out.update({"target": str(p.get("target", ""))[:80], "every_s": max(900, int(p.get("every_s") or 1800))})
            if not (out["target"].startswith("agent:") or out["target"] == "ap_review"): out["problems"].append("target must be agent:<name> or ap_review")
        elif k == "detector":
            out.update({"metric": p.get("metric"), "scope": p.get("scope", "gpu"), "op": p.get("op", ">") if p.get("op") in (">", "<") else ">", "threshold": float(p.get("threshold") or 0),
                        "for_ticks": max(1, min(60, int(p.get("for_ticks") or 4))), "action": str(p.get("action", "finding"))[:80], "severity": p.get("severity", "warning") if p.get("severity") in ("warning", "critical") else "warning"})
            if out["metric"] not in METRICS: out["problems"].append("unknown metric %s" % out["metric"])
        elif k == "policy":
            out.update({"change": str(p.get("change", ""))[:800]})
        else:
            out["kind"] = "policy"; out["change"] = json.dumps(p)[:400]; out["problems"].append("unknown kind, kept as recommendation")
        return out

    def runs(self, n=6):
        return [dict(r, data=json.loads(r["data"])) for r in self.db.all("select * from ghost_runs order by created desc limit ?", (n,))]

    def active(self):
        return [dict(json.loads(r["data"]), id=r["id"], kind=r["kind"], name=r["name"], status=r["status"], created=r["created"]) for r in self.db.all("select * from orch order by created")]

    def _set_prop(self, run_id, pid, **kw):
        r = self.db.one("select data from ghost_runs where id=?", (run_id,)); d = json.loads(r["data"])
        for p in d["proposals"]:
            if p["pid"] == pid: p.update(kw)
        self.db.ex("update ghost_runs set data=? where id=?", (json.dumps(d), run_id))

    async def decide(self, pid, approve, actor):
        run_id = pid.rsplit("-P", 1)[0]; r = self.db.one("select data from ghost_runs where id=?", (run_id,))
        if not r: return False, "unknown proposal"
        p = next((x for x in json.loads(r["data"])["proposals"] if x["pid"] == pid), None)
        if not p or p["status"] != "proposed": return False, "proposal is not open"
        if not approve:
            self._set_prop(run_id, pid, status="dismissed", decided_by=actor); self.ns["ENG"].event(None, "ghost_proposal_dismissed", actor, {"proposal": pid, "name": p["name"]}); self.ns["notify"](); return True, "dismissed"
        if p.get("problems") and p["kind"] != "policy": return False, "cannot dispatch: " + "; ".join(p["problems"])
        oid = "V2-%s-%s" % (p["kind"][:3].upper(), slug(p["name"])[:24])
        spec = {k: v for k, v in p.items() if k not in ("pid", "status", "problems")}
        status = "recommendation" if p["kind"] == "policy" else "active"
        self.db.ex("insert or replace into orch values(?,?,?,?,?,?)", (oid, p["kind"], p["name"], status, time.time(), json.dumps({"spec": spec, "proposal": pid, "approved_by": actor, "runs": 0, "fires": 0})))
        msg = "dispatched"
        if p["kind"] == "agent":
            k, aid = await self.ns["ENG"].ensure_agent("v2_" + slug(p["name"]), "GRIDPILOT " + p["name"], self.brief(p))
            self._orch_update(oid, agent_key=k, agent_id=aid); msg = "CREAO agent %s (%s)" % ("ready" if aid else "being created", "GRIDPILOT " + p["name"])
        if p["kind"] == "policy": msg = "recorded as an engineering recommendation (policies stay human-written code)"
        self._set_prop(run_id, pid, status="dispatched" if p["kind"] != "policy" else "accepted", decided_by=actor, orch_id=oid)
        self.ns["ENG"].event(None, "ghost_dispatched", actor, {"proposal": pid, "orch_id": oid, "kind": p["kind"], "name": p["name"], "detail": msg}); self.ns["notify"]()
        return True, msg

    def retire(self, oid, actor):
        self.db.ex("update orch set status='retired' where id=?", (oid,)); self.ns["ENG"].event(None, "ghost_retired", actor, {"orch_id": oid}); self.ns["notify"]()

    def _orch_update(self, oid, **kw):
        r = self.db.one("select data from orch where id=?", (oid,)); d = json.loads(r["data"]); d.update(kw)
        self.db.ex("update orch set data=? where id=?", (json.dumps(d, default=str), oid))

    def brief(self, p):
        return ("Create a reusable personal agent named \"GRIDPILOT %s\" with exactly ONE input field named `request` (long text).\n\n"
                "Origin: designed by the GRIDPILOT Ghost Architect (orchestration v2) and approved by the operator. Loop: %s.\n\nMission: %s\n\n"
                "On every run: parse `request` as JSON (gridpilot_api, tool_token, task, trigger, context). Tools are HTTPS endpoints: POST JSON to {gridpilot_api}/{tool} "
                "with headers `X-GridPilot-Tool-Token: {tool_token}` and `Content-Type: application/json`, using Python requests (json=...). Tools (read-only): "
                "ops_digest {} (whole-system digest), ops_gpu_fleet {rack?} (per-GPU utilization, temperature, power, memory, owner, throttle for all 512 GPUs, or one rack), "
                "ops_events {types?: [..], limit?} (recent events), ops_trace {kind: incident|wo|finance, id} (decision trace), ops_capacity {} (racks, free GPUs, cooling), "
                "ops_findings {} (recent findings), ops_submit_finding {title, severity: info|warning|critical, text, evidence: [..], recommended_actions: [..]}. "
                "You cannot change anything: recommended_actions go to the operator and the existing approval path. Never invent a tool result.\n\n"
                "Each run: read what your mission needs, then submit 0 to 3 findings with concrete evidence (ids, numbers). If nothing is wrong, submit nothing. "
                "Final answer: one JSON object {findings: n, summary}.") % (p["name"], p.get("loop", ""), p.get("mission", ""))

    # ------------------------------------------------------------ v2 runtime
    def _budget_ok(self):
        now = time.time(); self.v2_runs = [t for t in self.v2_runs if now - t < 3600]
        return len(self.v2_runs) < MAX_V2_RUNS_PER_HOUR

    async def run_agent(self, oid, trigger):
        o = next((x for x in self.active() if x["id"] == oid and x["status"] == "active" and x["kind"] == "agent"), None)
        if not o: return None, "agent not active"
        if time.time() - (o.get("last_run") or 0) < 300: return None, "ran less than 5 minutes ago"
        if not self._budget_ok(): self.ns["ENG"].event(None, "ghost_run_throttled", "backend", {"orch_id": oid, "reason": "v2 hourly run budget"}); return None, "hourly v2 run budget reached"
        p = o["spec"]; self.v2_runs.append(time.time())
        ref, err = await self.ns["start_agent"]("v2_" + slug(p["name"]), "GRIDPILOT " + p["name"], self.brief(p), OPS_TOOLS,
                                                {"task": "v2_mission", "trigger": trigger, "orch_id": oid, "context": {"sim_t": round(self.ns["ENG"].sim.t, 1)}}, {"orch_id": oid, "mission": p.get("mission", "")[:200], "trigger": str(trigger)[:120]})
        self._orch_update(oid, last_run=time.time(), runs=(o.get("runs") or 0) + 1, last_ref=ref, last_error=err)
        self.ns["ENG"].event(None, "v2_agent_run", "orch:" + oid, {"orch_id": oid, "run_ref": ref, "trigger": trigger, "error": err}); self.ns["notify"]()
        return ref, err

    def by_name(self, name):
        n = slug(name.split(":", 1)[-1])
        return next((x for x in self.active() if x["status"] == "active" and x["kind"] == "agent" and slug(x["name"]) == n), None)

    async def on_event(self, typ, data):
        for o in self.active():
            if o["status"] == "active" and o["kind"] == "agent" and o["spec"].get("trigger", {}).get("type") == "event" and typ in o["spec"]["trigger"].get("events", []):
                await self.run_agent(o["id"], {"type": "event", "event": typ, "data": {k: v for k, v in (data or {}).items() if isinstance(v, (str, int, float))}})

    async def schedule_tick(self):
        now = time.time()
        if now - self.last_sched_check < 20: return
        self.last_sched_check = now
        for o in self.active():
            if o["status"] != "active": continue
            sp = o["spec"]; every = None; target = None
            if o["kind"] == "scheduler": every, target = sp["every_s"], sp["target"]
            elif o["kind"] == "agent" and sp.get("trigger", {}).get("type") == "schedule": every, target = sp["trigger"]["every_s"], "agent:" + o["name"]
            if not every or now - (o.get("last_fire") or o["created"]) < every: continue
            self._orch_update(o["id"], last_fire=now, fires=(o.get("fires") or 0) + 1)
            if target == "ap_review": await self.ns["run_ap_review"](); self.ns["ENG"].event(None, "v2_scheduler_fired", "orch:" + o["id"], {"target": target})
            else:
                a = self.by_name(target)
                if a: await self.run_agent(a["id"], {"type": "schedule", "scheduler": o["id"]}); self.ns["ENG"].event(None, "v2_scheduler_fired", "orch:" + o["id"], {"target": target})

    def metric_values(self, metric, scope):
        ns = self.ns; sim = ns["ENG"].sim; fl = ns["FLEET"].last; tw = ns["TWIN"]
        if metric == "gpu_temp_c" and fl is not None: return {i: float(v) for i, v in enumerate(fl["temp"])}
        if metric == "gpu_util" and fl is not None: return {i: float(v) for i, v in enumerate(fl["util"])}
        if metric == "gpu_power_w" and fl is not None: return {i: float(v) for i, v in enumerate(fl["power"])}
        if metric == "rack_gpu_temp_c": return {i: float(v) for i, v in enumerate(sim.gpu_t)}
        if metric == "rack_inlet_c": return {i: float(v) for i, v in enumerate(sim.inlet)}
        if metric == "rack_power_kw": return {i: float(v) for i, v in enumerate(sim.P)}
        if metric == "crah_vibration_mm_s": return {k: tw.assets[k]["sensors"].get("vibration_mm_s", 0) for k in ("C01", "C02", "C03", "C04")}
        if metric == "crah_effectiveness_pct": return {k: tw.assets[k]["sensors"].get("effectiveness_pct", 100) for k in ("C01", "C02", "C03", "C04")}
        if metric == "pue": return {"pod": float(sim.pue())}
        if metric == "grid_kw": return {"pod": tw.energy["grid_kw"]}
        if metric == "bess_soc_pct": return {"pod": tw.bess["soc"] * 100}
        if metric == "ups_runtime_min": return {k: tw.assets[k]["sensors"].get("runtime_min", 99) for k in ("UPS-A", "UPS-B")}
        if metric in ("idle_gpus", "throttled_gpus") and fl is not None:
            st = ns["FLEET"].stats(); return {"pod": st["idle"] if metric == "idle_gpus" else st["throttled_thermal"] + st["throttled_power"]}
        return {}

    async def detect_tick(self):
        from gpus import gid
        import sim as S
        for o in self.active():
            if o["status"] != "active" or o["kind"] != "detector": continue
            sp = o["spec"]; vals = self.metric_values(sp["metric"], sp["scope"]); st = self.det_state.setdefault(o["id"], {})
            fired = []
            for key, v in vals.items():
                hit = v > sp["threshold"] if sp["op"] == ">" else v < sp["threshold"]
                st[key] = st.get(key, 0) + 1 if hit else 0
                if st[key] == sp["for_ticks"]: fired.append((key, v))
            if not fired: continue
            name = lambda k: gid(k) if sp["metric"].startswith("gpu_") else (S.rack_id(k // 8, k % 8) if sp["metric"].startswith("rack_") else str(k))
            items = [{"target": name(k), "value": round(v, 2)} for k, v in fired[:12]]
            self.db.ex("insert into findings(ts,source,severity,title,data) values(?,?,?,?,?)", (time.time(), "detector:" + o["id"], sp["severity"],
                       "%s: %s %s %s for %d ticks (%d targets)" % (o["name"], sp["metric"], sp["op"], sp["threshold"], sp["for_ticks"], len(fired)), json.dumps({"items": items, "detector": o["id"]})))
            self._orch_update(o["id"], fires=(o.get("fires") or 0) + 1, last_fire=time.time())
            self.ns["ENG"].event(None, "v2_detector_fired", "orch:" + o["id"], {"detector": o["name"], "metric": sp["metric"], "count": len(fired), "first": items[0]})
            if sp["action"].startswith("run_agent:"):
                a = self.by_name(sp["action"])
                if a: await self.run_agent(a["id"], {"type": "detector", "detector": o["id"], "items": items})
            self.ns["notify"]()

    # ------------------------------------------------------------ tools for v2 agents
    async def tool(self, tool, args, actor, oid):
        ns = self.ns
        if tool == "ops_digest": return self.digest()
        if tool == "ops_gpu_fleet":
            if args.get("rack"): return ns["FLEET"].rack(str(args["rack"]).upper())
            p = ns["FLEET"].payload(); return {"stats": p["stats"], "per_gpu": [{"gpu": __import__("gpus").gid(g), "util": p["util"][g], "temp": p["temp"][g], "power_w": p["power"][g], "workload": p["owner"][g], "throttle": p["throttle"][g]} for g in range(512) if p["temp"][g] > 76 or p["util"][g] < 0.05 or p["throttle"][g]][:160],
                                               "note": "per_gpu lists only hot (>76C), idle (<5%) or throttling GPUs; use rack for full detail"}
        if tool == "ops_events":
            types = args.get("types") or []; lim = min(80, int(args.get("limit") or 40))
            rows = self.db.all("select type, actor, ts, incident_id, substr(data,1,300) d from events order by id desc limit 400")
            return {"events": [r for r in rows if not types or r["type"] in types][:lim]}
        if tool == "ops_trace": return ns["build_trace"](str(args.get("kind")), str(args.get("id")))
        if tool == "ops_capacity":
            snap = ns["ENG"].sim.snapshot(field=False); cfg = ns["ENG"].sim.config; used = {r: 0 for r in cfg["racks"]}
            for w in cfg["workloads"].values(): used[w["rack"]] += w["gpus"]
            return {"racks": [{"rack_id": r["rack_id"], "free_gpus": 8 - used[r["rack_id"]], "gpu_temp_c": r["gpu_temp_c"], "utilization": r["utilization"], "zone": r["zone"], "status": r["status"]} for r in snap["racks"]], "cooling": snap["cooling"]}
        if tool == "ops_findings": return {"findings": [dict(f, data=json.loads(f["data"])) for f in self.db.all("select * from findings order by id desc limit 20")]}
        if tool == "ops_submit_finding":
            sev = args.get("severity") if args.get("severity") in ("info", "warning", "critical") else "info"
            self.db.ex("insert into findings(ts,source,severity,title,data) values(?,?,?,?,?)", (time.time(), actor + " · " + (oid or ""), sev, str(args.get("title", ""))[:200],
                       json.dumps({"text": str(args.get("text", ""))[:3000], "evidence": args.get("evidence") or [], "recommended_actions": args.get("recommended_actions") or [], "orch_id": oid})))
            ns["ENG"].event(None, "v2_finding", actor, {"orch_id": oid, "severity": sev, "title": str(args.get("title", ""))[:160]}); ns["notify"](); return {"ok": True, "note": "finding delivered to the operator"}
        return {"error": "unknown tool"}

    def view(self):
        return {"runs": self.runs(6), "active": self.active(), "findings": [dict(f, data=json.loads(f["data"])) for f in self.db.all("select * from findings order by id desc limit 25")],
                "budget": {"v2_runs_last_hour": len([t for t in self.v2_runs if time.time() - t < 3600]), "max": MAX_V2_RUNS_PER_HOUR}}
