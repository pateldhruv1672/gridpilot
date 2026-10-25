"""SpacetimeDB publisher for GRIDPILOT (maincloud/gridpilot).

The Python thermal model, CREAO/Cartesia integrations and the git config store stay in this backend worker. Once per
second it publishes the live physical snapshot (racks, cooling units, facility summary, thermal field) plus any
incident / plan / audit-event changes in ONE `publish_tick` reducer call. The dashboard subscribes to those tables.

Credential: the worker's own SpacetimeDB identity token lives in Mana's server-side secrets as SPACETIMEDB_WORKER_TOKEN.
Mana's symbolic proxy injects it on the way out, so it never enters this process. The module only accepts writes from
identities in its private `writer` table (owner + this worker); everyone else is read-only.
"""
import asyncio, json, os, re, time, traceback
import requests
import integrations as IX

HOST = os.environ.get("SPACETIMEDB_HOST", "https://maincloud.spacetimedb.com")
DB = os.environ.get("SPACETIMEDB_DB", "gridpilot")
INTERVAL_S = float(os.environ.get("SPACETIMEDB_INTERVAL_S", "1.0"))
CALL = IX._Proxy("gp_stdb_call", HOST + "/v1/database/" + DB + "/call", "POST",
                 {"authorization": "Bearer ${secrets.SPACETIMEDB_WORKER_TOKEN}", "content-type": "application/json"})

def _wire(name):
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name); s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", "_", s); s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", "_", s)
    return s.lower()
def W(v):
    if isinstance(v, dict): return {_wire(k): W(x) for k, x in v.items()}
    if isinstance(v, list): return [W(x) for x in v]
    return v

def _f(x, d=0.0):
    try: return float(x) if x is not None else d
    except Exception: return d

def _act(a):
    from story import act_txt
    return {"kind": a.get("type", ""), "workload": a.get("workload") or "", "fromRack": a.get("from_rack") or "", "toRack": a.get("to_rack") or "",
            "rack": a.get("rack") or "", "watts": _f(a.get("watts")), "fromWatts": _f(a.get("from_watts")), "unit": a.get("unit") or "",
            "fanPct": _f(a.get("fan_pct")), "fromPct": _f(a.get("from_pct")), "text": act_txt(a)}

def _summ(typ, d):
    if typ == "state_transition": return "%s → %s · %s" % (d.get("from"), d.get("to"), d.get("reason") or "")
    for k in ("reason", "summary", "message", "operator_said", "tool", "mode", "kind"):
        if d.get(k): return "%s: %s" % (k, str(d[k])[:200])
    return ""

class Publisher:
    def __init__(self, eng, sim):
        self.eng, self.sim = eng, sim
        self.last = 0.0; self.inflight = False; self.seq = 0
        self.ok = 0; self.fail = 0; self.last_ok = None; self.last_error = None; self.last_ms = None; self.backoff_until = 0
        self.inc_sig = {}; self.plan_sig = {}; self.last_event = None; self.enabled = True

    def status(self):
        return {"enabled": self.enabled, "host": HOST, "database": DB, "interval_s": INTERVAL_S, "published": self.ok, "failed": self.fail,
                "last_ok_ts": self.last_ok, "last_error": self.last_error, "last_call_ms": self.last_ms, "seq": self.seq}

    # ---- payload builders -------------------------------------------------------------------------------------
    def _physical(self, snap):
        lat = snap.get("latency") or {}
        racks = []
        for i, r in enumerate(snap["racks"]):
            wl = list(r.get("workloads") or [])
            racks.append({"rackId": r["rack_id"], "idx": i, "row": int(r["row"]), "col": int(r["col"]), "gpuCount": int(r["gpu_count"]),
                          "utilization": _f(r["utilization"]), "powerKw": _f(r["power_kw"]), "gpuTempC": _f(r["gpu_temp_c"]), "inletTempC": _f(r["inlet_temp_c"]),
                          "exhaustTempC": _f(r["exhaust_temp_c"]), "airflowM3s": _f(r["airflow_m3s"]), "coolingEfficiency": _f(r["cooling_efficiency"]),
                          "powerLimitW": _f(r["power_limit_w"]), "zone": r.get("zone") or "", "reservePool": bool(r.get("reserve_pool")), "workloads": wl,
                          "workloadP95Ms": [_f((lat.get(w) or {}).get("p95_ms")) for w in wl], "workloadLimitMs": [_f((lat.get(w) or {}).get("limit_ms")) for w in wl],
                          "workloadProtected": [bool((lat.get(w) or {}).get("protected")) for w in wl], "status": r.get("status") or "normal", "simT": _f(snap["t"])})
        cooling = []
        for k, c in enumerate(snap["cooling"]):
            eff, ff = _f(c["effectiveness"]), _f(c["flow_factor"], 1)
            st = "failed" if eff < 0.25 or ff < 0.2 else "degraded" if eff < 0.85 or ff < 0.85 else "nominal"
            cooling.append({"unitId": c["unit_id"], "idx": k, "fanPct": _f(c["fan_pct"]), "effectiveness": eff, "flowFactor": ff, "supplyTempC": _f(c["supply_temp_c"]),
                            "returnTempC": _f(c["return_temp_c"]), "aisleSupplyM3s": _f(c["aisle_supply_m3s"]), "aisleDemandM3s": _f(c["aisle_demand_m3s"]),
                            "aisleDeficit": _f(c["aisle_deficit"]), "status": st, "simT": _f(snap["t"])})
        tot = snap["totals"]; crit = sum(1 for r in snap["racks"] if r["status"] == "critical"); elev = sum(1 for r in snap["racks"] if r["status"] == "elevated")
        p95s = [v["p95_ms"] for v in lat.values()]
        self.seq += 1
        fac = {"id": 1, "simT": _f(snap["t"]), "wallTs": time.time(), "itKw": _f(tot["it_kw"]), "facilityKw": _f(tot["facility_kw"]), "pue": _f(tot["pue"]),
               "gpuUtil": _f(tot["gpu_util"]), "maxInletC": max(_f(r["inlet_temp_c"]) for r in snap["racks"]), "criticalRacks": crit, "elevatedRacks": elev,
               "slaBreaches": sum(1 for v in lat.values() if v.get("breach")), "latencyP95Ms": max(p95s) if p95s else 0.0,
               "electricityUsd": _f(self.eng.cost.get("electricity_usd")), "transferUsd": _f(self.eng.cost.get("transfer_usd")),
               "health": "CRITICAL" if crit else "DEGRADED" if elev else "NOMINAL", "speed": _f(self.eng.speed, 1), "paused": bool(self.eng.paused),
               "migrations": [{"workload": m.get("workload", ""), "fromRack": m.get("from", ""), "toRack": m.get("to", ""), "progress": _f(m.get("progress"))} for m in (snap.get("migrations") or [])],
               "seq": self.seq}
        fld = snap.get("field")
        field = [{"id": 1, "simT": _f(snap["t"]), "nx": int(fld["nx"]), "nz": int(fld["nz"]), "temp": [round(float(x), 2) for x in fld["T"]],
                  "u": [round(float(x), 3) for x in fld["u"]], "w": [round(float(x), 3) for x in fld["w"]]}] if fld else []
        return fac, racks, cooling, field

    def _incidents(self, full=False):
        rows, plans = [], []
        db = self.eng.db
        for r in db.all("select * from incidents order by created desc limit 6"):
            d = json.loads(r["data"] or "{}")
            pl = db.one("select id, version from plans where incident_id=? order by version desc limit 1", (r["id"],))
            sig = (r["state"], len(r["data"] or ""), pl and pl["version"])
            if not full and self.inc_sig.get(r["id"]) == sig: continue
            self.inc_sig[r["id"]] = sig
            rows.append({"incidentId": r["id"], "state": r["state"], "severity": d.get("severity") or "", "rack": r["rack"] or "", "title": d.get("title") or "",
                         "probableCause": d.get("probable_cause") or "", "detectedSimT": _f(d.get("detected_sim_t")), "createdTs": _f(r["created"]), "updatedTs": time.time(),
                         "planId": pl["id"] if pl else "", "planVersion": int(pl["version"]) if pl else 0, "mttrSimS": _f(d.get("mttr_sim_s"), -1.0),
                         "summary": ((d.get("summary") or {}).get("text") or "")[:4000] if isinstance(d.get("summary"), dict) else str(d.get("summary") or "")[:4000]})
        recent = [r["id"] for r in db.all("select id from incidents order by created desc limit 6")]
        if recent:
            q = "select * from plans where incident_id in (%s)" % ",".join("?" * len(recent))
            for p in db.all(q, tuple(recent)):
                key = "%s:%s" % (p["id"], p["version"]); sig = (p["status"],)
                if not full and self.plan_sig.get(key) == sig: continue
                self.plan_sig[key] = sig
                d = json.loads(p["data"] or "{}"); ev = d.get("evaluation") or {}; meta = d.get("meta") or {}
                pol = ev.get("policy") or {}
                rules = [x.get("rule") for x in (pol.get("plan_rules") or []) + (pol.get("actions") or []) if x.get("decision") != "AUTO_ALLOWED" and x.get("rule")]
                fc = ev.get("forecast") or {}; fin = ev.get("finance") or {}; base = ev.get("baseline") or {}
                plans.append({"planKey": key, "planId": p["id"], "incidentId": p["incident_id"], "version": int(p["version"]), "status": p["status"] or "", "author": p["author"] or "",
                              "actions": [_act(a) for a in (ev.get("actions") or d.get("actions") or [])], "recoverS": _f(fc.get("recover_s"), -1.0), "finalGpuC": _f(fc.get("final_gpu_c")),
                              "incrementalCostUsd": _f(fin.get("incremental_cost_usd")), "slaExposureUsd": _f(fin.get("sla_exposure_usd")), "baselineSlaUsd": _f(base.get("sla_exposure_usd")),
                              "operationalRisk": ev.get("operational_risk") or "", "policyDecision": pol.get("decision") or "", "policyRules": list(dict.fromkeys(rules)),
                              "reasoning": (meta.get("reasoning_summary") or "")[:4000], "confidence": _f(meta.get("confidence"), -1.0), "createdTs": _f(p["created"])})
        return rows, plans

    def _events(self):
        db = self.eng.db
        if self.last_event is None:
            mx = db.one("select max(id) m from events")["m"] or 0
            self.last_event = max(0, mx - 300)
        out = []
        for e in db.all("select * from events where id>? and type not in ('duplicate_telemetry_suppressed') order by id limit 200", (self.last_event,)):
            try: d = json.loads(e["data"] or "{}")
            except Exception: d = {}
            out.append({"eventId": int(e["id"]), "incidentId": e["incident_id"] or "", "ts": _f(e["ts"]), "simT": _f(e["sim_t"]), "kind": e["type"], "actor": e["actor"] or "",
                        "summary": _summ(e["type"], d)[:400], "detail": json.dumps(d)[:2000]})
        if out: self.pending_last = out[-1]["eventId"]
        return out

    # ---- loop hook -------------------------------------------------------------------------------------------
    def maybe_publish(self, snap):
        """Called from the sim loop with every fresh snapshot; the 1 Hz loop (run) publishes the latest one."""
        self.latest = snap

    async def run(self):
        while True:
            t0 = time.time()
            snap = getattr(self, "latest", None)
            if snap is not None and self.enabled and not self.inflight and t0 >= self.backoff_until:
                self._publish(snap)
            await asyncio.sleep(max(0.05, INTERVAL_S - (time.time() - t0)))

    def _publish(self, snap):
        now = time.time()
        self.last = now; self.inflight = True
        try:
            fac, racks, cooling, field = self._physical(snap)
            incs, plans = self._incidents(full=(self.ok == 0))
            self.pending_last = None
            evs = self._events()
            body = [W(fac), W(racks), W(cooling), W(field), W(incs), W(plans), W(evs)]
        except Exception as e:
            self.inflight = False; self.last_error = "build: %s" % e; traceback.print_exc(); return
        asyncio.get_event_loop().create_task(self._send(body, incs, plans))

    async def _send(self, body, incs, plans):
        t0 = time.time()
        try:
            await asyncio.to_thread(self._post, "/publish_tick", body)
            self.ok += 1; self.last_ok = time.time(); self.last_error = None; self.last_ms = int((time.time() - t0) * 1000)
            if self.pending_last: self.last_event = self.pending_last
        except Exception as e:
            self.fail += 1; self.last_error = str(e)[:300]
            for i in incs: self.inc_sig.pop(i["incidentId"], None)        # resend changes next time
            for p in plans: self.plan_sig.pop(p["planKey"], None)
            if "429" in str(e) or "503" in str(e): self.backoff_until = time.time() + 5
        finally:
            self.inflight = False

    def _post(self, sub, body):
        p = CALL.get()
        r = requests.post(p.url + sub, headers=p.headers, data=json.dumps(body, separators=(",", ":")), timeout=20, allow_redirects=False)
        if r.status_code >= 400: raise IX.ProviderError("SpacetimeDB %s -> HTTP %s: %s" % (sub, r.status_code, r.text[:300]))

    async def clear_operational(self):
        self.inc_sig.clear(); self.plan_sig.clear()
        mx = self.eng.db.one("select max(id) m from events")["m"] or 0; self.last_event = mx
        try: await asyncio.to_thread(self._post, "/clear_operational", [])
        except Exception as e: self.last_error = "clear: %s" % e
