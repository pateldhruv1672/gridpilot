"""GRIDPILOT digital twin: the full cyber-physical state of pod SFO-01 beyond the thermal field.

Subsystems: energy supply, battery storage, cooling, facility, IT & security. Every asset is SIMULATED, coupled to the
physics simulator (sim.py) where it matters (a failed CRAH really loses airflow; BESS dispatch really changes grid import).
Wear is hidden state; agents and the predictor only see noisy sensor readings, exactly like a real DCIM feed.
Deterministic (seeded) so runs are reproducible.
"""
import math, numpy as np

SITE_START_H = 13.0          # site clock at sim t=0 (13:00)
SITE_H_PER_SIM_S = 1 / 60.0  # one sim minute = one site hour, so a full tariff day plays out in 24 sim minutes
PEAK = (16.0, 21.0)          # utility peak window (site clock)

def tariff(h):
    h %= 24
    if PEAK[0] <= h < PEAK[1]: return 0.31
    if 7 <= h < PEAK[0] or PEAK[1] <= h < 23: return 0.16
    return 0.09

def carbon(h):   # gCO2/kWh, solar dip midday, evening ramp
    h %= 24
    return round(260 - 120 * math.exp(-((h - 13) / 3.2) ** 2) + 90 * math.exp(-((h - 19.5) / 2.0) ** 2), 1)

# id, subsystem, kind, name, serves, failure threshold note
ASSETS = [
    ("UTIL-A", "energy", "utility_feed", "Utility feed A (12.47 kV)", "pod SFO-01"),
    ("XFMR-1", "energy", "transformer", "Step-down transformer 1", "PDU-A, PDU-B"),
    ("GEN-1", "energy", "generator", "Standby diesel generator", "whole pod"),
    ("BESS-1", "battery", "bess", "Battery storage (500 kWh / 250 kW)", "peak shaving, ride-through"),
    ("UPS-A", "battery", "ups", "UPS string A", "rows A-D"),
    ("UPS-B", "battery", "ups", "UPS string B", "rows E-H"),
    ("C01", "cooling", "crah", "CRAH C01", "cold aisle A (rows A, B)"),
    ("C02", "cooling", "crah", "CRAH C02", "cold aisle B (rows C, D)"),
    ("C03", "cooling", "crah", "CRAH C03", "cold aisle C (rows E, F)"),
    ("C04", "cooling", "crah", "CRAH C04", "cold aisle D (rows G, H)"),
    ("CH-1", "cooling", "chiller", "Chiller CH-1", "chilled water loop"),
    ("PDU-A", "facility", "pdu", "PDU A", "rows A-D"),
    ("PDU-B", "facility", "pdu", "PDU B", "rows E-H"),
    ("LEAK-1", "facility", "leak", "Leak detection loop", "under-floor CHW piping"),
    ("VESDA-1", "facility", "fire", "VESDA smoke detection", "pod air"),
    ("CORE-SW1", "it_security", "switch", "Core switch 1", "fabric A"),
    ("CORE-SW2", "it_security", "switch", "Core switch 2", "fabric B"),
    ("ACCESS-1", "it_security", "access", "Cage access control", "cages 1-4"),
]
SUBSYSTEMS = [("energy", "Energy supply"), ("battery", "Battery storage"), ("cooling", "Cooling"), ("facility", "Facility"), ("it_security", "IT & security")]
PREDICTABLE = {"crah": "fan bearing wear", "ups": "battery string aging", "chiller": "condenser fouling", "bess": "cell imbalance", "pdu": "breaker contact wear"}

class Twin:
    def __init__(self, seed=7):
        self.rng = np.random.default_rng(seed)
        self.t = 0.0
        self.assets = {}
        for aid, sub, kind, name, serves in ASSETS:
            base = {"crah": 0.08, "ups": 0.12, "chiller": 0.10, "bess": 0.06, "pdu": 0.05}.get(kind, 0.0)
            self.assets[aid] = {"id": aid, "subsystem": sub, "kind": kind, "name": name, "serves": serves,
                                "wear": base + 0.04 * self.rng.random(),          # hidden true degradation 0..1 (1 = functional failure)
                                "rate": 1.2e-6 * (1 + self.rng.random()),      # per sim second: months of life at baseline
                                "state": "online", "sensors": {}, "failed_at": None, "maint": None, "true_fail_t": None}
        self.bess = {"soc": 0.72, "kw": 0.0, "mode": "idle", "saved_usd": 0.0, "throughput_kwh": 0.0}
        self.energy = {"grid_kw": 0.0, "cost_usd": 0.0, "cost_no_bess_usd": 0.0, "carbon_kg": 0.0}
        self.security = []           # access / firmware events
        self.series = []
        self._last_series = -99

    # ------------------------------------------------------------ clock
    def site_hour(self, t=None): return (SITE_START_H + (self.t if t is None else t) * SITE_H_PER_SIM_S) % 24

    # ------------------------------------------------------------ scenarios
    def inject(self, kind, sim=None, unit=None):
        if kind == "crah_bearing_wear":           # a CRAH fan bearing starts wearing fast: functional failure in about 15 sim minutes
            a = self.assets[unit if unit in ("C01", "C02", "C03", "C04") else "C02"]; a["rate"] = (1 - a["wear"]) / 900.0     # accelerated wear from its current level (no step change)
        elif kind == "grid_price_spike":          # wholesale price spike: tariff x4 for 3 site hours (3 sim minutes)
            self.spike_mult = 4.0; self.spike_until = self.t + 180
        elif kind == "ups_aging":
            a = self.assets["UPS-A"]; a["wear"] = max(a["wear"], 0.45); a["rate"] = (1 - a["wear"]) / 1500.0
        elif kind == "security_anomaly":
            h = self.site_hour()
            self.security.append({"t": round(self.t, 1), "site_time": "%02d:%02d" % (int(h), int((h % 1) * 60)), "kind": "badge", "who": "contractor badge #4471",
                                  "where": "cage 3 (rows E-F)", "detail": "after-hours entry, badge not scheduled, door held open 94 s", "severity": "high"})
            self.security.append({"t": round(self.t + 5, 1), "site_time": "", "kind": "firmware", "who": "PDU-B mgmt interface", "where": "PDU-B",
                                  "detail": "firmware upload attempt from unregistered host 10.20.4.77", "severity": "high"})
        elif kind == "repair_all":
            for a in self.assets.values():
                if a["kind"] in PREDICTABLE: a["wear"] = 0.05; a["rate"] = 1.2e-6
                a["state"] = "online"; a["failed_at"] = None; a["maint"] = None

    # ------------------------------------------------------------ dynamics
    def step(self, sim, dt):
        if dt <= 0: return []
        self.t = sim.t
        out = []
        for a in self.assets.values():
            if a["kind"] in PREDICTABLE and a["state"] != "maintenance":
                a["wear"] = min(1.2, a["wear"] + a["rate"] * dt * (1 + 0.15 * self.rng.standard_normal()))
                a["true_fail_t"] = round(self.t + max(0.0, (1 - a["wear"]) / max(a["rate"], 1e-12)), 1) if a["wear"] < 1 else a["failed_at"]
                if a["wear"] >= 1 and a["state"] == "online":
                    a["state"] = "failed"; a["failed_at"] = round(self.t, 1); out.append(("asset_failed", a["id"]))
                    self._apply_failure(sim, a)
            m = a.get("maint")
            if m and m["status"] == "scheduled" and self.t >= m["start"]:
                m["status"] = "in_progress"; a["state"] = "maintenance"; out.append(("maintenance_started", a["id"])); self._apply_offline(sim, a)
            elif m and m["status"] == "in_progress" and self.t >= m["start"] + m["duration"]:
                m["status"] = "done"; m["done_at"] = round(self.t, 1); a["wear"] = 0.04; a["rate"] = 1.2e-6; a["state"] = "online"; a["failed_at"] = None
                out.append(("maintenance_done", a["id"])); self._restore(sim, a)
        self._sensors(sim)
        self._energy(sim, dt)
        if self.t - self._last_series >= 2:
            self._last_series = self.t
            self.series.append({"t": round(self.t, 1), "grid_kw": round(self.energy["grid_kw"], 1), "bess_kw": round(self.bess["kw"], 1), "soc": round(self.bess["soc"] * 100, 1),
                                "tariff": round(tariff(self.site_hour()) * (getattr(self, "spike_mult", 1.0) if self.t < getattr(self, "spike_until", -1) else 1.0), 3), "carbon": carbon(self.site_hour()), "site_h": round(self.site_hour(), 2),
                                **{"vib_" + k: self.assets[k]["sensors"].get("vibration_mm_s") for k in ("C01", "C02", "C03", "C04")},
                                "ups_a_runtime": self.assets["UPS-A"]["sensors"].get("runtime_min")})
            self.series = self.series[-300:]
        return out

    def _unit_k(self, a): return int(a["id"][1:]) - 1
    def _apply_failure(self, sim, a):
        if a["kind"] == "crah": sim.degrade_unit(self._unit_k(a), 0.30, 0.25)
    def _apply_offline(self, sim, a):
        if a["kind"] == "crah": sim.degrade_unit(self._unit_k(a), 0.30, 0.25)
    def _restore(self, sim, a):
        if a["kind"] == "crah": sim.degrade_unit(self._unit_k(a), 1.0, 1.0)

    def _n(self, s): return float(self.rng.standard_normal()) * s

    def _sensors(self, sim):
        A = self.assets; it_kw = float(sim.it_kw); fac = float(sim.it_kw + sim.cool_kw + 0.04 * sim.it_kw)
        for k in range(4):
            a = A["C%02d" % (k + 1)]; w = min(a["wear"], 1.2); off = a["state"] == "maintenance"
            a["sensors"] = {"vibration_mm_s": round(0 if off else 1.6 + 7.4 * w * w + self._n(0.18), 2),            # ISO 10816 zone C alarm ~7.1 mm/s
                            "bearing_temp_c": round(0 if off else 38 + 22 * w * w + self._n(0.5), 1),
                            "fan_current_a": round(0 if off else 11.0 * float(sim.fan[k]) * (1 + 0.18 * w) + self._n(0.12), 2),
                            "filter_dp_pa": round(85 + 60 * w + self._n(2.0), 1),
                            "effectiveness_pct": round(float(sim.unit_eff[k]) * 100, 1), "supply_c": round(float(sim.sup_T[k]), 1)}
        ch = A["CH-1"]; w = ch["wear"]
        ch["sensors"] = {"cop": round(5.6 - 2.2 * w + self._n(0.05), 2), "condenser_approach_c": round(2.2 + 6.5 * w + self._n(0.15), 2),
                         "chw_supply_c": round(7.0 + 1.8 * w * w + self._n(0.05), 2), "load_kw": round(float(sim.cool_kw), 1)}
        for uid in ("UPS-A", "UPS-B"):
            a = A[uid]; w = a["wear"]; load = it_kw * 0.5
            r = 2.1 + 3.4 * w * w + self._n(0.04)
            a["sensors"] = {"internal_resistance_mohm": round(r, 2), "string_v": round(540 - 9 * w + self._n(0.6), 1), "load_kw": round(load, 1),
                            "runtime_min": round(max(0.5, 14.0 * (1 - 0.85 * w * w) * 300 / max(load, 50)), 1), "cell_temp_c": round(25 + 4 * w + self._n(0.2), 1)}
        b = A["BESS-1"]; w = b["wear"]
        b["sensors"] = {"soc_pct": round(self.bess["soc"] * 100, 1), "power_kw": round(self.bess["kw"], 1), "soh_pct": round(100 - 18 * w, 1),
                        "cell_dT_c": round(1.2 + 7 * w * w + abs(self.bess["kw"]) / 250 * 0.8 + self._n(0.1), 2), "mode": self.bess["mode"]}
        x = A["XFMR-1"]; load = fac / 1200.0
        x["sensors"] = {"load_pct": round(load * 100, 1), "oil_temp_c": round(42 + 38 * load * load + self._n(0.3), 1), "thd_pct": round(3.1 + self._n(0.1), 2)}
        A["UTIL-A"]["sensors"] = {"import_kw": round(self.energy["grid_kw"], 1), "voltage_kv": round(12.47 + self._n(0.02), 3), "freq_hz": round(60 + self._n(0.004), 3),
                                  "tariff_usd_kwh": tariff(self.site_hour()), "carbon_g_kwh": carbon(self.site_hour())}
        A["GEN-1"]["sensors"] = {"fuel_pct": 91.0, "battery_v": round(26.4 + self._n(0.03), 2), "coolant_c": round(41 + self._n(0.2), 1), "last_test": "weekly load test passed (site day -2)"}
        for pid, rows in (("PDU-A", range(0, 4)), ("PDU-B", range(4, 8))):
            a = A[pid]; kw = float(sum(sim.P[r * 8:(r + 1) * 8].sum() for r in rows)); w = a["wear"]
            a["sensors"] = {"load_kw": round(kw, 1), "load_pct": round(kw / 420 * 100, 1), "breaker_temp_c": round(35 + 18 * (kw / 420) ** 2 + 12 * w * w + self._n(0.3), 1),
                            "phase_imbalance_pct": round(2.0 + 3 * w + self._n(0.15), 2)}
        A["LEAK-1"]["sensors"] = {"leak": False, "loop_ohm": round(1200 + self._n(4), 0)}
        A["VESDA-1"]["sensors"] = {"obscuration_pct_m": round(abs(0.004 + self._n(0.001)), 4)}
        for sid in ("CORE-SW1", "CORE-SW2"):
            A[sid]["sensors"] = {"temp_c": round(44 + 0.15 * (float(sim.inlet.mean()) - 22) + self._n(0.3), 1), "pkt_loss_pct": round(abs(self._n(0.002)), 4), "fw": "9.3.12 (compliant)"}
        A["ACCESS-1"]["sensors"] = {"events_1h": len([e for e in self.security if self.t - e["t"] < 300]), "open_alerts": len([e for e in self.security if e["severity"] == "high"])}

    def _energy(self, sim, dt):
        fac = float(sim.it_kw + sim.cool_kw + 0.04 * sim.it_kw); h = self.site_hour()
        spiking = self.t < getattr(self, "spike_until", -1)
        price = tariff(h) * (getattr(self, "spike_mult", 1.0) if spiking else 1.0)
        b = self.bess; cap = 500.0
        # Energy agent policy (deterministic, autonomous within bounds): shave peak above 640 kW during the peak window, keep a 40% ride-through reserve, recharge off-peak.
        target = 0.0; mode = "idle"
        if spiking and b["soc"] > 0.40: target = 250.0; mode = "price_spike_discharge"
        elif PEAK[0] <= h < PEAK[1] and b["soc"] > 0.40: target = min(250.0, max(0.0, fac - 560.0) + 60.0); mode = "peak_shave"
        elif price <= 0.09 and b["soc"] < 0.95: target = -150.0; mode = "charge"
        b["kw"] += (target - b["kw"]) * min(1, dt / 4)
        b["soc"] = float(np.clip(b["soc"] - b["kw"] * dt / 3600 / cap * 60, 0, 1))   # x60: sim minute = site hour, so energy moves at site-clock pace
        if mode != b["mode"]: b["mode"] = mode; self.mode_changed = (round(self.t, 1), mode)
        grid = fac - b["kw"]; self.energy["grid_kw"] = grid
        site_hours = dt * SITE_H_PER_SIM_S
        self.energy["cost_usd"] += grid * site_hours * price; self.energy["cost_no_bess_usd"] += fac * site_hours * price
        self.energy["carbon_kg"] += grid * site_hours * carbon(h) / 1000
        b["saved_usd"] = self.energy["cost_no_bess_usd"] - self.energy["cost_usd"]
        b["throughput_kwh"] += abs(b["kw"]) * site_hours

    # ------------------------------------------------------------ views
    def subsystem_health(self, preds):
        out = []
        for sid, label in SUBSYSTEMS:
            assets = [a for a in self.assets.values() if a["subsystem"] == sid]
            risk = [preds.get(a["id"], {}) for a in assets]
            worst = "nominal"
            effs = [a["sensors"].get("effectiveness_pct", 100) for a in assets if a["kind"] == "crah"]
            if any(a["state"] == "failed" for a in assets): worst = "failed"
            elif effs and min(effs) < 60: worst = "critical"
            elif any(r.get("risk") == "critical" for r in risk) or (sid == "it_security" and any(e["severity"] == "high" for e in self.security[-5:])): worst = "critical"
            elif any(r.get("risk") == "warning" for r in risk) or any(a["state"] == "maintenance" for a in assets) or (effs and min(effs) < 85): worst = "warning"
            out.append({"id": sid, "label": label, "status": worst, "assets": len(assets), "kpi": self._kpi(sid)})
        return out

    def _kpi(self, sid):
        A = self.assets
        if sid == "energy": return {"Grid import": "%.0f kW" % self.energy["grid_kw"], "Tariff": "$%.2f/kWh" % tariff(self.site_hour()), "Carbon": "%.0f g/kWh" % carbon(self.site_hour())}
        if sid == "battery": return {"BESS SoC": "%.0f%%" % (self.bess["soc"] * 100), "Mode": self.bess["mode"].replace("_", " "), "UPS-A runtime": "%.1f min" % A["UPS-A"]["sensors"].get("runtime_min", 0)}
        if sid == "cooling": return {"Worst vibration": "%.1f mm/s" % max(A[c]["sensors"].get("vibration_mm_s", 0) for c in ("C01", "C02", "C03", "C04")), "Chiller COP": "%.2f" % A["CH-1"]["sensors"].get("cop", 0), "Min CRAH eff": "%.0f%%" % min(A[c]["sensors"].get("effectiveness_pct", 0) for c in ("C01", "C02", "C03", "C04"))}
        if sid == "facility": return {"PDU-A load": "%.0f%%" % A["PDU-A"]["sensors"].get("load_pct", 0), "PDU-B load": "%.0f%%" % A["PDU-B"]["sensors"].get("load_pct", 0), "Leak": "none"}
        return {"Core switches": "2/2 up", "Firmware": "compliant", "Security alerts": str(len([e for e in self.security if e["severity"] == "high"]))}

    def snapshot(self, preds):
        return {"site_time": "%02d:%02d" % (int(self.site_hour()), int((self.site_hour() % 1) * 60)), "site_hour": round(self.site_hour(), 2),
                "tariff": round(tariff(self.site_hour()) * (getattr(self, "spike_mult", 1.0) if self.t < getattr(self, "spike_until", -1) else 1.0), 3), "carbon": carbon(self.site_hour()), "peak_window": list(PEAK),
                "subsystems": self.subsystem_health(preds),
                "assets": [{k: a[k] for k in ("id", "subsystem", "kind", "name", "serves", "state", "sensors")} | {"failure_mode": PREDICTABLE.get(a["kind"]),
                            "maintenance": a.get("maint")} for a in self.assets.values()],
                "bess": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.bess.items()},
                "energy": {k: round(v, 3) for k, v in self.energy.items()},
                "security": self.security[-12:], "series": self.series[-150:]}
