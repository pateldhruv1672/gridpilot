"""Scenario runner: realistic multi-fault situations that put every GRIDPILOT agent to work.

Only the STIMULI are scripted (a cooling unit degrades, a bearing starts wearing, a fraudulent invoice arrives, the grid price spikes,
someone badges in after hours). Every response is real: deterministic detectors, CREAO agents, policy, approvals, execution and
verification run exactly as in production. The board tracks each participant from the recorded events, not from a script.
"""
import time, json, asyncio

INVOICES = {
    "bank_change": {"vendor": "NBT", "number": "NBT-%d", "remit_to": "Chase Bank ••7781", "from_email": "billing@northbeam-transit-billing.com",
                    "note": "We moved banks. Please pay this and future invoices to the new account today.", "lines": [{"sku": "INTERZONE-GB", "qty": 40, "rate": 0.25}]},
    "over_billed": {"vendor": "NBT", "number": "NBT-%d", "lines": [{"sku": "INTERZONE-GB", "qty": 900, "rate": 0.25}]},
    "price_creep": {"vendor": "ACX", "number": "ACX-%d", "lines": [{"sku": "RESERVE-GPU-H100", "qty": 6, "rate": 3.95}]},
}

PARTICIPANTS = {
    "detector": ("Thermal detector", "rules"), "remediation": ("Remediation Agent", "CREAO"), "specialists": ("CREAO specialists", "CREAO"),
    "policy": ("Policy engine", "rules"), "executor": ("Config executor (git)", "rules"), "voice": ("Cartesia voice operator", "Cartesia"),
    "predictor": ("Failure predictor", "rules"), "reliability": ("Reliability Agent", "CREAO"), "ap": ("AP Analyst", "CREAO"),
    "matcher": ("Three-way matcher", "rules"), "explainer": ("Finance Explainer", "CREAO"), "energy": ("Energy agent (battery)", "rules"),
    "security": ("Security watcher", "rules"), "v2": ("Ghost v2 agents & detectors", "CREAO"),
}

SCENARIOS = [
    {"id": "cooling_peak", "title": "Cooling failure at peak inference load", "summary": "CRAH C02 loses most of its capacity while R17 serves protected inference traffic. Watch the detector open an incident, the Remediation Agent diagnose and simulate fixes, the policy engine gate them, a human approve (by voice if Cartesia is on), git apply the change and the finance closeout reconcile the cost.",
     "participants": ["detector", "remediation", "specialists", "policy", "voice", "executor", "v2"], "stages": [(0, "inject", "thermal")]},
    {"id": "failed_change", "title": "A fix that fails after deploy", "summary": "Same cooling fault, but the first applied change breaks a validation check. The executor rolls back from git, a specialist investigates and the Remediation Agent replans around the failed target.",
     "participants": ["detector", "remediation", "specialists", "policy", "executor"], "stages": [(0, "inject", "arm_post_apply_failure"), (2, "inject", "thermal")]},
    {"id": "bearing_c04", "title": "Predictive maintenance: C04 bearing wear", "summary": "C04's fan bearing starts wearing with no step change. The predictor must notice the trend from noisy vibration readings, the Reliability Agent plans pre-positioning and a maintenance window before the failure, and the result is scored against the hidden truth.",
     "participants": ["predictor", "reliability", "policy", "executor"], "stages": [(0, "inject", "crah_bearing_wear:C04")]},
    {"id": "vendor_fraud", "title": "Vendor fraud and over-billing", "summary": "Three invoices land: a bank-detail change from a lookalike domain, a usage invoice billing 900 GB against metered usage, and a price above contract. The matcher decides, the CREAO AP Analyst reviews independently, and the Explainer answers why the risky one was held.",
     "participants": ["matcher", "ap", "explainer"], "stages": [(0, "invoice", "bank_change"), (1, "invoice", "over_billed"), (2, "invoice", "price_creep"), (4, "ap_review", None), (60, "explain", "bank_change")]},
    {"id": "grid_spike", "title": "Grid price spike", "summary": "Wholesale price quadruples for three site hours. The energy agent discharges the battery inside its 40% reserve on its own authority and the savings are measured against running without the battery.",
     "participants": ["energy"], "stages": [(0, "inject", "grid_price_spike")]},
    {"id": "intrusion", "title": "After-hours intrusion and firmware attempt", "summary": "An unscheduled contractor badge opens cage 3 and an unregistered host tries to upload PDU firmware. Agents are forbidden from acting on access or firmware: the watcher escalates to a human with the evidence.",
     "participants": ["security"], "stages": [(0, "inject", "security_anomaly")]},
    {"id": "storm", "title": "Compound storm: every agent at once", "summary": "Within two minutes: an intrusion, a grid price spike, a fraudulent invoice batch, a wearing bearing on C04 and a cooling failure on C02 under peak load. Every loop runs concurrently on shared state; nothing is staged after the faults.",
     "participants": ["security", "energy", "matcher", "ap", "explainer", "predictor", "reliability", "detector", "remediation", "specialists", "policy", "voice", "executor", "v2"],
     "stages": [(0, "inject", "security_anomaly"), (6, "inject", "grid_price_spike"), (12, "invoice", "bank_change"), (14, "invoice", "over_billed"), (16, "ap_review", None),
                (24, "inject", "crah_bearing_wear:C04"), (40, "inject", "thermal"), (90, "explain", "bank_change")]},
]

class Runner:
    def __init__(self, ns):
        self.ns = ns; self.task = None; self.history = []
        try: self.cur = ns["ENG"].db.get("scenario_cur", None)
        except Exception: self.cur = None

    def defs(self):
        return [{k: v for k, v in s.items() if k != "stages"} | {"stages": [{"at_s": a, "kind": k, "arg": x} for a, k, x in s["stages"]], "participants": [{"id": p, "name": PARTICIPANTS[p][0], "kind": PARTICIPANTS[p][1]} for p in s["participants"]]} for s in SCENARIOS]

    async def start(self, sid, reset=True, actor="operator"):
        sc = next((s for s in SCENARIOS if s["id"] == sid), None)
        if not sc: return False, "unknown scenario"
        if self.cur and self.cur["status"] == "running": return False, "scenario %s is still running; stop it first" % self.cur["id"]
        ns = self.ns
        if reset: ns["do_reset"]()
        self.cur = {"id": sid, "title": sc["title"], "status": "running", "started": time.time(), "sim_t0": ns["ENG"].sim.t, "stages": [], "invoices": {}, "actor": actor}
        ns["ENG"].event(None, "scenario_started", actor, {"scenario": sid, "title": sc["title"], "reset": reset})
        self.task = asyncio.ensure_future(self._run(sc)); return True, "scenario started"

    def stop(self, actor):
        if self.task and not self.task.done(): self.task.cancel()
        if self.cur and self.cur["status"] == "running":
            self.cur["status"] = "stopped"; self.ns["ENG"].db.set("scenario_cur", self.cur); self.ns["ENG"].event(None, "scenario_stopped", actor, {"scenario": self.cur["id"]})

    async def _run(self, sc):
        ns = self.ns; t0 = time.time()
        try:
            for at, kind, arg in sc["stages"]:
                while time.time() - t0 < at: await asyncio.sleep(0.5)
                rec = {"at": round(time.time() - t0, 1), "kind": kind, "arg": arg}
                try:
                    if kind == "inject":
                        k, _, unit = arg.partition(":"); rec["result"] = ns["do_inject"](k, "scenario:" + sc["id"], unit or None)
                    elif kind == "invoice":
                        d = json.loads(json.dumps(INVOICES[arg])); d["number"] = d["number"] % (int(time.time()) % 100000)
                        iid = ns["DESK"].submit_invoice(d); res = dict(ns["DESK"].autonomous_pass("agent:ap")).get(iid); a = ns["DESK"].analyze(iid)
                        self.cur["invoices"][arg] = iid; rec["result"] = {"invoice": iid, "status": res, "decision": a["decision"], "amount": a["amount"]}
                    elif kind == "ap_review": rec["result"] = {k: v for k, v in (await ns["run_ap_review"]()).items() if k != "deterministic"}
                    elif kind == "explain":
                        iid = self.cur["invoices"].get(arg)
                        if iid: rec["result"] = await ns["ask_explainer"](iid, "Why was this invoice held instead of paid, and what exactly should the controller verify before releasing it?")
                except Exception as ex: rec["error"] = str(ex)[:300]
                self.cur["stages"].append(rec); ns["ENG"].event(None, "scenario_stage", "scenario:" + sc["id"], {"scenario": sc["id"], **{k: v for k, v in rec.items() if k != "result"}, "result": str(rec.get("result"))[:200]})
                ns["ENG"].db.set("scenario_cur", self.cur); ns["notify"]()
            self.cur["staged_done"] = True; ns["ENG"].db.set("scenario_cur", self.cur)
        except asyncio.CancelledError: pass

    def board(self):
        cur = self.cur
        if not cur: return None
        ns = self.ns; db = ns["ENG"].db; t0 = cur["started"]
        sc = next(s for s in SCENARIOS if s["id"] == cur["id"])
        runs = db.all("select id, task, status, created, completed, incident_id, data from agent_runs where created >= ? order by created", (t0 - 1,))
        evs = db.all("select type, actor, ts, incident_id, data from events where ts >= ? order by id", (t0 - 1,))
        def ev(*types): return [dict(e, data=json.loads(e["data"] or "{}")) for e in evs if e["type"] in types]
        thoughts = {}
        for t in db.all("select run_ref, tool, args, ts from tool_calls where ts >= ? order by id", (t0 - 1,)):
            try: a = json.loads(t["args"] or "{}")
            except Exception: a = {}
            if isinstance(a, dict) and a.get("thought"): thoughts[t["run_ref"]] = {"text": a["thought"], "tool": t["tool"], "ts": t["ts"]}
        def runs_of(pred):
            rs = [r for r in runs if pred(r["task"])]
            return rs
        def run_status(rs):
            if not rs: return "waiting"
            if any(r["status"] in ("starting", "pending", "running", "queued", "awaiting_agent", "in_progress") for r in rs): return "working"
            return "done" if any(r["status"] == "completed" for r in rs) else "failed"
        def last_thought(rs):
            for r in rs[::-1]:
                if r["id"] in thoughts: return thoughts[r["id"]]
            return None
        incs = db.all("select id, state, rack from incidents where created >= ? order by created", (t0 - 1,))
        inc = ns["incident_detail"](ns["ENG"].incident(incs[-1]["id"])) if incs else None
        plan = (inc.get("plans") or [None])[-1] if inc else None
        out = []
        for p in sc["participants"]:
            name, kind = PARTICIPANTS[p]; st = "waiting"; fix = None; th = None; link = None
            if p == "detector":
                if incs: st = "done"; fix = "opened %s on %s" % (incs[-1]["id"], incs[-1]["rack"]); link = {"kind": "incident", "id": incs[-1]["id"]}
            elif p == "remediation":
                rs = runs_of(lambda t: t in ("diagnose_and_plan", "evaluate_alternative", "replan_after_failure", "execute_approved_plan", "post_incident_summary")); st = run_status(rs); th = last_thought(rs)
                if plan: fix = "%s v%d: %s" % (plan["plan_id"], plan["version"], ns["STY"].acts_txt(plan["actions"]))
                if incs: link = {"kind": "incident", "id": incs[-1]["id"]}
            elif p == "specialists":
                rs = runs_of(lambda t: t.startswith("specialist:") and t.split(":", 1)[1] not in ("reliability", "ap_analyst", "finance_explainer") and not t.startswith("specialist:v2_")); st = run_status(rs); th = last_thought(rs)
                if rs: fix = ", ".join(sorted({r["task"].split(":", 1)[1].replace("_", " ") for r in rs}))
                elif inc and inc["state"] == "CLOSED": st = "not needed"
            elif p == "policy":
                e = [x for x in ev("state_transition") if x["data"].get("to") == "POLICY_CHECKED"]
                if e: st = "done"; fix = "decision: " + ((plan or {}).get("policy") or {}).get("decision", "?").replace("_", " ").lower()
                wos = [x for x in ev("wo_transition") if x["data"].get("to") == "AWAITING_APPROVAL"]
                if wos and not e: st = "done"; fix = "preventive plan needs approval"
            elif p == "voice":
                calls = db.all("select status, data from calls where created >= ? order by created", (t0 - 1,))
                if calls:
                    cs = calls[-1]["status"]; st = "failed" if cs == "failed" else "done" if cs in ("completed", "ended") else "working"
                    try: err = json.loads(calls[-1]["data"] or "{}").get("error")
                    except Exception: err = None
                    fix = "call " + cs + (" · " + str(err)[:80] if err else "")
                elif inc and inc["state"] in ("APPROVED", "EXECUTING", "VALIDATING", "MONITORING_RECOVERY", "RESOLVED", "FINANCIAL_RECONCILIATION", "CLOSED"): st = "not needed"; fix = "approved in the console"
            elif p == "executor":
                exs = db.all("select id, state, data from executions where created >= ? order by created", (t0 - 1,))
                sch = [x for x in ev("wo_transition") if x["data"].get("to") == "SCHEDULED"]
                if exs:
                    x = exs[-1]; d = json.loads(x["data"] or "{}")
                    st = "working" if x["state"] in ("running", "pending", "applying", "validating") else "done" if x["state"] in ("applied", "completed", "succeeded", "validated") else "done" if len(exs) > 1 else "failed" if "roll" in x["state"] else "done"
                    fix = "; ".join("%s %s%s" % (y["id"], y["state"], (" @" + json.loads(y["data"] or "{}").get("applied_sha", "")[:8]) if json.loads(y["data"] or "{}").get("applied_sha") else "") for y in exs)
                elif sch: st = "done"; fix = sch[-1]["data"].get("reason")
            elif p == "predictor":
                e = ev("failure_predicted")
                if e: st = "done"; fix = "%s: RUL %ss (P15 %d%%)" % (e[-1]["data"].get("asset"), e[-1]["data"].get("rul_s"), round((e[-1]["data"].get("p_fail_15m") or 0) * 100)); link = {"kind": "wo", "id": e[-1]["data"].get("work_order")}
                else: st = "working"; fix = "learning the trend (needs ~80 s of readings)"
            elif p == "reliability":
                rs = runs_of(lambda t: t == "specialist:reliability"); st = run_status(rs); th = last_thought(rs)
                wo = next((w for w in ns["REL"].list(5) if w["created"] >= t0 - 1), None)
                if wo:
                    link = {"kind": "wo", "id": wo["id"]}; pl = (wo.get("plans") or [None])[-1]
                    fix = (wo["state"].replace("_", " ").lower() + (": " + ns["STY"].acts_txt(pl["actions"]) + " + repair at t+%ss" % pl["maintenance_start_in_s"] if pl else ""))
                    if wo.get("outcome"): st = "done"; fix = "prevented: peak %s°C vs %s°C" % (wo["outcome"]["observed_peak_gpu_c"], wo["outcome"]["counterfactual_peak_gpu_c"])
            elif p == "ap":
                rs = runs_of(lambda t: t == "specialist:ap_analyst"); st = run_status(rs); th = last_thought(rs)
                if rs: link = {"kind": "finance", "id": rs[-1]["id"]}
                recs = db.all("select count(*) n from ap_decisions where kind='agent_recommendation' and ts >= ?", (t0 - 1,))[0]["n"]
                if recs: fix = "%d invoice recommendations" % recs
            elif p == "matcher":
                if cur["invoices"]:
                    dec = {(x.get("result") or {}).get("invoice"): (x.get("result") or {}).get("decision") for x in cur["stages"] if x.get("kind") == "invoice"}
                    st = "done"; fix = "; ".join("%s → %s" % (iid, dec.get(iid, "?")) for iid in cur["invoices"].values())
            elif p == "explainer":
                rs = runs_of(lambda t: t == "specialist:finance_explainer"); st = run_status(rs); th = last_thought(rs)
                if rs: link = {"kind": "finance", "id": rs[-1]["id"]}
                exs = db.all("select reason from ap_decisions where kind='explanation' and ts >= ? order by id desc limit 1", (t0 - 1,))
                if exs: fix = exs[0]["reason"][:220]
            elif p == "energy":
                e = ev("bess_dispatch")
                if e:
                    modes = []
                    for x in e:
                        m_ = x["data"].get("mode", "")
                        if not modes or modes[-1] != m_: modes.append(m_)
                    b_ = ns["TWIN"].bess; last = modes[-1]
                    st = "working" if last in ("price_spike_discharge", "peak_shave", "charge") else "done"
                    fix = " → ".join(m.replace("_", " ") for m in modes[-4:]) + (" (stopped at the 40% reserve)" if last == "idle" and b_["soc"] <= 0.41 else "") + " · SoC %d%% · net saving $%.2f" % (round(b_["soc"] * 100), b_["saved_usd"])
            elif p == "security":
                e = ev("security_alert")
                if e: st = "done"; fix = "escalated %d alert(s) to a human (agents may not act on access/firmware)" % len(e)
            elif p == "v2":
                e = ev("v2_detector_fired", "v2_agent_run", "v2_finding")
                if e: st = "done" if any(x["type"] == "v2_finding" for x in e) else "working"; fix = "; ".join(sorted({x["type"].replace("v2_", "").replace("_", " ") for x in e}))
                elif not any(o["status"] == "active" for o in ns["GHOST"].active()): st = "not needed"; fix = "no v2 orchestration dispatched"
            out.append({"id": p, "name": name, "kind": kind, "status": st, "fix": fix, "thought": th, "link": link})
        active = sum(1 for x in out if x["status"] == "working"); done = sum(1 for x in out if x["status"] in ("done", "not needed"))
        waiting = sum(1 for x in out if x["status"] == "waiting")
        if cur["status"] == "running" and cur.get("staged_done") and active == 0 and (waiting == 0 or time.time() - t0 > 1200) and (not inc or inc["state"] == "CLOSED"):
            cur["status"] = "complete"; cur["completed"] = time.time(); ns["ENG"].db.set("scenario_cur", cur); ns["ENG"].event(None, "scenario_completed", "scenario:" + cur["id"], {"scenario": cur["id"], "done": done, "total": len(out)})
        return {**{k: v for k, v in cur.items() if k != "invoices"}, "invoices": cur["invoices"], "elapsed_s": round(time.time() - t0), "participants": out, "active": active, "done": done, "total": len(out),
                "creao_runs": len(runs), "creao_writes_hint": "CREAO allows about 30 runs/agent creations per hour"}
