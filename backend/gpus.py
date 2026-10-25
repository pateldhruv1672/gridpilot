"""Per-GPU telemetry for all 512 GPUs (64 racks x 8), derived from the physics simulator. SIMULATED.

Each workload is pinned to concrete GPU slots inside its rack (deterministic allocation in workload order), so every GPU has an
owner (or is idle), a utilization, a temperature (rack thermal state + load + vertical position in the rack + a fixed
per-device offset), a board power (power limit, load, leakage), memory use and a throttle state.
"""
import hashlib, numpy as np
from collections import deque
import sim as S

_OFF = np.array([((int(hashlib.md5(("gpu%d" % i).encode()).hexdigest()[:6], 16) / 0xFFFFFF) - 0.5) * 1.6 for i in range(512)])

def _mem_frac(wid):
    h = int(hashlib.md5(wid.encode()).hexdigest()[:4], 16) / 0xFFFF
    base = 0.88 if "train" in wid else 0.62 if "infer" in wid else 0.74
    return min(0.97, base + 0.08 * h)

class Fleet:
    def __init__(self):
        self.hist = [deque(maxlen=90) for _ in range(512)]
        self._last = -99
        self.last = None

    def compute(self, sim):
        util = np.zeros(512); owner = [None] * 512; mem = np.full(512, 0.012)
        used = [0] * 64
        for wid, wl in sorted(sim.config["workloads"].items()):
            inten = min(1.0, wl["intensity"] + sim.surge.get(wid, 0.0))
            for rid, frac in sim._effective_rack(wid, wl):
                r, c = S.rack_rc(rid); ri = r * 8 + c
                n = wl["gpus"] if frac >= 0.5 else max(0, int(round(wl["gpus"] * frac)))
                n = wl["gpus"] if frac == 1.0 else n
                for _ in range(n):
                    if used[ri] >= 8: break
                    g = ri * 8 + used[ri]; used[ri] += 1
                    util[g] = inten * (frac if frac < 1 else 1.0); owner[g] = wid; mem[g] = _mem_frac(wid) * (0.35 + 0.65 * frac)
        limitf = np.repeat(sim.limitf, 8)
        busy = np.minimum(util, limitf)
        rack_u = np.repeat(np.maximum(sim.util, 0.05), 8)
        inlet = np.repeat(sim.inlet, 8); gt = np.repeat(sim.gpu_t, 8)
        slot = np.tile(np.arange(8), 64)
        load_share = np.clip(busy / rack_u, 0, 1.6)
        temp = inlet + (gt - inlet) * (0.5 + 0.5 * load_share) + 0.35 * slot - 1.2 + _OFF
        res = np.repeat(getattr(sim, "rack_res", np.ones(64)), 8)
        temp = temp + (res - 1) * 6 * (busy > 0.05)
        leak = 1 + 0.004 * np.maximum(0, temp - 60)
        power = 450 * limitf * (0.13 + 0.87 * busy) * leak
        throttled_thermal = temp > 87.0
        throttled_power = (util > limitf + 1e-6) & (util > 0.05)
        clock = np.where(throttled_thermal, 1410 - (temp - 87) * 60, np.where(throttled_power, 1980 * limitf, 1980))
        self.last = {"util": busy, "temp": temp, "power": power, "mem": mem, "owner": owner, "thr_t": throttled_thermal, "thr_p": throttled_power, "clock": clock}
        if sim.t - self._last >= 2:
            self._last = sim.t
            for g in range(512): self.hist[g].append((round(sim.t, 1), round(float(busy[g]), 3), round(float(temp[g]), 1), round(float(power[g]), 0)))
        return self.last

    def payload(self):
        d = self.last
        if d is None: return None
        return {"util": [round(float(x), 3) for x in d["util"]], "temp": [round(float(x), 1) for x in d["temp"]], "power": [int(x) for x in d["power"]],
                "mem": [round(float(x), 3) for x in d["mem"]], "owner": d["owner"], "throttle": [("thermal" if a else "power" if b else None) for a, b in zip(d["thr_t"], d["thr_p"])],
                "stats": self.stats()}

    def stats(self):
        d = self.last; u = d["util"]; alloc = np.array([o is not None for o in d["owner"]])
        hot = np.argsort(-d["temp"])[:8]
        hist, _ = np.histogram(u[alloc], bins=10, range=(0, 1)) if alloc.any() else (np.zeros(10), None)
        by_wl = {}
        for g, o in enumerate(d["owner"]):
            if o: by_wl.setdefault(o, []).append(g)
        return {"allocated": int(alloc.sum()), "idle": int(512 - alloc.sum()), "mean_util_alloc": round(float(u[alloc].mean()), 3) if alloc.any() else 0,
                "fleet_util": round(float(u.mean()), 3), "throttled_thermal": int(d["thr_t"].sum()), "throttled_power": int(d["thr_p"].sum()),
                "max_temp": round(float(d["temp"].max()), 1), "board_power_kw": round(float(d["power"].sum()) / 1000, 1),
                "hottest": [{"gpu": gid(int(g)), "temp": round(float(d["temp"][g]), 1), "util": round(float(u[g]), 2), "workload": d["owner"][g]} for g in hot],
                "util_hist": [int(x) for x in hist],
                "workloads": {w: {"gpus": len(gs), "racks": sorted({gid(g).split("-")[0] for g in gs}), "mean_util": round(float(u[gs].mean()), 3), "max_temp": round(float(d["temp"][gs].max()), 1)} for w, gs in by_wl.items()},
                "stranded_idle_on_hot_racks": int(sum(1 for g in range(512) if d["owner"][g] is None and d["temp"][g] > 78))}

    def rack(self, rid):
        r, c = S.rack_rc(rid); ri = r * 8 + c; d = self.last
        return {"rack": rid, "gpus": [{"gpu": gid(ri * 8 + k), "slot": k, "workload": d["owner"][ri * 8 + k], "util": round(float(d["util"][ri * 8 + k]), 3), "temp": round(float(d["temp"][ri * 8 + k]), 1),
                                       "power_w": int(d["power"][ri * 8 + k]), "mem": round(float(d["mem"][ri * 8 + k]), 3), "clock_mhz": int(d["clock"][ri * 8 + k]),
                                       "throttle": "thermal" if d["thr_t"][ri * 8 + k] else "power" if d["thr_p"][ri * 8 + k] else None,
                                       "history": list(self.hist[ri * 8 + k])[-60:]} for k in range(8)]}

def gid(g): return "%s-G%d" % (S.rack_id((g // 8) // 8, (g // 8) % 8), g % 8)
