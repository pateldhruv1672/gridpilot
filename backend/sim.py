
"""GRIDPILOT simulator: 64 racks / 512 GPUs, 4 CRAH units, simplified 2D airflow + advection-diffusion thermal field.
Approximate thermal/airflow model for demonstration, NOT engineering-grade CFD. Deterministic (seed 42)."""
import copy, math
import numpy as np

H = 0.3                         # cell size (m)
LX, LZ = 9.6, 20.4
NX, NZ = int(round(LX / H)), int(round(LZ / H))
X0, PITCH, DEPTH, AISLE = 1.2, 0.7, 1.2, 1.2
RACK_X1 = X0 + 8 * PITCH        # 6.8
CRAH_X = 8.4
HEIGHT = 2.0                    # effective aisle height for 2D flux
RHO_CP = 1.206                  # kJ/(m3 K)
T_CHW = 12.0                    # chilled water
SUPPLY_SET = 20.0
UNIT_FLOW = 10.0                # m3/s per CRAH at 100% fan
W = np.array([[0.9, 0.1, 0, 0], [0.1, 0.8, 0.1, 0], [0, 0.1, 0.8, 0.1], [0, 0, 0.1, 0.9]])
THRESH = {"gpu_warning_c": 82.0, "gpu_critical_c": 88.0, "gpu_recovered_c": 80.0, "inlet_warning_c": 27.0,
          "rack_power_alert_kw": 12.5, "hw_throttle_c": 90.0}
ZONES = {"SFO-01A": range(0, 4), "SFO-01B": range(4, 8)}

def row_z0(r): return (1.2 + (r // 2) * 4.8) if r % 2 == 0 else (3.6 + (r // 2) * 4.8)
def cold_aisle_of_row(r): return r // 2          # aisle k sits between rows 2k and 2k+1
def rack_id(r, c): return "R%02d" % (r * 8 + c + 1)
def rack_rc(rid): n = int(rid[1:]) - 1; return n // 8, n % 8
def zone_of_row(r): return "SFO-01A" if r < 4 else "SFO-01B"

xc = (np.arange(NX) + 0.5) * H
zc = (np.arange(NZ) + 0.5) * H
XX, ZZ = np.meshgrid(xc, zc)    # shape (NZ, NX)

# ---- static masks
KIND = np.full((NZ, NX), "", dtype=object)   # 'cold','hot','rack','end','crah'
AISLE_ID = np.full((NZ, NX), -1)             # cold aisle id or hot aisle id
RACK_IDX = np.full((NZ, NX), -1)
COLD_AISLES = [(2.4 + 4.8 * k, 3.6 + 4.8 * k) for k in range(4)]
HOT_AISLES = [(0.0, 1.2)] + [(4.8 + 4.8 * k, 6.0 + 4.8 * k) for k in range(3)] + [(19.2, 20.4)]
for j in range(NZ):
    z = zc[j]
    for i in range(NX):
        x = xc[i]
        kind = None
        for k, (a, b) in enumerate(COLD_AISLES):
            if a <= z < b: kind = "cold"; AISLE_ID[j, i] = k
        for k, (a, b) in enumerate(HOT_AISLES):
            if a <= z < b: kind = "hot"; AISLE_ID[j, i] = k
        if kind is None:
            r = None
            for rr in range(8):
                if row_z0(rr) <= z < row_z0(rr) + DEPTH: r = rr
            if X0 <= x < RACK_X1:
                c = int((x - X0) // PITCH); RACK_IDX[j, i] = r * 8 + c; kind = "rack"
            elif x < X0: kind = "end"; AISLE_ID[j, i] = r
            else: kind = "crah"; AISLE_ID[j, i] = r
        KIND[j, i] = kind
M_COLD, M_HOT, M_RACK, M_END, M_CRAH = [(KIND == k) for k in ("cold", "hot", "rack", "end", "crah")]

def _face_cells(r, c):
    """(inlet cells, exhaust cells) index lists for rack r,c."""
    z0 = row_z0(r); xs = [i for i in range(NX) if X0 + c * PITCH <= xc[i] < X0 + (c + 1) * PITCH]
    jin = int(round((z0 + DEPTH) / H)) if r % 2 == 0 else int(round(z0 / H)) - 1
    jout = int(round(z0 / H)) - 1 if r % 2 == 0 else int(round((z0 + DEPTH) / H))
    return [(jin, i) for i in xs], [(jout, i) for i in xs]
FACES = {(r, c): _face_cells(r, c) for r in range(8) for c in range(8)}

CUSTOMERS = {
    "Helix AI": {"sla_p95_ms": 120, "protected": True, "price_gpu_hr": 4.10, "penalty_per_breach_min": 38.0},
    "Northwind Labs": {"sla_p95_ms": 180, "protected": False, "price_gpu_hr": 3.60, "penalty_per_breach_min": 9.0},
    "Quanta Bio": {"sla_p95_ms": 900, "protected": False, "price_gpu_hr": 3.20, "penalty_per_breach_min": 2.0},
    "Orbital Vision": {"sla_p95_ms": 150, "protected": True, "price_gpu_hr": 4.25, "penalty_per_breach_min": 31.0},
    "Lumen Robotics": {"sla_p95_ms": 220, "protected": False, "price_gpu_hr": 3.40, "penalty_per_breach_min": 6.0},
}
RESERVE_RACKS = ["R14", "R22", "R38", "R46"]       # leased burst capacity (Atlas Capacity Exchange)

def initial_config():
    """Controllable configuration (the config store's source content). Deterministic."""
    rng = np.random.RandomState(42)
    racks = {}
    for r in range(8):
        for c in range(8):
            rid = rack_id(r, c)
            racks[rid] = {"gpu_count": 8, "gpu_power_limit_w": 450, "zone": zone_of_row(r),
                          "reserve_pool": rid in RESERVE_RACKS, "status": "in_service"}
    custs = list(CUSTOMERS)
    workloads = {}
    n = 0
    for r in range(8):
        for c in range(8):
            rid = rack_id(r, c)
            if rid in RESERVE_RACKS:
                left = 2         # mostly idle reserve
            else:
                left = 8
            while left > 0:
                g = int(min(left, [2, 4, 2, 4, 1][rng.randint(5)]))
                cust = custs[rng.randint(len(custs))]
                kind = "inference" if CUSTOMERS[cust]["sla_p95_ms"] < 500 else "batch"
                n += 1
                workloads["%s_%02d" % ("inference_api" if kind == "inference" else "batch_job", n)] = {
                    "customer": cust, "kind": kind, "gpus": g, "rack": rid,
                    "intensity": round(float(0.55 + 0.3 * rng.rand()), 3), "base_p95_ms": round(float(55 + 40 * rng.rand()), 1),
                    "state_gb": int(20 + 60 * rng.rand())}
                left -= g
    # R17 has three named workloads (scenario anchor); replace whatever landed there
    for w in [k for k, v in workloads.items() if v["rack"] == "R17"]: del workloads[w]
    workloads["inference_api_07"] = {"customer": "Helix AI", "kind": "inference", "gpus": 4, "rack": "R17", "intensity": 0.84, "base_p95_ms": 74.0, "state_gb": 48}
    workloads["inference_api_12"] = {"customer": "Northwind Labs", "kind": "inference", "gpus": 2, "rack": "R17", "intensity": 0.80, "base_p95_ms": 82.0, "state_gb": 36}
    workloads["batch_embed_03"] = {"customer": "Quanta Bio", "kind": "batch", "gpus": 2, "rack": "R17", "intensity": 0.80, "base_p95_ms": 410.0, "state_gb": 64}
    cooling = {"C%02d" % (k + 1): {"fan_pct": 100, "serves_cold_aisle": k} for k in range(4)}
    return {"racks": racks, "workloads": dict(sorted(workloads.items())), "cooling": cooling}


class Sim:
    def __init__(self, config=None):
        self.t = 0.0
        self.config = copy.deepcopy(config or initial_config())
        self.T = np.full((NZ, NX), 24.0)
        self.u = np.zeros((NZ, NX)); self.w = np.zeros((NZ, NX))
        self.unit_eff = np.ones(4)          # coil effectiveness (physical health, not config)
        self.unit_flowf = np.ones(4)        # mechanical flow factor (physical health)
        self.unit_target = {"eff": np.ones(4), "flowf": np.ones(4)}
        self.surge = {}                     # workload -> extra intensity (scenario traffic)
        self.gpu_t = np.full(64, 70.0)
        self.rack_res = np.ones(64)          # thermal resistance multiplier (1 = healthy; >1 = fan-module fault)
        self.migrations = []                # in-flight {workload, from, to, t0, dur}
        self.energy_kwh = 0.0; self.it_energy_kwh = 0.0
        self.reserve_gpu_seconds = {}        # rack -> GPU-seconds of leased burst capacity consumed by migrated workloads
        self.rack = {}
        self._derive()
        for _ in range(400): self.step(0.5)   # settle to steady state
        self.t = 0.0; self.energy_kwh = 0.0; self.it_energy_kwh = 0.0; self.reserve_gpu_seconds = {}

    # ---------------- configuration
    def load_config(self, cfg):
        old = self.config["workloads"]
        for wid, wl in cfg["workloads"].items():
            if wid in old and old[wid]["rack"] != wl["rack"]:
                self.migrations.append({"workload": wid, "from": old[wid]["rack"], "to": wl["rack"], "t0": self.t, "dur": 40.0})
        self.config = copy.deepcopy(cfg)
        self._derive()

    def _effective_rack(self, wid, wl):
        """During a migration, load shifts linearly from source to target."""
        for m in self.migrations:
            if m["workload"] == wid:
                f = min(1.0, (self.t - m["t0"]) / m["dur"])
                return [(m["from"], 1 - f), (m["to"], f)]
        return [(wl["rack"], 1.0)]

    def _derive(self):
        util = np.zeros(64)
        self.placement = {}
        for wid, wl in self.config["workloads"].items():
            inten = min(1.0, wl["intensity"] + self.surge.get(wid, 0.0))
            for rid, frac in self._effective_rack(wid, wl):
                r, c = rack_rc(rid)
                util[r * 8 + c] += frac * wl["gpus"] * inten / 8.0
        self.util = np.clip(util, 0, 1)
        self.limitf = np.array([self.config["racks"][rack_id(i // 8, i % 8)]["gpu_power_limit_w"] / 450.0 for i in range(64)])
        self.fan = np.array([self.config["cooling"]["C%02d" % (k + 1)]["fan_pct"] / 100.0 for k in range(4)])

    # ---------------- scenario hooks (physical, not config)
    def degrade_unit(self, k, eff, flowf):
        self.unit_target["eff"][k] = eff; self.unit_target["flowf"][k] = flowf

    # ---------------- physics
    def _rack_power_flow(self):
        # throughput (and thus power) is capped by the power limit; leakage grows with temperature
        busy = np.minimum(self.util, self.limitf)
        hw = np.clip(1 - 0.06 * np.maximum(0, self.gpu_t - THRESH["hw_throttle_c"]), 0.5, 1)
        P = 1.6 + 9.5 * busy * hw + 0.02 * np.maximum(0, self.gpu_t - 70)
        Q = (0.35 + 0.25 * self.util) * (1 + 0.012 * np.maximum(0, self.gpu_t - 75))
        return P, Q, hw

    def _velocity(self, Q):
        u = np.zeros((NZ, NX)); w = np.zeros((NZ, NX))
        S = W @ (UNIT_FLOW * self.fan * self.unit_flowf)       # supply per cold aisle
        D = np.zeros(4)
        for r in range(8):
            D[cold_aisle_of_row(r)] += Q[r * 8:(r + 1) * 8].sum()
        self.deficit = np.clip((D - S) / np.maximum(D, 1e-6), 0, 1)
        self.supply, self.demand = S, D
        face_v = Q / (PITCH * HEIGHT)
        # cold aisles: flow along -x from CRAH, bleeding into racks
        for k, (a, b) in enumerate(COLD_AISLES):
            m = (AISLE_ID == k) & M_COLD
            frac = np.clip((XX - X0) / (RACK_X1 - X0), 0, 1)
            u[m] = -(S[k] * frac[m]) / (AISLE * HEIGHT)
            mid = (a + b) / 2
            col = np.clip(((XX - X0) // PITCH).astype(int), 0, 7)
            rlo, rhi = 2 * k, 2 * k + 1
            vlo = face_v[rlo * 8 + col]; vhi = face_v[rhi * 8 + col]
            inside = (XX >= X0) & (XX < RACK_X1)
            w[m & (ZZ < mid) & inside] = -(vlo * (mid - ZZ) / (AISLE / 2))[m & (ZZ < mid) & inside]
            w[m & (ZZ >= mid) & inside] = (vhi * (ZZ - mid) / (AISLE / 2))[m & (ZZ >= mid) & inside]
            # row-end recirculation: weak supply lets hot air wrap into the aisle end (x < X0)
            endm = m & (XX < X0 + 2 * PITCH)
            u[endm] += (0.25 + 1.6 * self.deficit[k]) * np.exp(-(XX[endm] - 0.3) / 1.2)
        # racks: through-flow cold face -> hot face
        for r in range(8):
            for c in range(8):
                m = RACK_IDX == r * 8 + c
                w[m] = -face_v[r * 8 + c] if r % 2 == 0 else face_v[r * 8 + c]
        # hot aisles: return along +x to CRAH intakes
        for k, (a, b) in enumerate(HOT_AISLES):
            m = (AISLE_ID == k) & M_HOT
            rows = [r for r in range(8) if (r % 2 == 0 and abs(row_z0(r) - b) < 1e-6) or (r % 2 == 1 and abs(row_z0(r) + DEPTH - a) < 1e-6)]
            Hx = np.zeros_like(XX)
            for r in rows:
                cum = np.concatenate([[0], np.cumsum(Q[r * 8:(r + 1) * 8])])
                colf = np.clip((XX - X0) / PITCH, 0, 8)
                Hx += np.interp(colf, np.arange(9), cum)
            u[m] = Hx[m] / (AISLE * HEIGHT)
            mid = (a + b) / 2
            w[m] = (-np.sign(ZZ - mid) * 0.35 * (np.abs(ZZ - mid) / (AISLE / 2)))[m]
        # row ends (x < X0): hot aisle air drawn around the end of the row toward deficient cold aisles
        for j in range(NZ):
            for i in range(NX):
                if M_END[j, i]:
                    r = AISLE_ID[j, i]; k = cold_aisle_of_row(r)
                    mid = (COLD_AISLES[k][0] + COLD_AISLES[k][1]) / 2
                    w[j, i] = np.sign(mid - zc[j]) * (0.12 + 1.4 * self.deficit[k])
        return u, w

    def _sample(self, F, x, z):
        fx = np.clip(x / H - 0.5, 0, NX - 1.001); fz = np.clip(z / H - 0.5, 0, NZ - 1.001)
        i0 = fx.astype(int); j0 = fz.astype(int); tx = fx - i0; tz = fz - j0
        return ((F[j0, i0] * (1 - tx) + F[j0, i0 + 1] * tx) * (1 - tz) + (F[j0 + 1, i0] * (1 - tx) + F[j0 + 1, i0 + 1] * tx) * tz)

    def step(self, dt_real, speed=1.0, sub=0.25):
        total = dt_real * speed
        n = max(1, int(round(total / sub)))
        dt = total / n
        for _ in range(n):
            self._substep(dt)

    def _substep(self, dt):
        self.t += dt
        # physical cooling health relaxes toward its target (degradation / repair is gradual)
        a = 1 - math.exp(-dt / 12.0)
        self.unit_eff += (self.unit_target["eff"] - self.unit_eff) * a
        self.unit_flowf += (self.unit_target["flowf"] - self.unit_flowf) * a
        self.migrations = [m for m in self.migrations if self.t - m["t0"] < m["dur"]]
        self._derive()
        P, Q, hw = self._rack_power_flow()
        self.u, self.w = self._velocity(Q)
        T = self._sample(self.T, XX - self.u * dt, ZZ - self.w * dt)
        # heating inside racks
        for idx in range(64):
            m = RACK_IDX == idx
            dT = P[idx] / (RHO_CP * Q[idx]); transit = DEPTH / (Q[idx] / (PITCH * HEIGHT))
            T[m] += dT / transit * dt
        # CRAH coils: supply temperature from return temperature and coil effectiveness
        ret = np.zeros(4); sup = np.zeros(4)
        for k in range(4):
            ha = [k, k + 1]
            cells = np.zeros_like(M_HOT)
            for h in ha: cells |= (AISLE_ID == h) & M_HOT & (XX > RACK_X1)
            ret[k] = T[cells].mean()
            eps = 0.9 * self.unit_eff[k]
            sup[k] = max(SUPPLY_SET, ret[k] - eps * (ret[k] - T_CHW))
        flows = UNIT_FLOW * self.fan * self.unit_flowf
        aisle_T = (W @ (flows * sup)) / np.maximum(W @ flows, 1e-6)
        for k in range(4):
            m = (AISLE_ID == k) & M_COLD & (XX >= CRAH_X)
            T[m] = aisle_T[k]
        T[M_CRAH] = 0.5 * T[M_CRAH] + 0.5 * np.mean(sup)
        # overhead recirculation (3D effect folded into 2D): deficient aisles mix exhaust into rack inlets, worst at row ends
        for r in range(8):
            k = cold_aisle_of_row(r)
            if self.deficit[k] <= 0: continue
            for c in range(8):
                fin, fout = FACES[(r, c)]
                idx = r * 8 + c
                texh = self.inlet[idx] + P[idx] / (RHO_CP * Q[idx]) if hasattr(self, "inlet") else np.mean([T[j, i] for j, i in fout])
                g = math.exp(-c / 1.5)
                for j, i in fin:
                    T[j, i] += 1.6 * self.deficit[k] * g * (texh - T[j, i]) * dt
        # turbulent diffusion
        lap = (np.roll(T, 1, 0) + np.roll(T, -1, 0) + np.roll(T, 1, 1) + np.roll(T, -1, 1) - 4 * T)
        lap[0, :] = lap[-1, :] = 0; lap[:, 0] = lap[:, -1] = 0
        T += 0.03 * dt / (H * H) * lap
        self.T = T
        # rack-level metrics
        inlet = np.zeros(64); exh = np.zeros(64)
        for (r, c), (fin, fout) in FACES.items():
            idx = r * 8 + c
            inlet[idx] = np.mean([T[j, i] for j, i in fin])
            exh[idx] = inlet[idx] + P[idx] / (RHO_CP * Q[idx])   # rack exhaust air = inlet + heat pickup
        target = inlet + 5.15 * P * getattr(self, 'rack_res', np.ones(64))
        self.gpu_t += (target - self.gpu_t) * (1 - math.exp(-dt / 12.0))
        self.P, self.Q, self.hw, self.inlet, self.exh = P, Q, hw, inlet, exh
        self.ret_T, self.sup_T = ret, sup
        # energy
        it_kw = P.sum()
        cool_kw = float(np.sum(18.0 * (self.fan * self.unit_flowf) ** 3)) + 0.22 * it_kw / max(0.4, float(np.mean(self.unit_eff)))
        self.it_kw, self.cool_kw = it_kw, cool_kw
        self.it_energy_kwh += it_kw * dt / 3600; self.energy_kwh += (it_kw + cool_kw + 0.04 * it_kw) * dt / 3600
        for wid, wl in self.config["workloads"].items():
            if self.config["racks"][wl["rack"]]["reserve_pool"] and wid in self.migrated_onto_reserve():
                self.reserve_gpu_seconds[wid] = self.reserve_gpu_seconds.get(wid, 0) + wl["gpus"] * dt

    def migrated_onto_reserve(self):
        return self.config.get("_migrated", set()) if isinstance(self.config.get("_migrated"), set) else set(self.config.get("_migrated", []))

    # ---------------- derived views
    def pue(self): return (self.it_kw + self.cool_kw + 0.04 * self.it_kw) / max(self.it_kw, 1e-6)

    def workload_latency(self):
        out = {}
        for wid, wl in self.config["workloads"].items():
            r, c = rack_rc(wl["rack"]); idx = r * 8 + c
            cust = CUSTOMERS[wl["customer"]]
            cap = self.limitf[idx]; demand = min(1.0, wl["intensity"] + self.surge.get(wid, 0))
            load = demand / max(0.05, min(cap, 1.0) * self.hw[idx])
            p95 = wl["base_p95_ms"] * (1 + 1.8 * max(0, load - 0.85) ** 1.2 * 3) * (1 / self.hw[idx])
            out[wid] = {"p95_ms": round(float(p95), 1), "limit_ms": cust["sla_p95_ms"], "breach": bool(p95 > cust["sla_p95_ms"]), "protected": cust["protected"]}
        return out

    def rack_status(self, idx):
        g = self.gpu_t[idx]
        if g > THRESH["gpu_critical_c"]: return "critical"
        if g > THRESH["gpu_warning_c"] or self.inlet[idx] > THRESH["inlet_warning_c"]: return "elevated"
        return "normal"

    def snapshot(self, field=True):
        lat = self.workload_latency()
        racks = []
        for idx in range(64):
            r, c = idx // 8, idx % 8; rid = rack_id(r, c)
            wls = [w for w, v in self.config["workloads"].items() if v["rack"] == rid]
            racks.append({"rack_id": rid, "row": r, "col": c, "gpu_count": 8, "utilization": round(float(self.util[idx]), 3),
                          "power_kw": round(float(self.P[idx]), 2), "gpu_temp_c": round(float(self.gpu_t[idx]), 1),
                          "inlet_temp_c": round(float(self.inlet[idx]), 1), "exhaust_temp_c": round(float(self.exh[idx]), 1),
                          "airflow_m3s": round(float(self.Q[idx]), 3), "cooling_efficiency": round(float(self.unit_eff[cold_aisle_of_row(r)]), 3),
                          "power_limit_w": self.config["racks"][rid]["gpu_power_limit_w"], "zone": zone_of_row(r),
                          "reserve_pool": rid in RESERVE_RACKS, "workloads": wls, "status": self.rack_status(idx)})
        units = [{"unit_id": "C%02d" % (k + 1), "fan_pct": int(self.fan[k] * 100), "effectiveness": round(float(self.unit_eff[k]), 3),
                  "flow_factor": round(float(self.unit_flowf[k]), 3), "supply_temp_c": round(float(self.sup_T[k]), 1),
                  "return_temp_c": round(float(self.ret_T[k]), 1), "aisle_supply_m3s": round(float(self.supply[k]), 2),
                  "aisle_demand_m3s": round(float(self.demand[k]), 2), "aisle_deficit": round(float(self.deficit[k]), 3)} for k in range(4)]
        snap = {"t": round(self.t, 1), "racks": racks, "cooling": units,
                "totals": {"it_kw": round(float(self.it_kw), 1), "facility_kw": round(float(self.it_kw + self.cool_kw + 0.04 * self.it_kw), 1),
                           "pue": round(float(self.pue()), 3), "gpu_util": round(float(self.util.mean()), 3),
                           "energy_kwh": round(self.energy_kwh, 3), "it_energy_kwh": round(self.it_energy_kwh, 3)},
                "latency": lat,
                "migrations": [{"workload": m["workload"], "from": m["from"], "to": m["to"], "progress": round(min(1, (self.t - m["t0"]) / m["dur"]), 3)} for m in self.migrations]}
        if field:
            snap["field"] = {"nx": NX, "nz": NZ, "h": H,
                             "T": np.round(self.T, 1).ravel().tolist(),
                             "u": np.round(self.u, 2).ravel().tolist(), "w": np.round(self.w, 2).ravel().tolist()}
        return snap

    def clone(self):
        return copy.deepcopy(self)
