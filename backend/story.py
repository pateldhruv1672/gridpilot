"""Plain-English interpretation of a GRIDPILOT decision loop, built only from recorded data.

Every sentence is derived from stored events, tool calls, plans and outcomes (nothing is invented). The agent's own words
(reasoning_summary / final answer) are quoted separately and labelled as such.
"""
import json, time

def _j(x):
    if isinstance(x, (dict, list)): return x
    try: return json.loads(x or "null")
    except Exception: return None

def act_txt(a):
    t = a.get("type") or a.get("action") or a.get("kind")
    if t == "migrate_workload": return "move %s %s→%s" % (a.get("workload"), a.get("from_rack", "?"), a.get("to_rack"))
    if t == "set_gpu_power_limit": return "cap %s GPUs at %sW" % (a.get("rack"), a.get("watts"))
    if t == "set_cooling_fan": return "%s fan %s%%%s" % (a.get("unit"), a.get("fan_pct"), " (already)" if a.get("from_pct") == a.get("fan_pct") else "")
    return str(t)

def acts_txt(acts):
    acts = acts or []
    if not acts: return "do nothing"
    caps = [a for a in acts if (a.get("type") == "set_gpu_power_limit")]
    rest = [a for a in acts if a not in caps]
    parts = [act_txt(a) for a in rest]
    if len(caps) > 3: parts.append("cap %d racks (%s…%s) at %sW" % (len(caps), caps[0].get("rack"), caps[-1].get("rack"), caps[0].get("watts")))
    else: parts += [act_txt(a) for a in caps]
    return " + ".join(parts)

def fmt_s(s):
    if s is None: return "—"
    s = int(round(s)); return ("%dm %02ds" % (s // 60, s % 60)) if s >= 60 else "%ds" % s

def _norm(acts): return sorted(json.dumps({k: v for k, v in a.items() if k in ("type", "workload", "to_rack", "rack", "watts", "unit", "fan_pct")}, sort_keys=True) for a in (acts or []))

def interpret_tool(tool, args, res):
    args = _j(args) or {}; res = _j(res) or {}
    if isinstance(res, dict) and res.get("_truncated"):
        try: res = json.loads(res.get("preview", "") + "")
        except Exception: res = {"_preview": True}
    err = isinstance(res, dict) and res.get("error")
    if err: return "Tool error: %s" % str(err)[:140]
    if tool in ("rel_get_context", "get_incident_context"): return "Read the case: prediction / incident state, pod telemetry and policy limits"
    if tool in ("rel_capacity", "get_capacity", "ops_capacity"): return "Checked capacity: which racks have free GPUs and cooling headroom"
    if tool == "get_telemetry": return "Read live telemetry for %s" % (", ".join(args.get("racks") or []) or args.get("rack") or "racks")
    if tool == "list_workloads": return "Listed workloads on %s" % (args.get("rack") or "the rack")
    if tool == "rel_evaluate":
        acts = (res.get("actions") if isinstance(res, dict) else None) or args.get("actions")
        wp = (res or {}).get("with_plan") or {}; cf = (res or {}).get("counterfactual") or {}
        if wp: return "Simulated '%s' with C02 out from t+%ss: peak %s°C vs %s°C doing nothing%s" % (acts_txt(acts), res.get("outage_from_s"), wp.get("peak_gpu_c"), cf.get("peak_gpu_c"), ", meets goal" if res.get("meets_goal") else ", misses goal")
        return "Simulated '%s' (maintenance at t+%ss)" % (acts_txt(acts), args.get("maintenance_start_in_s"))
    if tool == "evaluate_plan":
        f = (res or {}).get("forecast") or {}; fin = (res or {}).get("finance") or {}
        return "Simulated '%s': %s, end GPU %s°C, cost $%.2f, policy %s" % (acts_txt(args.get("actions")), ("recovers in %ss" % f["recover_s"]) if f.get("recover_s") is not None else "no recovery in 10 min", f.get("final_gpu_c"), fin.get("incremental_cost_usd") or 0, ((res or {}).get("policy") or {}).get("decision"))
    if tool in ("rel_propose", "propose_plan"): return "Proposed the plan: %s" % acts_txt(args.get("actions"))
    if tool == "request_execution": return "Asked GRIDPILOT to execute plan v%s (backend re-checks policy and approval)" % args.get("plan_version")
    if tool in ("rel_report", "submit_report"): return "Filed a report (%s)" % (args.get("kind") or "summary")
    if tool == "request_specialist": return "Called in a specialist: %s" % args.get("kind")
    if tool.startswith("fin_"): return {"fin_list_invoices": "Listed pending invoices", "fin_get_invoice": "Read invoice %s with contract, meter and vendor master" % args.get("invoice_id"),
                                        "fin_recommend": "Recommended %s on %s" % (args.get("decision"), args.get("invoice_id")), "fin_decision_log": "Read the decision log"}.get(tool, "Called %s" % tool)
    if tool == "ops_submit_finding": return "Filed finding: %s" % args.get("title")
    if tool.startswith("ops_"): return {"ops_digest": "Read the whole-system digest", "ops_gpu_fleet": "Read per-GPU telemetry" + (" for " + str(args["rack"]) if args.get("rack") else ""), "ops_events": "Read recent events", "ops_trace": "Read a decision trace", "ops_findings": "Read earlier findings"}.get(tool, tool)
    return "Called %s" % tool

def agent_steps(db, run_ids):
    out = []
    for rid in run_ids:
        for t in db.all("select tool, args, result, ok, ts from tool_calls where run_ref=? order by id", (rid,)):
            out.append({"tool": t["tool"], "text": interpret_tool(t["tool"], t["args"], t["result"]), "ok": bool(t["ok"]), "ts": t["ts"]})
    return out

def compare_options(opts, chosen_acts, peak_key, cf_key, goal_c=85.0, cost_key="incremental_cost_usd"):
    rows = []; ch_n = _norm(chosen_acts) if chosen_acts is not None else None
    for k, o in enumerate(opts):
        ms = o.get("maintenance_start_in_s")
        rows.append({"n": k + 1, "label": acts_txt(o.get("actions")) + (" · repair at t+%ss" % ms if ms is not None else ""), "nacts": len(o.get("actions") or []), "peak": o.get(peak_key), "cf": o.get(cf_key), "cost": o.get(cost_key) or 0, "meets": bool(o.get("meets_goal")), "policy": o.get("policy"),
                     "chosen": ch_n is not None and _norm(o.get("actions")) == ch_n})
    ch = next((r for r in rows if r["chosen"]), None)
    seen = {}
    for r in rows:
        key = r["label"] + str(r["peak"])
        if key in seen: r["verdict"] = "duplicate of option %d" % seen[key]; continue
        seen[key] = r["n"]
        if r["chosen"]: r["verdict"] = "chosen"; continue
        why = []
        if r.get("policy") == "FORBIDDEN": why.append("forbidden by policy")
        if r["peak"] is not None and r["cf"] is not None and abs(r["peak"] - r["cf"]) < 0.05: why.append("no improvement over doing nothing")
        if not r["meets"]: why.append(("misses the goal: peak %.1f°C is above %.0f°C" % (r["peak"], goal_c)) if (r["peak"] or 0) >= goal_c else "misses the goal: the repair would start after the predicted failure")
        if ch and r["peak"] is not None and ch["peak"] is not None and r["peak"] > ch["peak"] + 0.05: why.append("%.1f°C hotter than the chosen plan" % (r["peak"] - ch["peak"]))
        if ch and r["peak"] is not None and ch["peak"] is not None and r["peak"] < ch["peak"] - 0.05:
            why.append("%.1f°C cooler, but %s; the agent preferred the simpler change (see its reasoning)" % (ch["peak"] - r["peak"], ("needs %d changes instead of %d" % (r["nacts"], ch["nacts"])) if r["nacts"] > ch["nacts"] else "not selected"))
        if ch and r["cost"] > ch["cost"] + 0.005: why.append("costs $%.2f more" % (r["cost"] - ch["cost"]))
        if ch and r["peak"] is not None and ch["peak"] is not None and abs(r["peak"] - ch["peak"]) < 0.05 and r["nacts"] > ch["nacts"]: why.append("same result as the chosen plan, with extra changes")
        if "fan" in r["label"] and "(already)" in r["label"]: why.append("fans were already at that speed, so they add nothing")
        if not why and ch: why.append("no better than the chosen plan")
        r["verdict"] = "; ".join(why) or "not selected"
    return rows

def story_wo(wo, db, events):
    p = wo.get("prediction") or {}; tl = {t["state"]: t for t in wo.get("timeline") or []}
    pred_t = wo.get("predicted_at_sim_t"); fail_t = wo.get("predicted_fail_sim_t")
    plans = wo.get("plans") or []; plan = plans[-1] if plans else None; opts = wo.get("options") or []
    runs = wo.get("runs") or []
    st = wo["state"]; steps = []; nxt = []; caveats = []
    steps.append({"title": "Sensors", "text": "%s %s read %s %s (alarm level %s). The predictor only sees these noisy readings, never the hidden wear." % (wo["asset"], p.get("signal"), p.get("value"), p.get("unit"), p.get("alarm"))})
    steps.append({"title": "Prediction", "text": "At t+%ss the trend gave %s of remaining life (90%% range %s to %s) and a %d%% chance of failure within 15 minutes, so GRIDPILOT opened %s." % (int(pred_t or 0), fmt_s(p.get("rul_s")), fmt_s(p.get("rul_lo_s")), fmt_s(p.get("rul_hi_s")), round((p.get("p_fail_15m") or 0) * 100), wo["id"])})
    if "PLANNING" in tl:
        gap = tl["PLANNING"]["sim_t"] - (pred_t or tl["PLANNING"]["sim_t"])
        steps.append({"title": "Agent started", "text": "The CREAO Reliability Agent started %s after the prediction%s." % (fmt_s(gap), " (the first launch failed and was retried)" if gap > 30 else "")})
    if plan:
        took = (tl.get("AWAITING_APPROVAL", {}).get("sim_t") or 0) - (tl.get("PLANNING", {}).get("sim_t") or 0)
        n_ok = sum(1 for o in opts if o.get("meets_goal"))
        steps.append({"title": "Options explored", "text": "In %s the agent simulated %d options with %s out of service; %d kept the pod below the 85°C goal." % (fmt_s(took), len(opts), wo["asset"], n_ok)})
        ev = plan["evaluation"]
        steps.append({"title": "Decision", "text": "It proposed: %s, then take %s out for repair at t+%ss for %ss. Forecast pod peak %s°C with the plan vs %s°C doing nothing, cost $%.2f. Policy: %s." % (acts_txt(plan["actions"]), wo["asset"], plan["maintenance_start_in_s"], plan["maintenance_duration_s"], ev["with_plan"]["peak_gpu_c"], ev["counterfactual"]["peak_gpu_c"], ev.get("incremental_cost_usd") or 0, ev["policy"]["decision"].replace("_", " ").lower())})
        if ev["counterfactual"]["peak_gpu_c"] < 85: caveats.append("Doing nothing already stays under the 85°C goal (%s°C), so the risk here was moderate; the plan buys %.1f°C of margin." % (ev["counterfactual"]["peak_gpu_c"], ev["counterfactual"]["peak_gpu_c"] - ev["with_plan"]["peak_gpu_c"]))
        if ev.get("rul_used_s") is not None and ev["rul_used_s"] < plan["maintenance_start_in_s"]: caveats.append("By the time the plan was ready only about %s of predicted life was left, less than the %ss maintenance lead time." % (fmt_s(ev["rul_used_s"]), plan["maintenance_start_in_s"]))
    if "APPROVED" in tl: steps.append({"title": "Human", "text": "Operator approved at t+%ss." % int(tl["APPROVED"]["sim_t"])})
    elif "REJECTED" in tl: steps.append({"title": "Human", "text": "Operator rejected: %s" % tl["REJECTED"].get("reason")})
    elif st == "AWAITING_APPROVAL": steps.append({"title": "Human", "text": "Waiting for the operator. Nothing changes until someone approves."})
    if "SCHEDULED" in tl: steps.append({"title": "Action", "text": tl["SCHEDULED"]["reason"]})
    if "IN_MAINTENANCE" in tl: steps.append({"title": "Maintenance", "text": "%s out of service from t+%ss." % (wo["asset"], int(tl["IN_MAINTENANCE"]["sim_t"]))})
    out = wo.get("outcome")
    if "FAILED" in tl:
        ft = tl["FAILED"]["sim_t"]; early = (fail_t - ft) if fail_t else None
        steps.append({"title": "Outcome", "text": "%s failed at t+%ss, %s after the plan was proposed and before anyone approved it.%s" % (wo["asset"], int(ft), fmt_s(ft - (tl.get("AWAITING_APPROVAL", {}).get("sim_t") or ft)), (" That was %s earlier than the central estimate%s." % (fmt_s(early), ", outside the 90% range" if p.get("rul_lo_s") and (ft - (pred_t or 0)) < p["rul_lo_s"] else "")) if early and early > 0 else "")})
        headline = "%s failed before the preventive plan was approved" % wo["asset"]; status = "failed"
        nxt += ["Repair %s (Demo → Repair C02) and keep R17–R32 under watch; the thermal detector will open an incident if any GPU passes 88°C." % wo["asset"],
                "Planning used %s of the %s lead time. Faster options: cap how many options the agent explores, or a policy that auto-approves zero-cost migrations when remaining life is short." % (fmt_s((tl.get("AWAITING_APPROVAL", {}).get("sim_t") or 0) - (pred_t or 0)), fmt_s(p.get("rul_s")))]
    elif out:
        headline = ("Incident prevented: peak %s°C instead of %s°C" if out["prevented"] else "Maintenance done: peak %s°C (forecast without action %s°C)") % (out["observed_peak_gpu_c"], out["counterfactual_peak_gpu_c"]); status = "done"
        steps.append({"title": "Outcome", "text": "Observed pod peak %s°C on %s vs %s°C forecast without action; %d SLA breaches; $%.2f conditional exposure avoided." % (out["observed_peak_gpu_c"], out["hottest_rack"], out["counterfactual_peak_gpu_c"], len(out["breaches"]), out["avoided_exposure_usd"])})
    elif st == "AWAITING_APPROVAL":
        left = (fail_t - time.time()) if False else None
        headline = "Preventive plan waiting for your approval"; status = "active"
        nxt += ["Approve plan v%d in Twin & reliability to schedule maintenance at t+%ss, or reject it with a reason." % (plan["version"], plan["maintenance_start_in_s"]), "If nobody approves, %s is expected to fail around t+%ss." % (wo["asset"], int(fail_t or 0))]
    elif st == "PLANNING": headline = "Agent is evaluating preventive options"; status = "active"
    else: headline = "%s · %s" % (wo["id"], st.replace("_", " ").lower()); status = "active" if st not in ("CLOSED", "REJECTED") else "done"
    agent_words = None
    for rid in runs[::-1]:
        r = db.one("select data from agent_runs where id=?", (rid,)); d = _j(r["data"]) if r else {}
        txt = (plan or {}).get("reasoning_summary") or (d or {}).get("result_text")
        if txt: agent_words = txt; break
    rows = compare_options(opts, plan["actions"] if plan else None, "peak", "cf_peak")
    return {"headline": headline, "status": status, "steps": steps, "caveats": caveats, "next": nxt, "agent_words": agent_words, "options": rows,
            "decision": {"chosen": acts_txt(plan["actions"]) if plan else None, "why": ("lowest forecast peak (%s°C) among options that meet the goal, at $%.2f" % (plan["evaluation"]["with_plan"]["peak_gpu_c"], plan["evaluation"].get("incremental_cost_usd") or 0)) if plan else None},
            "agent_steps": agent_steps(db, runs)}

def story_incident(inc, db):
    tl = inc.get("timeline") or []; plans = inc.get("plans") or []; plan = plans[-1] if plans else None; ev = inc.get("evidence") or {}
    steps = [{"title": "Signal", "text": "%s GPU hotspot %s°C with inlet %s°C. %s" % (inc["rack"], ev.get("gpu_temp_c"), ev.get("inlet_temp_c"), inc.get("probable_cause") or "")},
             {"title": "Detection", "text": "The deterministic detector opened %s (%s) because a GPU stayed above the critical threshold for 3 ticks." % (inc["id"], inc.get("severity"))}]
    runs = inc.get("agent_runs") or []
    if runs: steps.append({"title": "Agent", "text": "%d CREAO run(s): %s." % (len(runs), ", ".join("%s %s" % (r["task"].replace("_", " "), r["status"]) for r in runs[:4]))})
    tcs = [t for t in inc.get("tool_calls") or [] if t["tool"] == "evaluate_plan"]
    if tcs: steps.append({"title": "Options explored", "text": "The agent simulated %d candidate action sets against the forecast, finance and policy engines." % len(tcs)})
    if plan:
        f = plan.get("forecast", {}); fin = plan.get("finance", {}); base = plan.get("baseline", {})
        steps.append({"title": "Decision", "text": "Plan %s v%d: %s. Forecast %s; cost $%.2f vs SLA exposure $%.2f if idle. Policy: %s." % (plan["plan_id"], plan["version"], acts_txt(plan["actions"]), ("recovery in about %ss" % f["recover_s"]) if f.get("recover_s") is not None else "no recovery within 10 min", fin.get("incremental_cost_usd") or 0, base.get("sla_exposure_usd") or 0, plan["policy"]["decision"].replace("_", " ").lower())})
    aprs = inc.get("approvals") or []
    if aprs: steps.append({"title": "Human", "text": "Approval %s for %s v%s." % (aprs[-1]["status"], aprs[-1]["plan_id"], aprs[-1]["plan_version"])})
    for x in inc.get("executions") or []:
        d = x.get("data") or {}; steps.append({"title": "Action", "text": "Execution %s %s%s." % (x["id"], x["state"], (" · commit " + d["applied_sha"][:10]) if d.get("applied_sha") else "")})
    for t in tl:
        if t["state"] in ("ROLLING_BACK", "REPLAN", "DENIED", "ESCALATED"): steps.append({"title": t["state"].replace("_", " ").title(), "text": t.get("reason") or ""})
    status = "done" if inc["state"] == "CLOSED" else "active"
    if inc["state"] == "CLOSED": headline = "%s resolved%s" % (inc["id"], (" in %ss (simulated)" % inc["mttr_sim_s"]) if inc.get("mttr_sim_s") else "")
    elif inc["state"] == "AWAITING_APPROVAL": headline = "Remediation plan waiting for your approval"
    else: headline = "%s · %s" % (inc["id"], inc["state"].replace("_", " ").lower())
    if inc.get("finance"): steps.append({"title": "Closeout", "text": "Incurred $%.2f; invoice %s %s." % (inc["finance"].get("incurred_usd") or 0, inc["finance"].get("invoice_id"), inc["finance"].get("reconciliation_status"))})
    nxt = []
    if inc["state"] == "AWAITING_APPROVAL" and plan: nxt.append("Approve or reject %s v%d in the Control Room, or ask for an alternative." % (plan["plan_id"], plan["version"]))
    rows = []
    for k, t in enumerate(tcs):
        r = t.get("result") or {}; f = r.get("forecast") or {}; fin = r.get("finance") or {}
        rows.append({"n": k + 1, "label": acts_txt(r.get("actions") or (t.get("args") or {}).get("actions")), "peak": f.get("final_gpu_c"), "cf": None, "cost": fin.get("incremental_cost_usd") or 0,
                     "meets": f.get("recover_s") is not None, "policy": (r.get("policy") or {}).get("decision"), "recover": f.get("recover_s"),
                     "chosen": bool(plan) and _norm(r.get("actions") or (t.get("args") or {}).get("actions")) == _norm(plan["actions"])})
    for r in rows:
        if r["chosen"]: r["verdict"] = "chosen"
        elif r["recover"] is None: r["verdict"] = "does not recover within 10 minutes"
        else:
            ch = next((x for x in rows if x["chosen"]), None); why = []
            if ch and ch["recover"] is not None and r["recover"] > ch["recover"]: why.append("recovers %ss slower" % (r["recover"] - ch["recover"]))
            if ch and r["cost"] > ch["cost"] + 0.005: why.append("costs $%.2f more" % (r["cost"] - ch["cost"]))
            if r["policy"] == "FORBIDDEN": why.append("forbidden by policy")
            r["verdict"] = "; ".join(why) or "not selected"
    return {"headline": headline, "status": status, "steps": steps, "caveats": [], "next": nxt, "agent_words": ((plan or {}).get("meta") or {}).get("reasoning_summary") or ((inc.get("summary") or {}).get("text")),
            "options": rows, "decision": {"chosen": acts_txt(plan["actions"]) if plan else None, "why": None}, "agent_steps": agent_steps(db, [r["id"] for r in runs])}

def story_finance(r, db, desk):
    d = _j(r["data"]) or {}; actor = "creao:" + (r["provider_run_id"] or r["id"])
    if r["task"] == "specialist:finance_explainer":
        ex = db.one("select reason from ap_decisions where invoice_id=? and kind='explanation' and actor=? order by id desc", (d.get("invoice_id"), actor))
        return {"headline": "Explained the decision on %s" % d.get("invoice_id"), "status": "done" if ex else "active", "steps": [{"title": "Question", "text": d.get("question") or ""}, {"title": "Answer", "text": (ex or {}).get("reason") or "pending"}],
                "caveats": [], "next": [], "agent_words": (ex or {}).get("reason"), "options": [], "decision": {}, "agent_steps": agent_steps(db, [r["id"]])}
    recs = db.all("select invoice_id, decision, amount, reason from ap_decisions where actor=? and kind='agent_recommendation' order by id", (actor,))
    rows = []; agree = 0
    for k, x in enumerate(recs):
        a = desk.analyze(x["invoice_id"]); ok = a["decision"] == x["decision"] and abs(a["amount"] - x["amount"]) < 0.01; agree += ok
        rows.append({"n": k + 1, "label": "%s · %s $%.2f" % (x["invoice_id"], x["decision"], x["amount"]), "verdict": ("agrees with the deterministic matcher" if ok else "differs: matcher says %s $%.2f" % (a["decision"], a["amount"])), "chosen": ok, "reason": x["reason"]})
    steps = [{"title": "Inbox", "text": "%d invoices were checked against the contract, the metered usage and the vendor master." % len(recs)},
             {"title": "Agent", "text": "The CREAO AP Analyst recommended a decision for each invoice and explained it."},
             {"title": "Cross-check", "text": "Its recommendations match the deterministic three-way matcher on %d of %d invoices." % (agree, len(recs))},
             {"title": "Human", "text": "Nothing is paid until the controller signs off; money never moves on the agent's say-so."}]
    if not recs:
        steps = [{"title": "Agent", "text": "This CREAO AP Analyst run (%s) finished without recording any invoice recommendations, so there is nothing to cross-check." % r["status"]}]
        return {"headline": "AP review run with no recorded recommendations", "status": "failed" if r["status"] != "completed" else "done", "steps": steps, "caveats": [], "next": ["Run AP review again from Finance desk."], "agent_words": (d.get("result_text") or "")[:1500] or None, "options": [], "decision": {}, "agent_steps": agent_steps(db, [r["id"]])}
    return {"headline": "AP review: agent and matcher agree on %d/%d invoices" % (agree, len(recs)), "status": "done", "steps": steps, "caveats": [], "next": ["Open Finance desk to sign off or dispute; disagreements are listed first."] if agree < len(recs) else [],
            "agent_words": (d.get("result_text") or "")[:1500] or None, "options": rows, "decision": {}, "agent_steps": agent_steps(db, [r["id"]])}
