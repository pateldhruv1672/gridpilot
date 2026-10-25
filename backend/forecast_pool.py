"""Forecast worker pool. The 600 s look-ahead simulations are CPU-heavy pure Python; run in threads they hold the GIL and starve
the asyncio loop that streams state to dashboards (observed: 2 ticks in 8 s during the compound storm). They run in separate
processes instead. The functions are module-level so they can be pickled; inputs are a cloned Sim plus an already-applied config."""
import asyncio, os, sys, multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import numpy as np

_POOL = None
def pool():
    global _POOL
    if _POOL is None:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path: sys.path.insert(0, here)
        ctx = mp.get_context("forkserver")
        ctx.set_forkserver_preload(["forecast_pool", "sim", "numpy"])   # never import the server's __main__ in workers
        _POOL = ProcessPoolExecutor(max_workers=int(os.environ.get("GRIDPILOT_FORECAST_WORKERS", "3")), mp_context=ctx)
    return _POOL

class _hidden_main:
    """multiprocessing passes __main__'s path to new workers, which would re-import server.py (and its startup side effects)
    in every worker. Workers only need this module, so hide the path while a submit may start a worker."""
    def __enter__(self):
        m = sys.modules.get("__main__"); self.saved = m.__dict__.pop("__file__", None) if m else None; return self
    def __exit__(self, *a):
        if self.saved is not None: sys.modules["__main__"].__dict__["__file__"] = self.saved

async def run(fn, *args):
    try:
        with _hidden_main(): fut = pool().submit(fn, *args)
        return await asyncio.wrap_future(fut)
    except Exception as ex:
        if "pickle" in str(ex).lower() or "BrokenProcessPool" in type(ex).__name__:
            global _POOL; _POOL = None
            return await asyncio.to_thread(fn, *args)          # fallback keeps behaviour identical
        raise

def warm():
    try:
        with _hidden_main(): pool().submit(int, 0)
    except Exception: pass

def engine_forecast(base, cfg, horizon, focus, targets, recovered_c):
    import sim as S
    fr, fc = S.rack_rc(focus); fi = fr * 8 + fc
    c = base.clone()
    if cfg is not None: c.load_config(cfg)
    e0 = c.energy_kwh; series = []; recover = None; breach_s = {}; peak_targets = {}
    below_since = None
    for k in range(int(horizon / 5)):
        c.step(5.0, speed=1, sub=1.0)
        g = float(c.gpu_t[fi]); series.append([round(c.t - base.t), round(g, 1)])
        if g < recovered_c:
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

def reliability_forecast(base, cfg, k, off_at, horizon, off_eff, off_flow):
    import sim as S
    c = base.clone()
    if cfg is not None: c.load_config(cfg)
    e0 = c.energy_kwh; series = []; peak = (0, None); breach = {}; off_done = False
    for step in range(int(horizon / 5)):
        if not off_done and (c.t - base.t) >= off_at: c.degrade_unit(k, off_eff, off_flow); off_done = True
        c.step(5.0, speed=1, sub=1.0)
        i = int(np.argmax(c.gpu_t)); g = float(c.gpu_t[i]); series.append([round(c.t - base.t), round(g, 1)])
        if g > peak[0]: peak = (g, S.rack_id(i // 8, i % 8))
        for wid, v in c.workload_latency().items():
            if v["breach"]: breach[wid] = breach.get(wid, 0) + 5
    lat = c.workload_latency()
    return {"series": series, "peak_gpu_c": round(peak[0], 1), "hottest_rack": peak[1], "energy_kwh": c.energy_kwh - e0,
            "breach_minutes": {w: s / 60 for w, s in breach.items()}, "breach_at_end": {w: v["breach"] for w, v in lat.items()}, "horizon_min": horizon / 60,
            "final_max_gpu_c": round(float(c.gpu_t.max()), 1)}
