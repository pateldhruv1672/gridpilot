
"""GRIDPILOT incident engine: deterministic detection, persistent state machine, plan versions, policy, approvals,
execution against the git config store, recovery monitoring, rollback, finance, and sponsor orchestration."""
import asyncio, copy, hashlib, json, os, secrets, sqlite3, time, traceback
from collections import deque
import numpy as np
import sim as S, configstore as CS, policy as POL, finance as FIN, integrations as IX, specialists as SP

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(os.environ.get("GRIDPILOT_HOME") or CODE_ROOT, "state", "gridpilot.db")
CRITICAL_TICKS = 3
RECOVERY_HOLD_S = 30.0
RECOVERY_DEADLINE_S = 420.0
APPROVAL_TTL_S = 15 * 60
PROTECTED = {c for c, v in S.CUSTOMERS.items() if v["protected"]}

TRANS = {
    None: {"DETECTED"}, "DETECTED": {"TRIAGING", "ESCALATED"}, "TRIAGING": {"PLAN_PROPOSED", "ESCALATED", "TRIAGING"},
    "PLAN_PROPOSED": {"POLICY_CHECKED"}, "POLICY_CHECKED": {"EXECUTING", "AWAITING_APPROVAL", "DENIED", "PLAN_PROPOSED"},
    "AWAITING_APPROVAL": {"APPROVED", "PLAN_PROPOSED", "REPLAN", "ESCALATED"}, "APPROVED": {"EXECUTING", "PLAN_PROPOSED"},
    "EXECUTING": {"VALIDATING", "REPLAN"}, "VALIDATING": {"MONITORING_RECOVERY", "REPLAN"},
    "MONITORING_RECOVERY": {"RESOLVED", "ROLLING_BACK"}, "ROLLING_BACK": {"REPLAN", "ESCALATED"},
    "REPLAN": {"TRIAGING", "PLAN_PROPOSED", "ESCALATED"}, "DENIED": {"TRIAGING", "REPLAN", "PLAN_PROPOSED", "ESCALATED"},
    "RESOLVED": {"FINANCIAL_RECONCILIATION"}, "FINANCIAL_RECONCILIATION": {"CLOSED"},
    "ESCALATED": {"TRIAGING", "PLAN_PROPOSED", "CLOSED"},
}
OPEN_FOR_PLANS = {"TRIAGING", "REPLAN", "AWAITING_APPROVAL", "DENIED", "ESCALATED", "POLICY_CHECKED", "APPROVED"}
CREAO_TASKS = {"diagnose_and_plan", "evaluate_alternative", "replan_after_failure", "execute_approved_plan", "post_incident_summary"}

def now(): return time.time()
def jd(x): return json.dumps(x, default=lambda o: o.item() if hasattr(o, "item") else str(o))

class DB:
    def __init__(self, path=None):
        path = path or DB_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False); self.c.row_factory = sqlite3.Row
        self.c.executescript("""
        create table if not exists events(id integer primary key autoincrement, ts real, sim_t real, incident_id text, type text, actor text, data text);
        create table if not exists incidents(id text primary key, created real, state text, rack text, data text);
        create table if not exists plans(id text, version integer, incident_id text, created real, author text, status text, data text, primary key(id, version));
        create table if not exists agent_runs(id text primary key, provider text, provider_run_id text, incident_id text, task text, status text, created real, completed real, data text);
        create table if not exists tool_calls(id integer primary key autoincrement, ts real, provider text, run_ref text, incident_id text, tool text, args text, result text, ok integer);
        create table if not exists approvals(id text primary key, incident_id text, plan_id text, plan_version integer, created real, expires real, status text, data text);
        create table if not exists executions(id text primary key, incident_id text, plan_id text, plan_version integer, created real, state text, data text, unique(plan_id, plan_version));
        create table if not exists usage(id text primary key, incident_id text, kind text, data text);
        create table if not exists invoices(id text primary key, incident_id text, created real, data text);
        create table if not exists calls(id text primary key, incident_id text, created real, status text, data text);
        create table if not exists settings(k text primary key, v text);
        create table if not exists specialists(key text primary key, kind text, name text, agent_id text, status text, creation_run_id text, created real, data text);
        """)
        self.c.commit()
    def ex(self, q, a=()):
        cur = self.c.execute(q, a); self.c.commit(); return cur
    def all(self, q, a=()): return [dict(r) for r in self.c.execute(q, a).fetchall()]
    def one(self, q, a=()):
        r = self.c.execute(q, a).fetchone(); return dict(r) if r else None
    def get(self, k, default=None):
        r = self.one("select v from settings where k=?", (k,)); return json.loads(r["v"]) if r else default
    def set(self, k, v): self.ex("insert or replace into settings(k,v) values(?,?)", (k, jd(v)))


THOUGHT_RULE = ("\n\nThink out loud (mandatory): every tool call body MUST include a field `thought` (1 to 3 sentences, first person, plain English) saying "
                "what you are checking or doing and why, given what you have learned so far, e.g. \"R17 is 91C while its inlet is only 27C, so I suspect "
                "local airflow loss rather than room heat; checking neighbours.\" GRIDPILOT streams these thoughts live to the operator. Never put "
                "secrets in a thought. Thoughts never change what a tool does.")

class Engine:
    def __init__(self, sim, owner_uid):
        self.sim = sim; self.owner = owner_uid; self.db = DB()
        self.speed = 2.0; self.paused = False
        self.crit_count = {}; self.series = deque(maxlen=900); self._last_series_t = -99
        self.tokens = {k: v for k, v in (self.db.get("tool_tokens") or {}).items() if v.get("expires", 0) > now()}   # creao tool token -> {...}, persisted
        self.call_tokens = {k: v for k, v in (self.db.get("call_tokens") or {}).items() if v.get("expires", now() + 1) > now()}
        self.public_base = None     # ingress base URL for external callers
        self.webhook_secret = self.db.get("webhook_secret") or secrets.token_urlsafe(18); self.db.set("webhook_secret", self.webhook_secret)
        self.notify = lambda: None
        self.fail_armed = False
        self.cost = self.db.get("cost_accum", {"electricity_usd": 0.0, "lease_usd": 0.0, "transfer_usd": 0.0})
        self._last_energy = sim.energy_kwh
        self.bg = set()
        self.pending_inputs = {}    # local run ref -> request (contains a tool token; memory only)
        self._cli_ok = False; self._cli_checked = 0

    # ------------------------------------------------------------------ audit
    def event(self, inc_id, typ, actor, data=None):
        self.db.ex("insert into events(ts,sim_t,incident_id,type,actor,data) values(?,?,?,?,?,?)",
                   (now(), round(self.sim.t, 1), inc_id, typ, actor, jd(data or {})))
        self.notify()

    def incident(self, inc_id):
        r = self.db.one("select * from incidents where id=?", (inc_id,))
        if not r: return None
        d = json.loads(r["data"]); d.update({"id": r["id"], "state": r["state"], "rack": r["rack"], "created": r["created"]}); return d

    def save_incident(self, inc):
        data = {k: v for k, v in inc.items() if k not in ("id", "state", "rack", "created")}
        self.db.ex("update incidents set state=?, data=? where id=?", (inc["state"], jd(data), inc["id"]))

    def open_incident(self):
        r = self.db.one("select id from incidents where state not in ('CLOSED') order by created desc limit 1")
        return self.incident(r["id"]) if r else None

    def transition(self, inc, new, actor, reason, data=None):
        old = inc["state"]
        if new not in TRANS.get(old, set()):
            raise ValueError("illegal transition %s -> %s" % (old, new))
        inc["state"] = new
        inc.setdefault("timeline", []).append({"state": new, "from": old, "ts": now(), "sim_t": round(self.sim.t, 1), "actor": actor, "reason": reason})
        self.save_incident(inc)
        self.event(inc["id"], "state_transition", actor, {"from": old, "to": new, "reason": reason, **(data or {})})
        return inc

    # ------------------------------------------------------------------ loop
    def tick(self):
        s = self.sim
        de = s.energy_kwh - self._last_energy; self._last_energy = s.energy_kwh
        self.cost["electricity_usd"] += max(0, de) * FIN.ELECTRICITY_USD_KWH
        if s.t - self._last_series_t >= 2.0:
            self._last_series_t = s.t
            lat = s.workload_latency()
            self.series.append({"t": round(s.t, 1), "it_kw": round(float(s.it_kw), 1), "facility_kw": round(float(s.it_kw + s.cool_kw + 0.04 * s.it_kw), 1),
                                "pue": round(float(s.pue()), 3), "util": round(float(s.util.mean()), 3),
                                "r17_gpu": round(float(s.gpu_t[16]), 1), "r17_inlet": round(float(s.inlet[16]), 1), "r17_power": round(float(s.P[16]), 2),
                                "max_gpu": round(float(s.gpu_t.max()), 1), "max_inlet": round(float(s.inlet.max()), 1),
                                "c02_eff": round(float(s.unit_eff[1]), 3), "breaches": sum(1 for v in lat.values() if v["breach"]),
                                "electricity_usd": round(self.cost["electricity_usd"], 4), "lease_usd": round(self.cost["lease_usd"], 2),
                                "transfer_usd": round(self.cost["transfer_usd"], 2)})
        # deterministic anomaly detection (no LLM polling)
        for idx in range(64):
            rid = S.rack_id(idx // 8, idx % 8)
            if s.gpu_t[idx] > S.THRESH["gpu_critical_c"]:
                self.crit_count[rid] = self.crit_count.get(rid, 0) + 1
            else:
                self.crit_count[rid] = 0
            if self.crit_count[rid] == CRITICAL_TICKS:
                self.detect(rid, idx)
        inc = self.open_incident()
        if inc and inc["state"] == "MONITORING_RECOVERY":
            self.monitor(inc)

    def detect(self, rid, idx):
        existing = self.db.one("select id from incidents where rack=? and state != 'CLOSED'", (rid,))
        if existing:
            self.event(existing["id"], "duplicate_telemetry_suppressed", "detector", {"rack": rid, "gpu_temp_c": round(float(self.sim.gpu_t[idx]), 1)})
            return
        if self.open_incident():      # one active incident at a time in this pod; correlate instead of duplicating
            self.event(self.open_incident()["id"], "correlated_alarm", "detector", {"rack": rid}); return
        n = self.db.get("incident_counter", 41) + 1; self.db.set("incident_counter", n)
        inc_id = "INC-%03d" % n
        s = self.sim; k = S.cold_aisle_of_row(idx // 8)
        evidence = {"rack": rid, "gpu_temp_c": round(float(s.gpu_t[idx]), 1), "inlet_temp_c": round(float(s.inlet[idx]), 1),
                    "exhaust_temp_c": round(float(s.exh[idx]), 1), "power_kw": round(float(s.P[idx]), 2), "utilization": round(float(s.util[idx]), 3),
                    "cold_aisle": k, "aisle_supply_temp_c": round(float(s.sup_T[k]), 1), "aisle_deficit": round(float(s.deficit[k]), 3),
                    "unit_effectiveness": {"C%02d" % (j + 1): round(float(s.unit_eff[j]), 3) for j in range(4)},
                    "threshold_c": S.THRESH["gpu_critical_c"], "consecutive_ticks": CRITICAL_TICKS}
        weakest = int(np.argmin(s.unit_eff))
        hypothesis = ("Detector hypothesis (deterministic): cooling unit C%02d effectiveness %.0f%%, aisle supply %.1f°C, airflow deficit %.0f%%"
                      % (weakest + 1, s.unit_eff[weakest] * 100, s.sup_T[k], s.deficit[k] * 100))
        exposure = self.exposure_now()
        data = {"severity": "SEV-1", "title": "GPU THERMAL ANOMALY", "detected_sim_t": round(s.t, 1), "detected_at": now(),
                "evidence": evidence, "probable_cause": hypothesis, "sla_exposure_now": exposure, "timeline": []}
        self.db.ex("insert into incidents(id,created,state,rack,data) values(?,?,?,?,?)", (inc_id, now(), None, rid, jd(data)))
        inc = self.incident(inc_id); inc["state"] = None
        self.transition(inc, "DETECTED", "detector", "GPU hotspot above %.0f°C for %d consecutive ticks" % (S.THRESH["gpu_critical_c"], CRITICAL_TICKS), {"evidence": evidence})
        self.spawn(self.begin_triage(inc_id))

    def exposure_now(self):
        lat = self.sim.workload_latency(); out = 0.0; items = []
        for wid, v in lat.items():
            if v["breach"]:
                c = S.CUSTOMERS[self.sim.config["workloads"][wid]["customer"]]
                items.append({"workload": wid, "p95_ms": v["p95_ms"], "limit_ms": v["limit_ms"], "usd_per_min": c["penalty_per_breach_min"]})
                out += c["penalty_per_breach_min"]
        return {"usd_per_min_if_sustained": round(out, 2), "breaching": items, "label": "CONDITIONAL EXPOSURE RATE"}

    def spawn(self, coro):
        t = asyncio.get_event_loop().create_task(coro); self.bg.add(t); t.add_done_callback(self.bg.discard)

    # ------------------------------------------------------------------ CREAO orchestration
    def creao_config(self):
        if now() - self._cli_checked > 30:
            self._cli_ok = IX.cli_available(); self._cli_checked = now()
        return {"app_id": self.db.get("creao_app_id"), "base": self.public_base, "cli": self._cli_ok}

    async def begin_triage(self, inc_id):
        inc = self.incident(inc_id)
        try:
            snap = self.sim.snapshot(field=False); cfg = self.sim.config
            hit = [w for w, v in cfg["workloads"].items() if v["rack"] == inc["rack"] and v["customer"] in PROTECTED and snap["latency"].get(w, {}).get("breach")]
        except Exception: hit = []
        if hit:
            self.spawn(self.spawn_specialist(inc_id, "sla_guardian", "Protected-SLA workloads %s are breaching on %s. Assess customer impact and the lowest-risk action set." % (", ".join(hit), inc["rack"]),
                                             requested_by="backend", context={"breaching": hit}, auto=True))
        await self.start_creao(inc_id, "diagnose_and_plan", {})

    async def start_creao(self, inc_id, task, extra):
        inc = self.incident(inc_id)
        cfg = self.creao_config()
        local = "CR-%s" % secrets.token_hex(4)
        mode = "app" if cfg["app_id"] else ("cli" if cfg["cli"] else None)
        if not mode or not cfg["base"]:
            self.db.ex("insert into agent_runs values(?,?,?,?,?,?,?,?,?)", (local, "creao", None, inc_id, task, "disconnected", now(), now(),
                       jd({"error": "CREAO not configured: " + ("missing App ID or CREAO CLI key" if not mode else "no public tool URL yet")})))
            self.event(inc_id, "creao_disconnected", "backend", {"task": task, "reason": "not configured"})
            return None
        token = secrets.token_urlsafe(24)
        self.tokens[token] = {"incident_id": inc_id, "task": task, "run_ref": local, "expires": now() + 3600, "allowed": SP.MAIN_TOOLS}
        self.db.set("tool_tokens", self.tokens)
        sp = self.specialist_summaries(inc_id)
        if sp: extra = dict(extra, specialist_findings=extra.get("specialist_findings") or sp)
        request = {"gridpilot_api": cfg["base"] + "/tools/creao", "tool_token": token, "task": task, "incident_id": inc_id,
                   "tool_protocol": "POST JSON to <gridpilot_api>/<tool_name> with header 'X-GridPilot-Tool-Token: <tool_token>'. Call GET <gridpilot_api>/manifest for tool schemas.",
                   **extra}
        provider = "creao" if mode == "app" else "creao-cli"
        self.db.ex("insert into agent_runs values(?,?,?,?,?,?,?,?,?)", (local, provider, None, inc_id, task, "starting", now(), None, jd({"request": {**request, "tool_token": "[redacted]"}})))
        if task in ("diagnose_and_plan",) and inc["state"] == "DETECTED":
            self.transition(inc, "TRIAGING", "backend", "CREAO Incident Commander run requested")
        elif task in ("replan_after_failure", "evaluate_alternative") and inc["state"] in ("REPLAN", "DENIED", "ESCALATED"):
            self.transition(inc, "TRIAGING", "backend", "CREAO re-analysis requested (%s)" % task)
        try:
            if mode == "cli":
                key, aid = await self.ensure_agent("remediation", "GRIDPILOT Remediation Agent", SP_REMEDIATION_TEXT())
                d = json.loads(self.db.one("select data from agent_runs where id=?", (local,))["data"]); d["agent_key"] = key
                self.db.ex("update agent_runs set data=? where id=?", (jd(d), local))
                self.pending_inputs[local] = request
                if not aid:
                    self.db.ex("update agent_runs set status='awaiting_agent' where id=?", (local,))
                    self.event(inc_id, "creao_agent_pending", "backend", {"run_ref": local, "task": task, "agent": "GRIDPILOT Remediation Agent"})
                    return local
                await self._start_cli_run(local, aid); return local
            res = await asyncio.to_thread(IX.creao_start_run, cfg["app_id"], {"request": jd(request)}, cfg["base"] + "/webhooks/creao/" + self.webhook_secret)
            rid = res.get("id")
            self.db.ex("update agent_runs set provider_run_id=?, status=? where id=?", (rid, res.get("status", "pending"), local))
            self.event(inc_id, "creao_run_started", "backend", {"run_ref": local, "creao_run_id": rid, "task": task, "app": res.get("appName")})
            return local
        except Exception as e:
            self.db.ex("update agent_runs set status='failed', completed=?, data=? where id=?", (now(), jd({"error": str(e)[:800]}), local))
            self.event(inc_id, "creao_run_failed_to_start", "backend", {"run_ref": local, "task": task, "error": str(e)[:400]})
            return None

    async def creao_poller(self):
        while True:
            await asyncio.sleep(4)
            try:
                for r in self.db.all("select * from agent_runs where provider='creao' and status in ('pending','running','queued','starting') and provider_run_id is not null"):
                    await self.refresh_creao(r)
            except Exception:
                traceback.print_exc()

    async def refresh_creao(self, r):
        app = self.db.get("creao_app_id")
        try:
            run = await asyncio.to_thread(IX.creao_get_run, app, r["provider_run_id"])
        except Exception as e:
            return
        st = run.get("status")
        if st == r["status"]: return
        data = json.loads(r["data"] or "{}"); data["provider"] = {k: run.get(k) for k in ("status", "errorCode", "createdAt", "completedAt") if k in run}
        if st in ("completed", "failed", "cancelled"):
            result = run.get("result") or {}
            data["result_text"] = (result.get("text") or "")[:20000]
            data["chat_text"] = (result.get("chatText") or "")[:8000]
            data["structured"], data["structured_errors"] = self.parse_structured(data["result_text"] or data["chat_text"])
            self.db.ex("update agent_runs set status=?, completed=?, data=? where id=?", (st, now(), jd(data), r["id"]))
            self.event(r["incident_id"], "creao_run_" + st, "creao:" + r["provider_run_id"], {"run_ref": r["id"], "task": r["task"],
                       "structured_valid": not data["structured_errors"], "error": run.get("errorCode")})
            await self.after_creao(r, st, data)
        else:
            self.db.ex("update agent_runs set status=?, data=? where id=?", (st, jd(data), r["id"]))
            self.notify()

    def parse_structured(self, text):
        req = ["incident_id", "diagnosis", "evidence", "candidate_actions", "expected_thermal_outcomes", "expected_sla_risks",
               "requested_action", "confidence", "reasoning_summary", "requires_approval"]
        if not text: return None, ["empty result"]
        s = text.find("{"); e = text.rfind("}")
        obj = None
        for start in [i for i in range(len(text)) if text[i] == "{"][:40]:
            try:
                obj = json.loads(text[start:e + 1]); break
            except Exception: continue
        if not isinstance(obj, dict): return None, ["no JSON object in final output"]
        errs = ["missing field %s" % k for k in req if k not in obj]
        return obj, errs

    async def after_creao(self, r, st, data):
        inc = self.incident(r["incident_id"])
        if not inc: return
        if st != "completed":
            self.event(inc["id"], "creao_run_unsuccessful", "backend", {"run_ref": r["id"], "status": st}); return
        if r["task"] == "post_incident_summary":
            inc["summary"] = {"run_ref": r["id"], "text": data.get("result_text", "")[:6000]}; self.save_incident(inc)

    # ------------------------------------------------------------------ dynamic CREAO agents (CREAO CLI, cr_sk_ key)
    async def ensure_agent(self, kind, name, text):
        text = text + THOUGHT_RULE
        key = kind + ":" + hashlib.sha256(text.encode()).hexdigest()[:12]
        row = self.db.one("select * from specialists where key=?", (key,))
        if row and row["status"] == "ready": return key, row["agent_id"]
        if row and row["status"] == "creating": return key, None
        res = await asyncio.to_thread(IX.cli_create_agent, text, (self.public_base + "/webhooks/creao/" + self.webhook_secret) if self.public_base else None)
        self.db.ex("insert or replace into specialists values(?,?,?,?,?,?,?,?)", (key, kind, name, None, "creating", res.get("run_id"), now(),
                   jd({"model": res.get("model"), "conversation_id": res.get("conversation_id"), "brief_chars": len(text), "created_at": res.get("created_at")})))
        self.event(None, "creao_agent_creation_started", "backend", {"key": key, "name": name, "creation_run_id": res.get("run_id")})
        return key, None

    async def _start_cli_run(self, local, agent_id):
        req = self.pending_inputs.pop(local, None)
        r = self.db.one("select * from agent_runs where id=?", (local,))
        if req is None:
            self.db.ex("update agent_runs set status='failed', completed=? where id=?", (now(), local))
            self.event(r["incident_id"], "creao_run_failed_to_start", "backend", {"run_ref": local, "error": "request lost (backend restarted before the agent was ready)"}); return
        try:
            res = await asyncio.to_thread(IX.cli_start_run, agent_id, {"request": jd(req)},
                                          (self.public_base + "/webhooks/creao/" + self.webhook_secret) if self.public_base else None)
            d = json.loads(r["data"] or "{}"); d["agent_id"] = agent_id; d["conversation_id"] = res.get("conversation_id"); d["model"] = res.get("model")
            self.db.ex("update agent_runs set provider_run_id=?, status=?, data=? where id=?", (res.get("run_id"), res.get("status", "pending"), jd(d), local))
            self.event(r["incident_id"], "creao_run_started", "backend", {"run_ref": local, "creao_run_id": res.get("run_id"), "task": r["task"], "agent_id": agent_id})
        except Exception as ex:
            self.db.ex("update agent_runs set status='failed', completed=? where id=?", (now(), local))
            self.event(r["incident_id"], "creao_run_failed_to_start", "backend", {"run_ref": local, "task": r["task"], "error": str(ex)[:400]})

    async def special_case(self, inc_id, kind, extra, question):
        """A failure path: create/run the matching specialist first; its findings feed the remediation replan."""
        ref = await self.spawn_specialist(inc_id, kind, question, requested_by="backend", context=extra, auto=True, follow_up="replan")
        if not ref:
            await self.start_creao(inc_id, "replan_after_failure", extra)

    async def spawn_specialist(self, inc_id, kind, question, mission=None, requested_by="backend", context=None, auto=False, follow_up=None):
        cfg = self.creao_config()
        if not cfg["cli"] or not cfg["base"]:
            self.event(inc_id, "specialist_unavailable", "backend", {"kind": kind, "reason": "CREAO CLI key (cr_sk_) not saved" if not cfg["cli"] else "no public tool URL yet"}); return None
        n = self.db.one("select count(*) n from agent_runs where incident_id=? and task like 'specialist:%'", (inc_id,))["n"]
        if n >= SP.MAX_PER_INCIDENT:
            self.event(inc_id, "specialist_refused", "backend", {"kind": kind, "reason": "limit of %d specialists per incident" % SP.MAX_PER_INCIDENT}); return None
        try: spec = SP.spec_for(kind, mission)
        except ValueError as ex:
            self.event(inc_id, "specialist_refused", requested_by, {"kind": kind, "reason": str(ex)}); return None
        inc = self.incident(inc_id); plan = self.current_plan(inc_id)
        local = "SP-%s" % secrets.token_hex(4); token = secrets.token_urlsafe(24)
        self.tokens[token] = {"incident_id": inc_id, "task": "specialist:" + spec["kind"], "run_ref": local, "expires": now() + 3600, "allowed": SP.READ_TOOLS}
        self.db.set("tool_tokens", self.tokens)
        request = {"gridpilot_api": cfg["base"] + "/tools/creao", "tool_token": token, "incident_id": inc_id, "question": str(question)[:2000],
                   "specialist": spec["kind"], "context": context or {},
                   "tool_protocol": "POST JSON to <gridpilot_api>/<tool_name> with header 'X-GridPilot-Tool-Token: <tool_token>'. GET <gridpilot_api>/manifest for schemas."}
        data = {"specialist": spec, "question": request["question"], "requested_by": requested_by, "auto": auto, "follow_up": follow_up, "context": context or {},
                "trigger_state": inc["state"], "plan_version_at_start": plan["version"] if plan else 0, "request": {**request, "tool_token": "[redacted]"}}
        self.db.ex("insert into agent_runs values(?,?,?,?,?,?,?,?,?)", (local, "creao-cli", None, inc_id, "specialist:" + spec["kind"], "creating_agent", now(), None, jd(data)))
        self.event(inc_id, "specialist_requested", requested_by, {"run_ref": local, "kind": spec["kind"], "name": spec["name"], "trigger": spec.get("trigger"), "question": request["question"][:300]})
        self.pending_inputs[local] = request
        try:
            key, aid = await self.ensure_agent(spec["kind"], spec["name"], SP.creation_text(spec, SP.READ_TOOLS))
        except Exception as ex:
            self.pending_inputs.pop(local, None)
            self.db.ex("update agent_runs set status='failed', completed=? where id=?", (now(), local))
            self.event(inc_id, "specialist_failed", "backend", {"run_ref": local, "error": str(ex)[:400]}); return None
        d = json.loads(self.db.one("select data from agent_runs where id=?", (local,))["data"]); d["agent_key"] = key
        self.db.ex("update agent_runs set data=?, status=? where id=?", (jd(d), "awaiting_agent" if not aid else "starting", local))
        if aid: await self._start_cli_run(local, aid)
        self.notify(); return local

    def specialist_summaries(self, inc_id):
        out = []
        for r in self.db.all("select * from agent_runs where incident_id=? and task like 'specialist:%' order by created", (inc_id,)):
            d = json.loads(r["data"] or "{}"); s = d.get("structured") or {}
            out.append({"run_ref": r["id"], "kind": r["task"].split(":", 1)[1], "status": r["status"], "finding": s.get("finding"), "root_cause": s.get("root_cause"),
                        "avoid_racks": s.get("avoid_racks"), "recommended_actions": s.get("recommended_actions"), "confidence": s.get("confidence"),
                        "backend_evaluation": d.get("evaluation")})
        return out

    async def cli_poller(self):
        while True:
            await asyncio.sleep(4)
            try:
                taken = {a["agent_id"] for a in self.db.all("select agent_id from specialists where agent_id is not null")}
                for a in self.db.all("select * from specialists where status='creating'"):
                    meta = json.loads(a["data"] or "{}")
                    try: cs = await asyncio.to_thread(IX.cli_creation_status, a["name"], a["creation_run_id"], meta.get("created_at"), taken)
                    except Exception: continue
                    st = cs.get("status")
                    if st in ("succeeded", "completed") and cs.get("agent_id"):
                        taken.add(cs["agent_id"])
                        self.db.ex("update specialists set status='ready', agent_id=? where key=?", (cs["agent_id"], a["key"]))
                        self.event(None, "creao_agent_created", "creao-v2:" + a["creation_run_id"], {"key": a["key"], "name": cs.get("name") or a["name"], "agent_id": cs["agent_id"]})
                    elif st in ("failed", "cancelled"):
                        self.db.ex("update specialists set status='failed' where key=?", (a["key"],))
                        self.event(None, "creao_agent_creation_failed", "creao-v2:" + a["creation_run_id"], {"key": a["key"], "status": st, "error": cs.get("error")})
                    elif now() - a["created"] > 900:
                        self.db.ex("update specialists set status='failed' where key=?", (a["key"],))
                        self.event(None, "creao_agent_creation_failed", "backend", {"key": a["key"], "error": "no agent appeared within 15 minutes"})
                for r in self.db.all("select * from agent_runs where provider='creao-cli' and status='awaiting_agent'"):
                    d = json.loads(r["data"] or "{}"); a = self.db.one("select * from specialists where key=?", (d.get("agent_key"),))
                    if a and a["status"] == "ready": await self._start_cli_run(r["id"], a["agent_id"])
                    elif a and a["status"] == "failed":
                        self.pending_inputs.pop(r["id"], None)
                        self.db.ex("update agent_runs set status='failed', completed=? where id=?", (now(), r["id"]))
                        self.event(r["incident_id"], "creao_run_failed_to_start", "backend", {"run_ref": r["id"], "error": "agent creation failed"})
                        await self._follow_up(r, d)
                for r in self.db.all("select * from agent_runs where provider='creao-cli' and status in ('pending','running','queued','starting') and provider_run_id is not null"):
                    await self.refresh_cli(r)
                for r in self.db.all("select * from agent_runs where provider='creao-cli' and task like 'specialist:%' and completed is null and created < ?", (now() - 420,)):
                    d = json.loads(r["data"] or "{}")
                    if d.get("follow_up") and not d.get("follow_up_done"):
                        self.event(r["incident_id"], "specialist_timeout", "backend", {"run_ref": r["id"], "note": "continuing replan without specialist findings"})
                        await self._follow_up(r, d)
            except Exception:
                traceback.print_exc()

    async def refresh_cli(self, r):
        try: run = await asyncio.to_thread(IX.cli_get_run, r["provider_run_id"])
        except Exception: return
        st = run.get("status")
        if st == r["status"]: return
        data = json.loads(r["data"] or "{}"); data["provider"] = {k: run.get(k) for k in ("status", "error", "model", "usage", "conversation_id") if k in run}
        if st in ("completed", "failed", "cancelled"):
            text = ((run.get("result") or {}).get("text") or "")
            data["result_text"] = text[:20000]
            if r["task"].startswith("specialist:"):
                data["structured"], data["structured_errors"] = SP.parse_result(text)
            else:
                data["structured"], data["structured_errors"] = self.parse_structured(text)
            self.db.ex("update agent_runs set status=?, completed=?, data=? where id=?", (st, now(), jd(data), r["id"]))
            self.event(r["incident_id"], "creao_run_" + st, "creao:" + r["provider_run_id"], {"run_ref": r["id"], "task": r["task"], "structured_valid": not data["structured_errors"]})
            if r["task"].startswith("specialist:"): await self.after_specialist(r, st, data)
            else: await self.after_creao(r, st, data)
        else:
            self.db.ex("update agent_runs set status=?, data=? where id=?", (st, jd(data), r["id"])); self.notify()

    async def after_specialist(self, r, st, data):
        inc = self.incident(r["incident_id"]); obj = data.get("structured") or {}
        acts = obj.get("recommended_actions") or []
        if st == "completed" and acts and inc:
            try:
                ev = await self.evaluate(acts, inc["rack"], "specialist %s" % r["task"].split(":", 1)[1])
                data["evaluation"] = {"actions": ev["actions"], "recover_s": ev["forecast"]["recover_s"], "final_gpu_c": ev["forecast"]["final_gpu_c"],
                                      "incremental_cost_usd": ev["finance"]["incremental_cost_usd"], "policy_decision": ev["policy"]["decision"]}
                self.event(inc["id"], "specialist_recommendation_evaluated", "backend", {"run_ref": r["id"], **{k: data["evaluation"][k] for k in ("recover_s", "final_gpu_c", "incremental_cost_usd", "policy_decision")}})
            except Exception as ex:
                data["evaluation_error"] = str(ex)[:400]
        if obj.get("finding") and inc:
            inc.setdefault("reports", []).append({"kind": "specialist:" + r["task"].split(":", 1)[1], "text": str(obj.get("finding"))[:4000], "by": "creao:" + (r["provider_run_id"] or r["id"]), "ts": now()})
            self.save_incident(inc)
        actor = "creao:" + (r["provider_run_id"] or r["id"]); extra = {}
        if r["task"] == "specialist:finance_explainer" and st == "completed" and data.get("invoice_id"):
            got = self.db.one("select id from ap_decisions where invoice_id=? and kind='explanation' and actor=?", (data["invoice_id"], actor))
            if not got:
                ans = (obj.get("answer") if isinstance(obj, dict) else None) or (data.get("result_text") or "")[:4000]
                if ans: self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (data["invoice_id"], now(), actor, "explanation", 0, str(ans)[:4000], jd({"question": data.get("question")}), "explanation"))
            extra["explained"] = data["invoice_id"]
        if r["task"] == "specialist:ap_analyst":
            n = self.db.one("select count(*) n from ap_decisions where actor=? and kind='agent_recommendation'", (actor,))["n"]
            extra["recommendations"] = n
        self.db.ex("update agent_runs set data=? where id=?", (jd(data), r["id"]))
        self.event(r["incident_id"], "specialist_completed", actor, {"run_ref": r["id"], "status": st, "has_recommendation": bool(data.get("evaluation")) or bool(extra.get("recommendations")) or bool(extra.get("explained")), **extra})
        for h in getattr(self, "hooks", []):
            try: h(r, st, data)
            except Exception: traceback.print_exc()
        await self._follow_up(r, data)

    async def _follow_up(self, r, data):
        if data.get("follow_up") != "replan" or data.get("follow_up_done"): return
        data["follow_up_done"] = True; self.db.ex("update agent_runs set data=? where id=?", (jd(data), r["id"]))
        inc = self.incident(r["incident_id"]); plan = self.current_plan(r["incident_id"])
        if not inc or inc["state"] not in ("REPLAN", "TRIAGING", "DENIED") or (plan and plan["version"] != data.get("plan_version_at_start")): return
        ref = await self.start_creao(inc["id"], "replan_after_failure", dict(data.get("context") or {}, specialist_findings=self.specialist_summaries(inc["id"])))
        ev = data.get("evaluation")
        if not ref and ev and ev.get("recover_s") is not None and ev.get("policy_decision") != "FORBIDDEN":
            await self.adopt_specialist(r["id"], "backend")

    async def adopt_specialist(self, run_ref, actor):
        r = self.db.one("select * from agent_runs where id=?", (run_ref,))
        if not r or not r["task"].startswith("specialist:"): raise ValueError("unknown specialist run")
        d = json.loads(r["data"] or "{}"); ev = d.get("evaluation"); s = d.get("structured") or {}
        if not ev: raise ValueError("this specialist returned no evaluated recommendation")
        meta = {"diagnosis": s.get("root_cause") or s.get("finding"), "evidence": s.get("evidence"), "candidate_actions": [ev["actions"]],
                "expected_thermal_outcomes": s.get("expected_outcome"), "expected_sla_risks": s.get("risks"), "confidence": s.get("confidence"),
                "reasoning_summary": "Recommendation from CREAO specialist %s (%s), re-evaluated deterministically by the backend." % (r["task"].split(":", 1)[1], r["provider_run_id"]),
                "origin": "creao-specialist", "adopted_by": actor}
        return await self.propose_plan(r["incident_id"], ev["actions"], "creao-specialist:%s:%s" % (r["task"].split(":", 1)[1], (r["provider_run_id"] or "")[:8]), meta)

    async def prepare_agents(self):
        out = {}
        for k, spec in [("remediation", None)] + list(SP.CATALOG.items()):
            if k == "remediation": key, aid = await self.ensure_agent("remediation", "GRIDPILOT Remediation Agent", SP_REMEDIATION_TEXT())
            else: key, aid = await self.ensure_agent(k, spec["name"], SP.creation_text(dict(spec, kind=k), SP.READ_TOOLS))
            out[k] = {"key": key, "agent_id": aid, "status": "ready" if aid else "creating"}
        return out

    # ------------------------------------------------------------------ forecasting (deterministic)
    def apply_actions(self, cfg, actions):
        cfg = copy.deepcopy(cfg)
        for a in actions:
            t = a["type"]
            if t == "migrate_workload": cfg["workloads"][a["workload"]]["rack"] = a["to_rack"]
            elif t == "set_gpu_power_limit": cfg["racks"][a["rack"]]["gpu_power_limit_w"] = int(a["watts"])
            elif t == "set_cooling_fan": cfg["cooling"][a["unit"]]["fan_pct"] = int(a["fan_pct"])
        return cfg

    def normalize_actions(self, actions):
        out = []; cfg = self.sim.config
        if not isinstance(actions, list): raise ValueError("actions must be a list")
        for a in actions:
            if isinstance(a, dict) and "type" not in a:
                alias = a.get("action") or a.get("kind")
                if alias: a = {**{k: v for k, v in a.items() if k not in ("action", "kind")}, "type": alias}
            if not isinstance(a, dict) or "type" not in a: raise ValueError("each action needs a type")
            t = a["type"]
            if t == "migrate_workload":
                w = a.get("workload"); to = a.get("to_rack")
                if w not in cfg["workloads"]: raise ValueError("unknown workload %s" % w)
                if to not in cfg["racks"]: raise ValueError("unknown rack %s" % to)
                out.append({"type": t, "workload": w, "from_rack": cfg["workloads"][w]["rack"], "to_rack": to})
            elif t == "set_gpu_power_limit":
                out.append({"type": t, "rack": a.get("rack"), "watts": a.get("watts"), "from_watts": cfg["racks"].get(a.get("rack"), {}).get("gpu_power_limit_w")})
            elif t == "set_cooling_fan":
                out.append({"type": t, "unit": a.get("unit"), "fan_pct": a.get("fan_pct"), "from_pct": cfg["cooling"].get(a.get("unit"), {}).get("fan_pct")})
            else:
                out.append(dict(a))
        return out

    def _run_forecast(self, base, actions, horizon=600.0, focus="R17"):
        fr, fc = S.rack_rc(focus); fi = fr * 8 + fc
        c = base.clone()
        if actions:
            c.load_config(self.apply_actions(c.config, [a for a in actions if a["type"] in ("migrate_workload", "set_gpu_power_limit", "set_cooling_fan")]))
        e0 = c.energy_kwh; series = []; recover = None; breach_s = {}; peak_targets = {}
        targets = [a["to_rack"] for a in actions if a.get("type") == "migrate_workload"]
        below_since = None
        for k in range(int(horizon / 5)):
            c.step(5.0, speed=1, sub=1.0)
            g = float(c.gpu_t[fi]); series.append([round(c.t - base.t), round(g, 1)])
            if g < S.THRESH["gpu_recovered_c"]:
                below_since = below_since if below_since is not None else c.t - base.t
            else: below_since = None
            if recover is None and below_since is not None and (c.t - base.t) - below_since >= 20: recover = below_since
            for wid, v in c.workload_latency().items():
                if v["breach"]: breach_s[wid] = breach_s.get(wid, 0) + 5
            for t in targets:
                tr, tc = S.rack_rc(t); peak_targets[t] = max(peak_targets.get(t, 0), float(c.gpu_t[tr * 8 + tc]))
        lat_end = c.workload_latency()
        return {"series": series, "recover_s": recover, "final_gpu_c": round(float(c.gpu_t[fi]), 1), "final_inlet_c": round(float(c.inlet[fi]), 1),
                "peak_gpu_c": round(max(v for _, v in series), 1), "energy_kwh": c.energy_kwh - e0,
                "breach_minutes": {w: s / 60 for w, s in breach_s.items()}, "breach_at_end": {w: v["breach"] for w, v in lat_end.items() if v["breach"]},
                "peak_target_gpu_c": {k: round(v, 1) for k, v in peak_targets.items()}, "max_gpu_end_c": round(float(c.gpu_t.max()), 1), "horizon_min": horizon / 60}

    async def evaluate(self, actions, focus="R17", label=None):
        """Deterministic forecast + finance + policy preview. No state change."""
        acts = self.normalize_actions(actions)
        base = self.sim.clone()
        fc, bl = await asyncio.gather(asyncio.to_thread(self._run_forecast, base, acts, 600.0, focus),
                                      asyncio.to_thread(self._run_forecast, base, [], 600.0, focus))
        fc["baseline_energy_kwh"] = bl["energy_kwh"]
        fin = FIN.evaluate(acts, self.sim.config, fc, S.CUSTOMERS)
        bfc = dict(bl); bfc["baseline_energy_kwh"] = bl["energy_kwh"]
        base_fin = FIN.evaluate([], self.sim.config, bfc, S.CUSTOMERS)
        pol = POL.evaluate_plan(acts, self.sim.config, fin["incremental_cost_usd"], PROTECTED)
        risk = "low" if (fc["recover_s"] is not None and fc["max_gpu_end_c"] < 82) else ("medium" if fc["final_gpu_c"] < 88 else "high")
        return {"label": label, "actions": acts, "forecast": {k: v for k, v in fc.items() if k != "energy_kwh"},
                "baseline": {"recover_s": bl["recover_s"], "final_gpu_c": bl["final_gpu_c"], "peak_gpu_c": bl["peak_gpu_c"], "series": bl["series"],
                             "sla_exposure_usd": base_fin["sla_exposure_usd"]},
                "finance": fin, "policy": pol, "operational_risk": risk,
                "net_expected_benefit_usd": round(base_fin["sla_exposure_usd"] - fin["sla_exposure_usd"] - fin["incremental_cost_usd"] - fin["energy_delta_usd"], 2),
                "assumptions": FIN.ASSUMPTIONS, "computed_at_sim_t": round(self.sim.t, 1),
                "model_note": "Approximate thermal/airflow simulation forecast (600 s horizon). Not engineering-grade CFD."}

    # ------------------------------------------------------------------ plans
    async def propose_plan(self, inc_id, actions, author, meta):
        inc = self.incident(inc_id)
        if not inc: raise ValueError("unknown incident")
        if inc["state"] not in OPEN_FOR_PLANS:
            raise ValueError("incident %s is in state %s; plans cannot be proposed now" % (inc_id, inc["state"]))
        ev = await self.evaluate(actions, inc["rack"])
        prev = self.db.one("select id, max(version) v from plans where incident_id=?", (inc_id,))
        pid = prev["id"] if prev and prev["id"] else "PLN-%s" % inc_id.split("-")[1]
        ver = (prev["v"] or 0) + 1 if prev and prev["id"] else 1
        for p in self.db.all("select id, version from plans where incident_id=? and status in ('proposed','awaiting_approval','approved')", (inc_id,)):
            self.db.ex("update plans set status='superseded' where id=? and version=?", (p["id"], p["version"]))
        for a in self.db.all("select id from approvals where incident_id=? and status in ('pending','code_issued','approved')", (inc_id,)):
            self.db.ex("update approvals set status='invalidated' where id=?", (a["id"],))
        data = {"actions": ev["actions"], "evaluation": ev, "meta": meta}
        self.db.ex("insert into plans values(?,?,?,?,?,?,?)", (pid, ver, inc_id, now(), author, "proposed", jd(data)))
        inc = self.incident(inc_id)
        self.transition(inc, "PLAN_PROPOSED", author, "plan %s v%d proposed" % (pid, ver), {"plan_id": pid, "plan_version": ver})
        inc = self.incident(inc_id)
        dec = ev["policy"]["decision"]
        self.transition(inc, "POLICY_CHECKED", "policy-engine", "decision %s" % dec, {"plan_id": pid, "plan_version": ver, "policy": ev["policy"]})
        inc = self.incident(inc_id)
        if dec == "FORBIDDEN":
            self.db.ex("update plans set status='denied' where id=? and version=?", (pid, ver))
            self.transition(inc, "DENIED", "policy-engine", "; ".join(r["rule"] for r in ev["policy"]["actions"] + ev["policy"]["plan_rules"] if r["decision"] == "FORBIDDEN"))
        elif dec == "APPROVAL_REQUIRED":
            self.db.ex("update plans set status='awaiting_approval' where id=? and version=?", (pid, ver))
            aid = "APR-%s" % secrets.token_hex(3)
            self.db.ex("insert into approvals values(?,?,?,?,?,?,?,?)", (aid, inc_id, pid, ver, now(), now() + APPROVAL_TTL_S, "pending", jd({})))
            self.transition(inc, "AWAITING_APPROVAL", "policy-engine", "human approval required", {"approval_id": aid, "plan_id": pid, "plan_version": ver})
            if self.db.get("auto_call", True) and not self.active_call(inc_id):
                self.spawn(self.start_call(inc_id, "backend"))
        else:
            self.db.ex("update plans set status='approved' where id=? and version=?", (pid, ver))
        return {"plan_id": pid, "plan_version": ver, "policy_decision": dec, "evaluation": ev}

    def current_plan(self, inc_id):
        r = self.db.one("select * from plans where incident_id=? order by version desc limit 1", (inc_id,))
        if not r: return None
        d = json.loads(r["data"]); d.update({k: r[k] for k in ("id", "version", "author", "status", "created")}); return d

    # ------------------------------------------------------------------ approvals
    def pending_approval(self, inc_id):
        return self.db.one("select * from approvals where incident_id=? and status in ('pending','code_issued') order by created desc limit 1", (inc_id,))

    async def renew_approval(self, inc_id, actor):
        """An approval window lapsed while the plan was still current. Re-evaluate the exact same plan against live telemetry;
        if it is still valid, open a fresh approval window for the same plan version. Otherwise hand it back to CREAO to replan."""
        inc = self.incident(inc_id); plan = self.current_plan(inc_id)
        if not inc or inc["state"] != "AWAITING_APPROVAL" or not plan: return False, "nothing to renew (state %s)" % (inc or {}).get("state"), None
        live = self.pending_approval(inc_id)
        if live and now() <= live["expires"]: return True, "approval window still open", live["id"]
        ev = await self.evaluate(plan["actions"], inc["rack"], "renewal re-check")
        dec = ev["policy"]["decision"]; rec = ev["forecast"].get("recover_s")
        self.db.ex("update approvals set status='expired' where incident_id=? and status in ('pending','code_issued')", (inc_id,))
        summary = {"plan_id": plan["id"], "plan_version": plan["version"], "policy": dec, "recover_s": rec,
                   "incremental_cost_usd": ev["finance"]["incremental_cost_usd"], "was": {"recover_s": plan["evaluation"]["forecast"].get("recover_s"), "incremental_cost_usd": plan["evaluation"]["finance"]["incremental_cost_usd"]}}
        if dec != "APPROVAL_REQUIRED" or rec is None:
            self.event(inc_id, "approval_renewal_rejected", actor, {**summary, "reason": "plan no longer valid on live telemetry"})
            await self.start_creao(inc_id, "replan_after_failure", {"rejection": "approval lapsed and re-check failed: policy %s, recovery %s" % (dec, rec)})
            return False, "plan no longer holds on live telemetry; CREAO asked to replan", None
        aid = "APR-%s" % secrets.token_hex(3)
        self.db.ex("insert into approvals values(?,?,?,?,?,?,?,?)", (aid, inc_id, plan["id"], plan["version"], now(), now() + APPROVAL_TTL_S, "pending", jd({"renewal": summary})))
        self.event(inc_id, "approval_renewed", actor, {**summary, "approval_id": aid})
        return True, "re-checked on live telemetry; approval window renewed for %s v%d" % (plan["id"], plan["version"]), aid

    def verify_approval(self, inc_id, plan_id, plan_version, channel, actor, code=None, evidence=None):
        inc = self.incident(inc_id); plan = self.current_plan(inc_id); apr = self.pending_approval(inc_id)
        if not inc or inc["state"] != "AWAITING_APPROVAL": return False, "incident is not awaiting approval (state %s)" % (inc or {}).get("state")
        if not plan or plan["id"] != plan_id or int(plan_version) != plan["version"]:
            return False, "stale approval: current plan is %s v%s" % (plan and plan["id"], plan and plan["version"])
        if not apr or apr["plan_version"] != plan["version"]: return False, "no pending approval for this plan version"
        if now() > apr["expires"]:
            self.db.ex("update approvals set status='expired' where id=?", (apr["id"],)); return False, "approval window expired; use Re-check & renew"
        d = json.loads(apr["data"]); d.update({"channel": channel, "actor": actor, "decided_at": now(), "plan_version": plan["version"], **(evidence or {})})
        self.db.ex("update approvals set status='approved', data=? where id=?", (jd(d), apr["id"]))
        self.db.ex("update plans set status='approved' where id=? and version=?", (plan_id, plan["version"]))
        self.transition(inc, "APPROVED", actor, "verified %s approval of %s v%d" % (channel, plan_id, plan["version"]), {"approval_id": apr["id"], "channel": channel, **({"operator_said": evidence.get("operator_said"), "call_id": evidence.get("call_id")} if evidence else {})})
        self.spawn(self.after_approval(inc_id, plan_id, plan["version"]))
        return True, "approved"

    async def after_approval(self, inc_id, pid, ver):
        cfg = self.creao_config()
        if (cfg["app_id"] or cfg["cli"]) and cfg["base"]:
            await self.start_creao(inc_id, "execute_approved_plan", {"approved_plan": {"plan_id": pid, "plan_version": ver},
                                    "instruction": "Verify the incident is unchanged, then call request_execution for exactly this plan version, then monitor via get_incident_context and submit_report."})
        else:
            self.event(inc_id, "awaiting_execution_trigger", "backend", {"reason": "CREAO not configured; operator may trigger execution from the console"})

    def issue_voice_code(self, inc_id, plan_id, plan_version):
        apr = self.pending_approval(inc_id); plan = self.current_plan(inc_id)
        if not apr or not plan or plan["id"] != plan_id or int(plan_version) != plan["version"]:
            return None, "no pending approval for %s v%s (current %s v%s)" % (plan_id, plan_version, plan and plan["id"], plan and plan["version"])
        code = "%06d" % secrets.randbelow(10 ** 6)
        d = json.loads(apr["data"]); d["code_hash"] = hashlib.sha256(code.encode()).hexdigest(); d["code_issued_at"] = now(); d["code_plain_for_owner"] = code
        self.db.ex("update approvals set status='code_issued', data=? where id=?", (jd(d), apr["id"]))
        self.event(inc_id, "approval_code_issued", "backend", {"approval_id": apr["id"], "plan_id": plan_id, "plan_version": plan["version"]})
        return code, "code issued"

    def reject(self, inc_id, actor, reason):
        inc = self.incident(inc_id); plan = self.current_plan(inc_id)
        if inc["state"] != "AWAITING_APPROVAL": raise ValueError("not awaiting approval")
        self.db.ex("update plans set status='rejected' where id=? and version=?", (plan["id"], plan["version"]))
        self.db.ex("update approvals set status='rejected' where incident_id=? and status in ('pending','code_issued')", (inc_id,))
        self.transition(inc, "REPLAN", actor, "operator rejected %s v%d: %s" % (plan["id"], plan["version"], reason))
        self.spawn(self.start_creao(inc_id, "replan_after_failure", {"rejection": {"plan_id": plan["id"], "plan_version": plan["version"], "reason": reason}}))

    def escalate(self, inc_id, actor, reason):
        inc = self.incident(inc_id)
        self.transition(inc, "ESCALATED", actor, reason)

    # ------------------------------------------------------------------ execution
    async def request_execution(self, inc_id, plan_id, plan_version, actor):
        inc = self.incident(inc_id); plan = self.current_plan(inc_id)
        if not plan or plan["id"] != plan_id or int(plan_version) != plan["version"]:
            return False, "stale plan: current is %s v%s" % (plan and plan["id"], plan and plan["version"])
        if self.db.one("select 1 from executions where plan_id=? and plan_version=?", (plan_id, plan["version"])):
            return False, "duplicate: this plan version was already executed"
        pol = POL.evaluate_plan(plan["actions"], self.sim.config, plan["evaluation"]["finance"]["incremental_cost_usd"], PROTECTED)
        if pol["decision"] == "FORBIDDEN": return False, "policy forbids this plan"
        if pol["decision"] == "APPROVAL_REQUIRED":
            apr = self.db.one("select * from approvals where incident_id=? and plan_id=? and plan_version=? and status='approved'", (inc_id, plan_id, plan["version"]))
            if not apr or inc["state"] != "APPROVED": return False, "verified human approval for this exact plan version is required"
        elif inc["state"] not in ("POLICY_CHECKED", "APPROVED"):
            return False, "incident state %s does not allow execution" % inc["state"]
        ex_id = "EXE-%s" % secrets.token_hex(3)
        self.db.ex("insert into executions values(?,?,?,?,?,?,?)", (ex_id, inc_id, plan_id, plan["version"], now(), "started", jd({"actor": actor, "policy": pol})))
        self.transition(inc, "EXECUTING", actor, "execution %s of %s v%d" % (ex_id, plan_id, plan["version"]), {"execution_id": ex_id})
        self.spawn(self.execute(inc_id, ex_id, plan))
        return True, ex_id

    def _exec_update(self, ex_id, state, **kw):
        r = self.db.one("select data from executions where id=?", (ex_id,)); d = json.loads(r["data"]); d.update(kw)
        d.setdefault("steps", [])
        self.db.ex("update executions set state=?, data=? where id=?", (state, jd(d), ex_id)); self.notify(); return d

    def _step(self, ex_id, name, ok, detail):
        r = self.db.one("select data from executions where id=?", (ex_id,)); d = json.loads(r["data"])
        d.setdefault("steps", []).append({"name": name, "ok": ok, "detail": detail, "ts": now(), "sim_t": round(self.sim.t, 1)})
        self.db.ex("update executions set data=? where id=?", (jd(d), ex_id)); self.notify()

    async def execute(self, inc_id, ex_id, plan):
        inc = self.incident(inc_id); actions = plan["actions"]
        try:
            snap_sha = CS.snapshot("pre-" + ex_id)
            self._step(ex_id, "snapshot", True, "tag pre-%s -> %s" % (ex_id, snap_sha[:10]))
            cfg_now = CS.read_tree()
            cand = self.apply_actions(cfg_now, actions)
            wt, branch, diff, changed = CS.build_candidate(cand, ex_id.lower())
            self._step(ex_id, "candidate_built", True, "isolated worktree %s, %d files changed" % (wt, len(changed)))
            self._exec_update(ex_id, "validating", snapshot_sha=snap_sha, diff=diff, changed_paths=changed)
            inc = self.incident(inc_id); self.transition(inc, "VALIDATING", "backend", "validating candidate diff", {"execution_id": ex_id})
            await asyncio.sleep(0.6)
            checks = await self.validate_candidate(wt, cfg_now, actions, changed, plan, inc["rack"])
            self._exec_update(ex_id, "validating", validation=checks)
            if not all(c["ok"] for c in checks):
                CS.discard_candidate(wt, branch)
                bad = [c for c in checks if not c["ok"]]
                self._step(ex_id, "validation_failed", False, "; ".join(c["name"] + ": " + c["detail"] for c in bad))
                self._exec_update(ex_id, "validation_failed")
                inc = self.incident(inc_id); self.transition(inc, "REPLAN", "backend", "validation failed before application", {"failed_checks": bad})
                self.spawn(self.special_case(inc_id, "capacity_planner", {"failure": {"stage": "validation", "checks": bad}},
                                             "The candidate configuration failed validation: %s. Find feasible placements and a corrected action set." % "; ".join(c["name"] for c in bad)))
                return
            sha = CS.commit_candidate(wt, branch, "%s: apply %s v%d for %s" % (ex_id, plan["id"], plan["version"], inc_id))
            self._step(ex_id, "committed", True, "main -> %s" % sha[:10])
            applied = CS.read_tree()
            self.sim.load_config(applied)
            self._step(ex_id, "applied_to_simulator", True, "simulator reloaded configuration %s" % sha[:10])
            self.record_usage(inc_id, ex_id, actions, cfg_now)
            if getattr(self, "desk", None):
                try: self.desk.accrue_incident(inc_id, [x for x in (dict(json.loads(u["data"]), kind=u["kind"]) for u in self.db.all("select * from usage where incident_id=?", (inc_id,))) if x.get("execution_id") == ex_id])
                except Exception: traceback.print_exc()
            self._exec_update(ex_id, "monitoring", applied_sha=sha, applied_at_sim_t=round(self.sim.t, 1))
            inc = self.incident(inc_id)
            inc["monitor"] = {"execution_id": ex_id, "start_sim_t": self.sim.t, "below_since": None, "targets": [a["to_rack"] for a in actions if a["type"] == "migrate_workload"]}
            self.save_incident(inc)
            self.transition(inc, "MONITORING_RECOVERY", "backend", "configuration %s applied; monitoring recovery" % sha[:10], {"execution_id": ex_id, "sha": sha})
            if self.fail_armed:
                self.fail_armed = False
                tgt = inc["monitor"]["targets"]
                if tgt:
                    tr, tc = S.rack_rc(tgt[0])
                    self.sim.rack_res[tr * 8 + tc] = 2.1
                    self.event(inc_id, "fault_injected", "demo-control", {"what": "armed fault fired: fan-module failure on target rack %s after apply" % tgt[0]})
        except Exception as e:
            traceback.print_exc()
            self._step(ex_id, "error", False, str(e)[:500])
            inc = self.incident(inc_id)
            if inc["state"] in ("EXECUTING", "VALIDATING"):
                self.transition(inc, "REPLAN", "backend", "execution error: %s" % str(e)[:200])

    async def validate_candidate(self, wt, cfg_now, actions, changed, plan, focus):
        checks = []
        try:
            cand = CS.read_tree(wt); ok = True; det = "YAML parsed; types valid"
            for rid, rk in cand["racks"].items():
                if not isinstance(rk.get("gpu_power_limit_w"), int): ok, det = False, "%s gpu_power_limit_w not int" % rid
            for wid, w in cand["workloads"].items():
                if w.get("rack") not in cand["racks"]: ok, det = False, "%s rack invalid" % wid
            checks.append({"name": "schema", "ok": ok, "detail": det})
        except Exception as e:
            return [{"name": "schema", "ok": False, "detail": str(e)[:200]}]
        allowed = set()
        for a in actions:
            if a["type"] == "migrate_workload": allowed.add("infrastructure/scheduler/workloads.yaml")
            if a["type"] == "set_gpu_power_limit": allowed.add("infrastructure/racks/%s.yaml" % a["rack"])
            if a["type"] == "set_cooling_fan": allowed.add("infrastructure/cooling/policies.yaml")
        extra = sorted(set(changed) - allowed)
        checks.append({"name": "diff_matches_plan", "ok": not extra and bool(changed), "detail": "changed %s; allowed %s%s" % (changed, sorted(allowed), "; unexpected " + str(extra) if extra else "")})
        # semantic: the candidate equals exactly the approved plan applied to current config
        expect = self.apply_actions(cfg_now, actions)
        checks.append({"name": "plan_version_consistency", "ok": expect == cand, "detail": "candidate == current config + %s v%d actions" % (plan["id"], plan["version"])})
        used = {r: 0 for r in cand["racks"]}
        for w in cand["workloads"].values(): used[w["rack"]] += w["gpus"]
        over = [r for r, u in used.items() if u > cand["racks"][r]["gpu_count"]]
        checks.append({"name": "rack_capacity", "ok": not over, "detail": "over capacity: %s" % over if over else "all racks within 8 GPUs"})
        bad_t = []
        for a in actions:
            if a["type"] == "migrate_workload":
                r, c = S.rack_rc(a["to_rack"]); idx = r * 8 + c
                if cand["racks"][a["to_rack"]]["status"] != "in_service" or self.sim.gpu_t[idx] > S.THRESH["gpu_warning_c"]: bad_t.append(a["to_rack"])
        checks.append({"name": "placement_constraints", "ok": not bad_t, "detail": "unhealthy targets %s" % bad_t if bad_t else "targets in service and below warning"})
        pw = [r for r, rk in cand["racks"].items() if not (POL.POWER_RANGE_W[0] <= rk["gpu_power_limit_w"] <= POL.POWER_RANGE_W[1])]
        checks.append({"name": "gpu_power_bounds", "ok": not pw, "detail": "out of bounds %s" % pw if pw else "all limits within 300-450 W"})
        ev = await self.evaluate(actions, focus)
        sla_ok = all(not (w in ev["forecast"]["breach_at_end"] and S.CUSTOMERS[cand["workloads"][w]["customer"]]["protected"]) for w in cand["workloads"])
        checks.append({"name": "sla_restrictions", "ok": sla_ok, "detail": "no protected workload breaching at forecast horizon" if sla_ok else "protected workload breach forecast"})
        feas = ev["forecast"]["recover_s"] is not None and all(v < S.THRESH["gpu_critical_c"] for v in ev["forecast"]["peak_target_gpu_c"].values())
        checks.append({"name": "cooling_feasibility", "ok": feas, "detail": "forecast recovery at %ss; target peaks %s" % (ev["forecast"]["recover_s"], ev["forecast"]["peak_target_gpu_c"])})
        return checks

    def record_usage(self, inc_id, ex_id, actions, cfg_before):
        for a in actions:
            if a["type"] != "migrate_workload": continue
            wl = cfg_before["workloads"][a["workload"]]
            if cfg_before["racks"][a["to_rack"]].get("reserve_pool"):
                uid = "USG-%s" % secrets.token_hex(3)
                self.db.ex("insert into usage values(?,?,?,?)", (uid, inc_id, "reserve_lease", jd({"id": uid, "execution_id": ex_id, "workload": a["workload"], "rack": a["to_rack"],
                           "gpus": wl["gpus"], "start_sim_t": self.sim.t, "end_sim_t": None})))
            if cfg_before["racks"][wl["rack"]]["zone"] != cfg_before["racks"][a["to_rack"]]["zone"]:
                tid = "XFR-%s" % secrets.token_hex(3)
                route = "%s->%s" % (cfg_before["racks"][wl["rack"]]["zone"], cfg_before["racks"][a["to_rack"]]["zone"])
                self.db.ex("insert into usage values(?,?,?,?)", (tid, inc_id, "transfer", jd({"id": tid, "execution_id": ex_id, "workload": a["workload"], "gb": wl["state_gb"],
                           "route": route, "ts": round(self.sim.t, 1)})))
                self.cost["transfer_usd"] += wl["state_gb"] * FIN.INTER_ZONE_TRANSFER_USD_GB
        self.event(inc_id, "usage_metered", "backend", {"execution_id": ex_id})

    def monitor(self, inc):
        m = inc.get("monitor") or {}
        s = self.sim; r, c = S.rack_rc(inc["rack"]); idx = r * 8 + c
        g = float(s.gpu_t[idx]); el = s.t - m.get("start_sim_t", s.t)
        # safety: targets or any other rack must not go critical
        for t in m.get("targets", []):
            tr, tc = S.rack_rc(t)
            if s.gpu_t[tr * 8 + tc] > S.THRESH["gpu_critical_c"]:
                return self.spawn(self.rollback(inc["id"], "target rack %s exceeded %.0f°C (%.1f°C)" % (t, S.THRESH["gpu_critical_c"], s.gpu_t[tr * 8 + tc])))
        if g < S.THRESH["gpu_recovered_c"]:
            if m.get("below_since") is None: m["below_since"] = s.t; inc["monitor"] = m; self.save_incident(inc)
            elif s.t - m["below_since"] >= RECOVERY_HOLD_S:
                inc["resolved_sim_t"] = s.t; inc["mttr_sim_s"] = round(s.t - inc["detected_sim_t"], 1); inc["resolved_at"] = now()
                inc["mttr_wall_s"] = round(now() - inc["detected_at"], 1); self.save_incident(inc)
                self._exec_update(m["execution_id"], "completed", recovered_at_sim_t=round(s.t, 1))
                self.transition(inc, "RESOLVED", "backend", "%s GPU hotspot %.1f°C held below %.0f°C for %.0fs" % (inc["rack"], g, S.THRESH["gpu_recovered_c"], RECOVERY_HOLD_S))
                self.spawn(self.closeout(inc["id"]))
        else:
            if m.get("below_since") is not None: m["below_since"] = None; inc["monitor"] = m; self.save_incident(inc)
            if el > RECOVERY_DEADLINE_S:
                self.spawn(self.rollback(inc["id"], "no recovery within %.0fs (GPU %.1f°C)" % (RECOVERY_DEADLINE_S, g)))

    async def rollback(self, inc_id, reason):
        inc = self.incident(inc_id)
        if inc["state"] != "MONITORING_RECOVERY": return
        ex_id = inc["monitor"]["execution_id"]
        ex = json.loads(self.db.one("select data from executions where id=?", (ex_id,))["data"])
        self.transition(inc, "ROLLING_BACK", "backend", reason, {"execution_id": ex_id})
        try:
            sha, diff, before = CS.restore(ex["snapshot_sha"], "%s: rollback to snapshot pre-%s (%s)" % (ex_id, ex_id, reason))
            restored = CS.read_tree()
            verify_diff = CS.git("diff", ex["snapshot_sha"], "HEAD", "--", "infrastructure")
            self.sim.load_config(restored)
            ok = verify_diff.strip() == ""
            self._step(ex_id, "rolled_back", ok, "restored snapshot %s as %s; verification %s" % (ex["snapshot_sha"][:10], sha[:10], "tree identical" if ok else "MISMATCH"))
            self._exec_update(ex_id, "rolled_back", rollback={"sha": sha, "diff": diff, "reason": reason, "verified": ok})
            for u in self.db.all("select * from usage where incident_id=? and kind='reserve_lease'", (inc_id,)):
                d = json.loads(u["data"])
                if d["end_sim_t"] is None: d["end_sim_t"] = self.sim.t; self.db.ex("update usage set data=? where id=?", (jd(d), u["id"]))
            er = self.db.one("select plan_id, plan_version from executions where id=?", (ex_id,))
            self.db.ex("update plans set status='rolled_back' where id=? and version=?", (er["plan_id"], er["plan_version"]))
            inc = self.incident(inc_id); inc["monitor"] = None; self.save_incident(inc)
            self.transition(inc, "REPLAN", "backend", "rollback verified; requesting revised plan", {"rollback_sha": sha})
            self.spawn(self.special_case(inc_id, "thermal_forensics", {"failure": {"stage": "post_apply", "reason": reason, "rolled_back_to": ex["snapshot_sha"][:10], "targets": (ex.get("changed_paths") or [])}},
                                         "The applied plan failed after application and was rolled back (%s). Why did it fail, which racks must be avoided, and what corrected action set recovers the incident rack?" % reason))
        except Exception as e:
            traceback.print_exc()
            inc = self.incident(inc_id); self.transition(inc, "ESCALATED", "backend", "rollback error: %s" % str(e)[:200])

    # ------------------------------------------------------------------ finance closeout
    def usage_records(self, inc_id):
        usage, transfers = [], []
        for u in self.db.all("select * from usage where incident_id=?", (inc_id,)):
            d = json.loads(u["data"])
            if u["kind"] == "reserve_lease":
                end = d["end_sim_t"] if d["end_sim_t"] is not None else self.sim.t
                d["gpu_hours"] = round(d["gpus"] * (end - d["start_sim_t"]) / 3600.0, 4); usage.append(d)
            else: transfers.append(d)
        return usage, transfers

    async def closeout(self, inc_id):
        inc = self.incident(inc_id)
        self.transition(inc, "FINANCIAL_RECONCILIATION", "backend", "metering incident costs and reconciling provider invoice")
        for u in self.db.all("select * from usage where incident_id=? and kind='reserve_lease'", (inc_id,)):
            d = json.loads(u["data"])
            if d["end_sim_t"] is None: d["end_sim_t"] = self.sim.t; self.db.ex("update usage set data=? where id=?", (jd(d), u["id"]))
        usage, transfers = self.usage_records(inc_id)
        inv = FIN.invoice_for(inc_id, usage, transfers)
        inv_id = "INV-ACX-%s" % inc_id.split("-")[1]
        rec = FIN.reconcile(inv, usage, transfers)
        lease_usd = sum(l["usd"] for l in inv["lines"] if l["sku"] == "RESERVE-GPU-H100")
        self.cost["lease_usd"] += lease_usd
        self.db.ex("insert or replace into invoices values(?,?,?,?)", (inv_id, inc_id, now(), jd({"invoice": inv, "reconciliation": rec, "usage": usage, "transfers": transfers})))
        inc = self.incident(inc_id)
        inc["finance"] = {"invoice_id": inv_id, "incurred_usd": inv["total_usd"], "reconciliation_status": rec["status"]}
        self.save_incident(inc)
        self.event(inc_id, "invoice_reconciled", "finance-engine", {"invoice_id": inv_id, **rec})
        if getattr(self, "desk", None):
            try: self.desk.ingest_incident_invoice(inc_id, inv, usage)
            except Exception: traceback.print_exc()
        self.transition(inc, "CLOSED", "backend", "incident closed; invoice %s %s" % (inv_id, rec["status"]))
        if rec.get("status") != "RECONCILED":
            self.spawn(self.spawn_specialist(inc_id, "finops_auditor", "Invoice %s did not reconcile (%s). Attribute the variance and state what to dispute." % (inv_id, rec.get("status")),
                                             requested_by="backend", context={"invoice_id": inv_id}, auto=True))
        await self.start_creao(inc_id, "post_incident_summary", {"invoice_id": inv_id})

    # ------------------------------------------------------------------ voice (Cartesia)
    def active_call(self, inc_id):
        return self.db.one("select * from calls where incident_id=? and status in ('initiating','queued','ringing','in_progress','connected','active') order by created desc limit 1", (inc_id,))

    async def start_call(self, inc_id, actor):
        import voice
        return await voice.start_call(self, inc_id, actor)

    # ------------------------------------------------------------------ demo controls
    def inject(self, kind):
        s = self.sim
        if kind == "thermal":
            s.degrade_unit(1, 0.28, 0.5)
            for w in ["inference_api_07", "inference_api_12", "batch_embed_03"]:
                if s.config["workloads"].get(w, {}).get("rack") == "R17": s.surge[w] = 0.15
        elif kind == "repair_c02":
            s.degrade_unit(1, 1.0, 1.0)
        elif kind == "repair_rack_faults":
            s.rack_res[:] = 1.0
        elif kind == "arm_post_apply_failure":
            self.fail_armed = True
        self.event(None, "demo_injection", "demo-control:" + self.owner[:6], {"kind": kind})

    def status(self):
        inc = self.open_incident() or (lambda r: self.incident(r["id"]) if r else None)(self.db.one("select id from incidents order by created desc limit 1"))
        return inc


def SP_REMEDIATION_TEXT():
    brief = open(os.path.join(CODE_ROOT, "docs", "CREAO_AGENT.md")).read()
    return brief
