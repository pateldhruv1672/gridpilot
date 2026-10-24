"""Incident engine end to end, offline (no sponsor calls): python tests/test_incident_flow.py  [fail]  (fail = arm the post-apply fault and test rollback)"""

import sys, asyncio, json, time, os, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import sim as S, configstore as CS, engine as E
CS.REPO = os.path.join(tempfile.mkdtemp(), "repo"); shutil.rmtree(CS.REPO, ignore_errors=True)
E.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
if os.path.exists(E.DB_PATH): os.remove(E.DB_PATH)
CS.init(S.initial_config())
s = S.Sim(CS.read_tree())
eng = E.Engine(s, "owner")
eng.db.set("auto_call", False)
async def run(fail=False):
    eng.inject("thermal")
    for i in range(400):
        s.step(0.5, speed=4); eng.tick(); await asyncio.sleep(0)
        inc = eng.open_incident()
        if inc and inc["state"] in ("DETECTED","TRIAGING"): break
    inc = eng.open_incident(); print("incident", inc["id"], inc["state"], "t", round(s.t), inc["probable_cause"])
    # CREAO not configured: state should remain DETECTED; simulate a plan proposal path only to exercise backend (actor = test)
    await asyncio.sleep(0.2)
    inc = eng.incident(inc["id"]); print("state after triage attempt:", inc["state"])
    # throttle-only (auto-allowed) evaluation
    ev = await eng.evaluate([{"type":"set_gpu_power_limit","rack":"R17","watts":405}], "R17")
    print("throttle-only: recover", ev["forecast"]["recover_s"], "final", ev["forecast"]["final_gpu_c"], "policy", ev["policy"]["decision"])
    inc["state"]="TRIAGING"; eng.save_incident(inc)   # test harness only
    acts=[{"type":"migrate_workload","workload":"inference_api_07","to_rack":"R14"},{"type":"migrate_workload","workload":"inference_api_12","to_rack":"R38"},
          {"type":"set_gpu_power_limit","rack":"R17","watts":405},{"type":"set_cooling_fan","unit":"C01","fan_pct":125},{"type":"set_cooling_fan","unit":"C03","fan_pct":125}]
    if fail: eng.fail_armed = True
    res = await eng.propose_plan(inc["id"], acts, "test-harness", {"diagnosis":"test"})
    print("plan", res["plan_id"], res["plan_version"], res["policy_decision"], "cost", res["evaluation"]["finance"]["incremental_cost_usd"], "exposure", res["evaluation"]["finance"]["sla_exposure_usd"], [r["rule"] for r in res["evaluation"]["policy"]["plan_rules"]])
    ok,msg = await eng.request_execution(inc["id"], res["plan_id"], 1, "test"); print("exec without approval ->", ok, msg)
    ok,msg = eng.verify_approval(inc["id"], res["plan_id"], 1, "dashboard", "operator:test"); print("approve", ok, msg)
    ok,msg = await eng.request_execution(inc["id"], res["plan_id"], 1, "test"); print("execute", ok, msg)
    ok2,msg2 = await eng.request_execution(inc["id"], res["plan_id"], 1, "test"); print("duplicate ->", ok2, msg2)
    for i in range(1500):
        s.step(0.5, speed=4); eng.tick(); await asyncio.sleep(0.001)
        st = eng.incident(inc["id"])["state"]
        if st in ("CLOSED","REPLAN","ESCALATED"): break
    inc = eng.incident(inc["id"])
    print("final state", inc["state"], "mttr", inc.get("mttr_sim_s"), "R17", round(s.gpu_t[16],1), "R14", round(s.gpu_t[13],1))
    print([ (t["from"],t["to"] if "to" in t else t["state"]) for t in inc["timeline"]])
    ex = eng.db.all("select * from executions")
    for e in ex:
        d=json.loads(e["data"]); print("exec", e["state"], [ (st["name"],st["ok"]) for st in d.get("steps",[])])
        print("validation", [(c["name"], c["ok"]) for c in d.get("validation",[])])
        print(d.get("diff","")[:900])
        if d.get("rollback"): print("ROLLBACK diff head:", d["rollback"]["diff"][:400], d["rollback"]["verified"])
    for r in eng.db.all("select * from invoices"):
        d=json.loads(r["data"]); print("invoice", d["invoice"]["total_usd"], d["reconciliation"])
import sys as _s
asyncio.run(run(fail=len(_s.argv)>1))
