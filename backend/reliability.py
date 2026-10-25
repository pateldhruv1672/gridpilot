"""Reliability loop: predict -> plan -> approve -> pre-position -> maintain -> verify.

The predictor (predict.py) raises a risk from sensors. GRIDPILOT opens a work order and hands it to the CREAO Reliability Agent,
which may only read and evaluate. Every candidate is forecast deterministically under the predicted failure (or the
maintenance outage, whichever comes first) against the do-nothing counterfactual. A human approves; the backend applies the
mitigations through the git config store, schedules the maintenance window in the twin and verifies the outcome.
"""
import json, time, secrets, traceback
import numpy as np
import sim as S, finance as FIN, policy as POL, configstore as CS

OFF_EFF, OFF_FLOW = 0.30, 0.25          # CRAH out of service (failed or in maintenance)
REL_TOOLS = ["rel_get_context", "rel_capacity", "rel_evaluate", "rel_propose", "rel_report"]
REL_TEXT = ("Create a reusable personal agent named \"GRIDPILOT Reliability Agent\" with exactly ONE input field named `request` (long text).\n\n"
    "Role: predictive-maintenance planner for Atlas Compute's GPU pod SFO-01. A deterministic predictor has forecast that an asset (for example a CRAH fan "
    "bearing) will fail. Your job is to turn that prediction into a preventive work order the operator can approve: when to take the asset out of service, "
    "and what to change BEFORE it goes out so no rack overheats and no protected customer breaches its SLA. You never approve or execute anything.\n\n"
    "On every run: parse `request` as JSON (gridpilot_api, tool_token, work_order, asset). Tools are HTTPS endpoints: POST JSON to {gridpilot_api}/{tool} with "
    "headers `X-GridPilot-Tool-Token: {tool_token}` and `Content-Type: application/json`. Use Python requests (json=...), not curl. Tools: rel_get_context {} "
    "(work order, asset sensors, prediction with RUL and 90% band, served racks, peer units, workloads, policy, maintenance rules), rel_capacity {} (every rack: "
    "free GPUs, temperature, zone, reserve flag, cooling unit), rel_evaluate {actions, maintenance_start_in_s} (deterministic 10-minute forecast with the unit "
    "out of service from the earlier of the predicted failure or maintenance start, compared with doing nothing: pod peak GPU temperature, hottest rack, SLA "
    "breaches, cost, policy), rel_propose {actions, maintenance_start_in_s, maintenance_duration_s, reasoning_summary, confidence, evidence: [..]} (creates the "
    "plan version for human approval), rel_report {text}. Never invent a tool result.\n\n"
    "Playbook: call rel_get_context and rel_capacity. Evaluate the do-nothing case (actions []) first, then 2 to 4 preventive options: raising fans on the "
    "neighbouring CRAH units (60 to 130%), GPU power caps on the racks the unit serves, migrating protected or heavy workloads to cooler racks with free GPUs "
    "(prefer the same zone), and combinations. Choose a maintenance start inside the predicted RUL band with margin (start before rul_lo_s if possible). Pick "
    "the cheapest option whose forecast keeps every rack below 85 C with no SLA breach; say why in reasoning_summary, citing the RUL, the counterfactual peak and "
    "the chosen option's peak. Then call rel_propose once. Actions: migrate_workload {workload, to_rack}, set_gpu_power_limit {rack, watts 300 to 450}, "
    "set_cooling_fan {unit, fan_pct 60 to 130}.\n\n"
    "Final answer: one JSON object {work_order, asset, rul_s, counterfactual_peak_c, chosen_actions, plan_peak_c, maintenance_start_in_s, confidence, reasoning_summary}.")

class Reliability:
    def __init__(self, eng, twin, pred, start_agent, notify):
        self.eng, self.twin, self.pred, self.start_agent, self.notify = eng, twin, pred, start_agent, notify
        self.db = eng.db
        self.db.c.execute("create table if not exists work_orders(id text primary key, asset text, created real, state text, data text)"); self.db.c.commit()
        self.watch = {}       # wo -> peak tracking during the outage

    # ------------------------------------------------------------ records
    def get(self, wid):
        r = self.db.one("select * from work_orders where id=?", (wid,))
        if not r: return None
        d = json.loads(r["data"]); d.update({k: r[k] for k in ("id", "asset", "created", "state")}); return d
    def save(self, wo):
        data = {k: v for k, v in wo.items() if k not in ("id", "asset", "created", "state")}
        self.db.ex("update work_orders set state=?, data=? where id=?", (wo["state"], json.dumps(data, default=str), wo["id"]))
    def transition(self, wo, state, actor, reason, data=None):
        old = wo["state"]; wo["state"] = state
        wo.setdefault("timeline", []).append({"state": state, "ts": time.time(), "sim_t": round(self.twin.t, 1), "actor": actor, "reason": reason})
        self.save(wo); self.eng.event(None, "wo_transition", actor, {"work_order": wo["id"], "asset": wo["asset"], "from": old, "to": state, "reason": reason, **(data or {})})
        self.notify()
    def open_for(self, asset):
        return self.db.one("select id from work_orders where asset=? and state not in ('CLOSED','REJECTED','FAILED')", (asset,))
    def list(self, n=12):
        return [self.get(r["id"]) for r in self.db.all("select id from work_orders order by created desc limit ?", (n,))]

    # ------------------------------------------------------------ predictor alerts
    async def on_alert(self, aid, p):
        if self.open_for(aid): return
        n = (self.db.get("wo_counter", 7100) or 7100) + 1; self.db.set("wo_counter", n); wid = "WO-%d" % n
        a = self.twin.assets[aid]
        wo = {"id": wid, "asset": aid, "created": time.time(), "state": "PREDICTED",
              "prediction": {k: p.get(k) for k in ("risk", "rul_s", "rul_lo_s", "rul_hi_s", "p_fail_5m", "p_fail_15m", "signal", "value", "unit", "alarm", "model")},
              "asset_name": a["name"], "failure_mode": {"crah": "fan bearing wear", "ups": "battery string aging"}.get(a["kind"], a["kind"]), "plans": [], "reports": [],
              "predicted_at_sim_t": round(self.twin.t, 1), "predicted_fail_sim_t": round(self.twin.t + (p.get("rul_s") or 0), 1)}
        self.db.ex("insert into work_orders values(?,?,?,?,?)", (wid, aid, wo["created"], "PREDICTED", "{}")); self.save(wo)
        self.eng.event(None, "failure_predicted", "predictor", {"work_order": wid, "asset": aid, "signal": p.get("signal"), "value": p.get("value"),
                       "rul_s": p.get("rul_s"), "rul_lo_s": p.get("rul_lo_s"), "rul_hi_s": p.get("rul_hi_s"), "p_fail_15m": p.get("p_fail_15m")})
        self.notify()
        if a["kind"] == "crah": await self.start_planning(wid, "predictor")
        else: self.transition(wo, "AWAITING_PLAN", "backend", "no automated playbook for %s yet; operator reviews" % a["kind"])

    async def start_planning(self, wid, actor, note=None):
        wo = self.get(wid)
        req = {"task": "preventive_plan", "work_order": wid, "asset": wo["asset"], "note": note or "Plan preventive action before the predicted failure."}
        ref, err = await self.start_agent("reliability", "GRIDPILOT Reliability Agent", REL_TEXT, REL_TOOLS, req, {"work_order": wid, "mission": "preventive maintenance plan", "trigger": "failure predicted on " + wo["asset"]})
        wo = self.get(wid)
        if not ref:
            wo["agent_error"] = err; self.transition(wo, "AGENT_UNAVAILABLE", "backend", "CREAO not available: %s" % err); return None
        wo.setdefault("runs", []).append(ref); self.transition(wo, "PLANNING", actor, "CREAO Reliability Agent run %s started" % ref, {"run_ref": ref})
        return ref

    # ------------------------------------------------------------ scenario forecast
    def unit_k(self, aid): return int(aid[1:]) - 1
    def forecast(self, base, actions, k, off_at, horizon=600.0):
        c = base.clone()
        if actions: c.load_config(self.eng.apply_actions(c.config, actions))
        e0 = c.energy_kwh; series = []; peak = (0, None); breach = {}; off_done = False
        for step in range(int(horizon / 5)):
            if not off_done and (c.t - base.t) >= off_at: c.degrade_unit(k, OFF_EFF, OFF_FLOW); off_done = True
            c.step(5.0, speed=1, sub=1.0)
            i = int(np.argmax(c.gpu_t)); g = float(c.gpu_t[i]); series.append([round(c.t - base.t), round(g, 1)])
            if g > peak[0]: peak = (g, S.rack_id(i // 8, i % 8))
            for wid, v in c.workload_latency().items():
                if v["breach"]: breach[wid] = breach.get(wid, 0) + 5
        lat = c.workload_latency()
        return {"series": series, "peak_gpu_c": round(peak[0], 1), "hottest_rack": peak[1], "energy_kwh": c.energy_kwh - e0,
                "breach_minutes": {w: s / 60 for w, s in breach.items()}, "breach_at_end": {w: v["breach"] for w, v in lat.items()}, "horizon_min": horizon / 60,
                "final_max_gpu_c": round(float(c.gpu_t.max()), 1)}

    async def evaluate(self, wid, actions, start_in):
        import asyncio
        wo = self.get(wid); aid = wo["asset"]; k = self.unit_k(aid); p = self.pred.preds.get(aid, {})
        acts = self.eng.normalize_actions(actions or [])
        rul = p.get("rul_lo_s") or p.get("rul_s") or 0
        if p.get("risk") == "failed": rul = 0
        start_in = float(start_in if start_in is not None else max(30, rul * 0.6))
        off_at = max(0.0, min(start_in, rul if rul else start_in))
        base = self.eng.sim.clone()
        cf, fc = await asyncio.gather(asyncio.to_thread(self.forecast, base, [], k, off_at), asyncio.to_thread(self.forecast, base, acts, k, off_at))
        fc["baseline_energy_kwh"] = cf["energy_kwh"]; cfx = dict(cf); cfx["baseline_energy_kwh"] = cf["energy_kwh"]
        fin = FIN.evaluate(acts, self.eng.sim.config, fc, S.CUSTOMERS); fin0 = FIN.evaluate([], self.eng.sim.config, cfx, S.CUSTOMERS)
        pol = POL.evaluate_plan(acts, self.eng.sim.config, fin["incremental_cost_usd"], {c for c, v in S.CUSTOMERS.items() if v["protected"]})
        ok = fc["peak_gpu_c"] < 85 and not any(m > 0 for m in fc["breach_minutes"].values())
        return {"actions": acts, "maintenance_start_in_s": round(start_in), "outage_from_s": round(off_at), "rul_used_s": rul,
                "counterfactual": {"peak_gpu_c": cf["peak_gpu_c"], "hottest_rack": cf["hottest_rack"], "breach_minutes": cf["breach_minutes"], "sla_exposure_usd": fin0["sla_exposure_usd"], "series": cf["series"]},
                "with_plan": {"peak_gpu_c": fc["peak_gpu_c"], "hottest_rack": fc["hottest_rack"], "breach_minutes": fc["breach_minutes"], "sla_exposure_usd": fin["sla_exposure_usd"], "series": fc["series"]},
                "incremental_cost_usd": fin["incremental_cost_usd"], "energy_delta_usd": fin["energy_delta_usd"], "cost_lines": fin["cost_lines"],
                "policy": pol, "meets_goal": ok, "goal": "every rack below 85 C and no SLA breach during the outage",
                "avoided_exposure_usd": round(fin0["sla_exposure_usd"] - fin["sla_exposure_usd"], 2),
                "model_note": "Approximate thermal/airflow simulation (10-minute horizon), unit out of service from outage_from_s."}

    # ------------------------------------------------------------ agent tools
    async def tool(self, tool, args, wid, actor):
        wo = self.get(wid)
        if not wo: return {"error": "unknown work order"}
        aid = wo["asset"]; snap = self.eng.sim.snapshot(field=False); a = self.twin.assets[aid]
        if tool == "rel_get_context":
            k = self.unit_k(aid) if a["kind"] == "crah" else None
            served = [r for r in snap["racks"] if k is not None and S.cold_aisle_of_row(r["row"]) == k]
            return {"work_order": {x: wo.get(x) for x in ("id", "state", "asset", "asset_name", "failure_mode", "prediction", "predicted_fail_sim_t")}, "now_sim_t": round(self.twin.t, 1),
                    "asset": {"id": aid, "name": a["name"], "serves": a["serves"], "sensors": a["sensors"], "state": a["state"]},
                    "prediction_now": {x: self.pred.preds.get(aid, {}).get(x) for x in ("risk", "rul_s", "rul_lo_s", "rul_hi_s", "p_fail_5m", "p_fail_15m", "signal", "value", "unit", "model")},
                    "sensor_history_tail": self.pred.history(aid, 30)[::3],
                    "peer_units": [{"id": "C%02d" % (j + 1), **self.twin.assets["C%02d" % (j + 1)]["sensors"], "state": self.twin.assets["C%02d" % (j + 1)]["state"]} for j in range(4)],
                    "served_racks": [{x: r[x] for x in ("rack_id", "gpu_temp_c", "inlet_temp_c", "power_kw", "power_limit_w", "workloads", "zone")} for r in served],
                    "workloads_on_served_racks": [{"id": w, **v, "protected_sla": S.CUSTOMERS[v["customer"]]["protected"]} for w, v in self.eng.sim.config["workloads"].items() if any(v["rack"] == r["rack_id"] for r in served)],
                    "policy": {"auto": "fan 60-130%, power cut <=10%", "approval": "migration, >10% power cut, cost >$100, protected SLA; every maintenance work order needs operator sign-off", "forbidden": "delete, shutdown, firmware, billing"},
                    "maintenance_rules": {"duration_s_default": 150, "earliest_start_in_s": 45, "unit_state_during_maintenance": "out of service (same airflow loss as failure)"}}
        if tool == "rel_capacity":
            cfg = self.eng.sim.config; used = {r: 0 for r in cfg["racks"]}
            for w in cfg["workloads"].values(): used[w["rack"]] += w["gpus"]
            return {"racks": [{"rack_id": r["rack_id"], "free_gpus": 8 - used[r["rack_id"]], "gpu_temp_c": r["gpu_temp_c"], "zone": r["zone"], "reserve_pool": r["reserve_pool"],
                               "cooling_unit": "C%02d" % (S.cold_aisle_of_row(r["row"]) + 1)} for r in snap["racks"]], "cooling": snap["cooling"],
                    "notes": "reserve_pool racks are leased burst capacity (rate card); cross-zone moves incur transfer fees"}
        if tool == "rel_evaluate":
            ev = await self.evaluate(wid, args.get("actions", []), args.get("maintenance_start_in_s"))
            wo = self.get(wid); wo.setdefault("options", []).append({k: ev[k] for k in ("actions", "maintenance_start_in_s", "incremental_cost_usd", "meets_goal", "avoided_exposure_usd")} | {"peak": ev["with_plan"]["peak_gpu_c"], "cf_peak": ev["counterfactual"]["peak_gpu_c"], "policy": ev["policy"]["decision"], "by": actor, "ts": time.time()})
            wo["options"] = wo["options"][-12:]; self.save(wo); self.notify()
            return {k: v for k, v in ev.items() if k not in ()} | {"counterfactual": {k: v for k, v in ev["counterfactual"].items() if k != "series"}, "with_plan": {k: v for k, v in ev["with_plan"].items() if k != "series"}}
        if tool == "rel_propose":
            return await self.propose(wid, args, actor)
        if tool == "rel_report":
            wo.setdefault("reports", []).append({"by": actor, "text": str(args.get("text", ""))[:4000], "ts": time.time()}); self.save(wo); self.notify(); return {"ok": True}
        return {"error": "unknown tool"}

    async def propose(self, wid, args, actor):
        wo = self.get(wid)
        if wo["state"] not in ("PLANNING", "PREDICTED", "AWAITING_APPROVAL", "AWAITING_PLAN", "AGENT_UNAVAILABLE", "REPLAN"):
            return {"error": "work order %s is %s; plans cannot be proposed now" % (wid, wo["state"])}
        ev = await self.evaluate(wid, args.get("actions", []), args.get("maintenance_start_in_s"))
        wo = self.get(wid); ver = len(wo["plans"]) + 1
        plan = {"version": ver, "author": actor, "created": time.time(), "actions": ev["actions"], "maintenance_start_in_s": ev["maintenance_start_in_s"],
                "maintenance_duration_s": int(min(400, max(60, float(args.get("maintenance_duration_s") or 150)))), "evaluation": ev,
                "reasoning_summary": str(args.get("reasoning_summary", ""))[:3000], "confidence": args.get("confidence"), "evidence": args.get("evidence") or [], "status": "awaiting_approval"}
        for p in wo["plans"]:
            if p["status"] == "awaiting_approval": p["status"] = "superseded"
        wo["plans"].append(plan); self.save(wo)
        if ev["policy"]["decision"] == "FORBIDDEN":
            plan["status"] = "denied"; self.save(wo); self.transition(wo, "REPLAN", "policy-engine", "plan v%d contains a forbidden action" % ver); return {"plan_version": ver, "policy": "FORBIDDEN"}
        self.transition(wo, "AWAITING_APPROVAL", "policy-engine", "preventive plan v%d needs operator sign-off (maintenance work order, policy %s)" % (ver, ev["policy"]["decision"]),
                        {"plan_version": ver, "meets_goal": ev["meets_goal"], "cf_peak": ev["counterfactual"]["peak_gpu_c"], "plan_peak": ev["with_plan"]["peak_gpu_c"]})
        return {"plan_version": ver, "policy": ev["policy"]["decision"], "meets_goal": ev["meets_goal"], "state": "AWAITING_APPROVAL", "note": "the operator approves; you cannot"}

    # ------------------------------------------------------------ human decisions + execution
    async def approve(self, wid, ver, actor):
        wo = self.get(wid)
        if not wo or wo["state"] != "AWAITING_APPROVAL": return False, "work order is not awaiting approval"
        plan = wo["plans"][-1]
        if plan["version"] != int(ver) or plan["status"] != "awaiting_approval": return False, "stale plan: current is v%d" % plan["version"]
        # re-check on live state before acting (the prediction may have moved)
        ev = await self.evaluate(wid, plan["actions"], plan["maintenance_start_in_s"])
        wo = self.get(wid); plan = wo["plans"][-1]; plan["approval_recheck"] = {"peak": ev["with_plan"]["peak_gpu_c"], "meets_goal": ev["meets_goal"], "cf_peak": ev["counterfactual"]["peak_gpu_c"]}
        plan["status"] = "approved"; plan["approved_by"] = actor; plan["approved_at"] = time.time(); self.save(wo)
        self.transition(wo, "APPROVED", actor, "operator approved preventive plan v%d" % plan["version"])
        wo = self.get(wid); plan = wo["plans"][-1]
        try:
            sha = None
            if plan["actions"]:
                snap = CS.snapshot("pre-" + wid.lower()); cfg_now = CS.read_tree()
                cand = self.eng.apply_actions(cfg_now, plan["actions"])
                wt, branch, diff, changed = CS.build_candidate(cand, wid.lower())
                sha = CS.commit_candidate(wt, branch, "%s: preventive mitigations before %s maintenance (plan v%d, approved by %s)" % (wid, wo["asset"], plan["version"], actor))
                self.eng.sim.load_config(CS.read_tree())
                plan["applied_sha"] = sha; plan["snapshot_sha"] = snap; plan["diff"] = diff[:6000]
            start = self.twin.t + plan["maintenance_start_in_s"]
            self.twin.assets[wo["asset"]]["maint"] = {"wo": wid, "start": round(start, 1), "duration": plan["maintenance_duration_s"], "status": "scheduled"}
            wo["maintenance"] = {"start_sim_t": round(start, 1), "duration_s": plan["maintenance_duration_s"]}; self.save(wo)
            self.transition(wo, "SCHEDULED", "backend", "mitigations committed%s; maintenance window at sim t+%ds for %ds" % ((" (" + sha[:10] + ")") if sha else "", round(start), plan["maintenance_duration_s"]),
                            {"applied_sha": sha})
            return True, "approved: mitigations applied, maintenance scheduled"
        except Exception as ex:
            traceback.print_exc(); wo = self.get(wid); self.transition(wo, "REPLAN", "backend", "execution failed: %s" % str(ex)[:200]); return False, str(ex)

    def reject(self, wid, actor, reason):
        wo = self.get(wid)
        if not wo or wo["state"] != "AWAITING_APPROVAL": raise ValueError("not awaiting approval")
        wo["plans"][-1]["status"] = "rejected"; self.save(wo); self.transition(wo, "REJECTED", actor, reason)

    # ------------------------------------------------------------ twin events + verification
    def on_twin_event(self, kind, aid):
        r = self.db.one("select id from work_orders where asset=? and state not in ('CLOSED','REJECTED','FAILED') order by created desc", (aid,))
        wo = self.get(r["id"]) if r else None
        if kind == "asset_failed":
            self.eng.event(None, "asset_failed", "twin", {"asset": aid, "work_order": wo and wo["id"]})
            if wo and wo["state"] in ("SCHEDULED",):
                self.watch[wo["id"]] = {"peak": 0, "rack": None, "breach": set(), "from": self.twin.t}
                self.transition(wo, "IN_MAINTENANCE", "twin", "asset failed before the window; mitigations already in place, crew dispatched now")
                self.twin.assets[aid]["maint"] = {"wo": wo["id"], "start": round(self.twin.t, 1), "duration": wo["plans"][-1]["maintenance_duration_s"], "status": "in_progress"}
                self.twin.assets[aid]["state"] = "maintenance"
            elif wo:
                self.transition(wo, "FAILED", "twin", "asset failed before a preventive plan was executed")
                self.pred.close_episode(aid, "failed", self.twin.t, {"failure_t": self.twin.t, "work_order": wo["id"]})
            else:
                self.pred.close_episode(aid, "failed", self.twin.t, {"failure_t": self.twin.t})
        if kind == "maintenance_started" and wo:
            self.watch[wo["id"]] = {"peak": 0, "rack": None, "breach": set(), "from": self.twin.t}
            self.transition(wo, "IN_MAINTENANCE", "twin", "%s out of service for maintenance" % aid)
        if kind == "maintenance_done" and wo:
            w = self.watch.pop(wo["id"], {"peak": 0, "rack": None, "breach": set()})
            plan = wo["plans"][-1] if wo["plans"] else None
            cf = plan["evaluation"]["counterfactual"]["peak_gpu_c"] if plan else None
            prevented = w["peak"] < S.THRESH["gpu_critical_c"] and not w["breach"]
            wo["outcome"] = {"observed_peak_gpu_c": round(w["peak"], 1), "hottest_rack": w["rack"], "breaches": sorted(w["breach"]), "counterfactual_peak_gpu_c": cf,
                             "prevented": bool(prevented and cf is not None and cf >= S.THRESH["gpu_critical_c"]), "safe": prevented,
                             "avoided_exposure_usd": plan["evaluation"]["avoided_exposure_usd"] if plan and prevented else 0.0}
            self.save(wo)
            self.transition(wo, "VERIFIED", "backend", "maintenance complete; observed peak %.1f C vs counterfactual %s C; %s" % (w["peak"], cf, "incident prevented" if wo["outcome"]["prevented"] else ("no breach" if prevented else "breach occurred")))
            self.pred.close_episode(aid, "prevented" if prevented else "failed", self.twin.t, {"work_order": wo["id"], "observed_peak_gpu_c": round(w["peak"], 1)})
            wo = self.get(wo["id"]); self.transition(wo, "CLOSED", "backend", "work order closed; %s back in service, wear reset" % aid)

    def tick(self):
        for wid, w in self.watch.items():
            g = self.eng.sim.gpu_t; i = int(np.argmax(g))
            if float(g[i]) > w["peak"]: w["peak"] = float(g[i]); w["rack"] = S.rack_id(i // 8, i % 8)
            for wl, v in self.eng.sim.workload_latency().items():
                if v["breach"]: w["breach"].add(wl)
