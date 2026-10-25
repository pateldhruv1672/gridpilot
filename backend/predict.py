"""Predictive maintenance: remaining useful life (RUL) from noisy sensors only (never the twin's hidden wear).

Per asset kind a health index HI in [0, 1] (1 = functional failure threshold) is computed from one physical signal:
  CRAH fan bearing: vibration velocity (ISO 10816 style)       square-law wear  -> fit sqrt(HI) linearly in time
  UPS string:       internal resistance                          square-law aging -> sqrt(HI)
  BESS:             cell temperature spread (load-compensated)   square-law       -> sqrt(HI)
  Chiller:          condenser approach temperature               linear fouling   -> HI
  PDU:              phase imbalance                              linear           -> HI
Ordinary least squares over a sliding window gives the trend; the classical prediction interval gives the uncertainty, from
which we get RUL (median, 90% band) and P(failure within 5 / 15 sim minutes). Accuracy is scored against the twin's ground
truth only for reporting (proof), never fed back into the prediction.
"""
import math
from collections import deque

SIG = {
    "crah": ("vibration_mm_s", lambda s: (s["vibration_mm_s"] - 1.6) / 7.4, "sqrt", "vibration", "mm/s", 7.1),
    "ups": ("internal_resistance_mohm", lambda s: (s["internal_resistance_mohm"] - 2.1) / 3.4, "sqrt", "internal resistance", "mΩ", 5.5),
    "bess": ("cell_dT_c", lambda s: (s["cell_dT_c"] - 1.2 - abs(s["power_kw"]) / 250 * 0.8) / 7.0, "sqrt", "cell ΔT", "°C", 8.2),
    "chiller": ("condenser_approach_c", lambda s: (s["condenser_approach_c"] - 2.2) / 6.5, "lin", "condenser approach", "°C", 8.7),
    "pdu": ("phase_imbalance_pct", lambda s: (s["phase_imbalance_pct"] - 2.0) / 3.0, "lin", "phase imbalance", "%", 5.0),
}
WINDOW = 90       # samples (2 s apart = 3 sim minutes)
MIN_N = 40

def phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

class Predictor:
    def __init__(self):
        self.hist = {}            # asset -> deque[(t, hi, raw)]
        self.preds = {}
        self.episodes = {}        # asset -> open episode
        self.closed = []          # finished episodes (proof)
        self.errors = deque(maxlen=400)   # (asset, t, predicted_rul, true_rul)
        self._last = -99

    def observe(self, twin):
        if twin.t - self._last < 2: return []
        self._last = twin.t; alerts = []
        for a in twin.assets.values():
            spec = SIG.get(a["kind"])
            if not spec or not a["sensors"] or a["state"] == "maintenance":
                if a["state"] == "maintenance": self.hist.pop(a["id"], None)
                continue
            try: hi = spec[1](a["sensors"])
            except Exception: continue
            h = self.hist.setdefault(a["id"], deque(maxlen=300)); h.append((twin.t, hi, a["sensors"][spec[0]]))
            p = self.fit(a, h, spec, twin.t)
            prev = self.preds.get(a["id"], {}).get("risk", "nominal")
            self.preds[a["id"]] = p
            if p["risk"] in ("warning", "critical") and prev not in ("warning", "critical"): alerts.append((a["id"], p))
            ep = self.episodes.get(a["id"])
            if p["risk"] in ("watch", "warning", "critical") and not ep:
                ep = self.episodes[a["id"]] = {"asset": a["id"], "first_watch_t": twin.t, "first_warning_t": None, "rul_at_warning": None, "true_rul_at_warning": None}
            if ep and p["risk"] in ("warning", "critical") and ep["first_warning_t"] is None:
                ep["first_warning_t"] = twin.t; ep["rul_at_warning"] = p["rul_s"]; ep["true_rul_at_warning"] = round(a["true_fail_t"] - twin.t, 1) if a["true_fail_t"] else None
            if p["rul_s"] is not None and a["true_fail_t"] and p["risk"] != "nominal":
                self.errors.append((a["id"], round(twin.t, 1), p["rul_s"], round(a["true_fail_t"] - twin.t, 1)))
        return alerts

    def fit(self, a, h, spec, t_now):
        key, f, mode, label, unit, alarm = spec
        pts = list(h)[-WINDOW:]
        base = {"asset": a["id"], "signal": label, "unit": unit, "alarm": alarm, "value": pts[-1][2], "hi": round(max(0, pts[-1][1]), 3), "n": len(pts),
                "model": ("square-law wear" if mode == "sqrt" else "linear drift") + ", OLS over %d samples" % len(pts), "failure_mode": None}
        if a["state"] == "failed": return {**base, "risk": "failed", "rul_s": 0, "rul_lo_s": 0, "rul_hi_s": 0, "p_fail_5m": 1.0, "p_fail_15m": 1.0, "slope": None}
        if len(pts) < MIN_N: return {**base, "risk": "learning", "rul_s": None, "rul_lo_s": None, "rul_hi_s": None, "p_fail_5m": None, "p_fail_15m": None, "slope": None}
        tr = (lambda v: math.sqrt(max(v, 0.0))) if mode == "sqrt" else (lambda v: v)
        xs = [p[0] - t_now for p in pts]; ys = [tr(p[1]) for p in pts]; n = len(xs)
        mx = sum(xs) / n; my = sum(ys) / n; sxx = sum((x - mx) ** 2 for x in xs) or 1e-9
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx; a0 = my - b * mx
        res = [y - (a0 + b * x) for x, y in zip(xs, ys)]; s = math.sqrt(sum(r * r for r in res) / max(1, n - 2)) or 1e-6
        se_b = s / math.sqrt(sxx)
        def pfail(H):
            mean = a0 + b * H; sd = s * math.sqrt(1 + 1 / n + (H - mx) ** 2 / sxx)
            return round(1 - phi((1 - mean) / sd), 3)
        cur = a0
        def rul(slope):
            if slope <= 1e-9: return None
            return max(0.0, (1 - cur) / slope)
        r = rul(b); lo = rul(b + 1.645 * se_b); hi = rul(b - 1.645 * se_b) if b - 1.645 * se_b > 1e-9 else None
        p5, p15 = pfail(300), pfail(900)
        significant = b > 2.5 * se_b and pts[-1][1] > 0.06      # trend must be statistically real and the signal off its floor
        if not significant: r = lo = hi = None; p5 = min(p5, 0.05); p15 = min(p15, 0.08)
        risk = "critical" if p5 > 0.5 or (r is not None and r < 300) else "warning" if p15 > 0.5 or (r is not None and r < 900) else "watch" if p15 > 0.1 or (r is not None and r < 2400) else "nominal"
        proj = []
        if r is not None and r < 3600:
            for k in range(0, 13):
                H = (r * 1.15) * k / 12; mean = a0 + b * H; sd = s * math.sqrt(1 + 1 / n + (H - mx) ** 2 / sxx)
                back = (lambda y: max(0, y) ** 2) if mode == "sqrt" else (lambda y: y)
                proj.append([round(t_now + H, 1), round(back(mean), 3), round(back(mean - 1.645 * sd), 3), round(back(mean + 1.645 * sd), 3)])
        return {**base, "risk": risk, "rul_s": None if r is None else round(r, 1), "rul_lo_s": None if lo is None else round(lo, 1), "rul_hi_s": None if hi is None else round(hi, 1),
                "p_fail_5m": p5, "p_fail_15m": p15, "slope": round(b, 6), "resid_sd": round(s, 4), "projection": proj}

    def history(self, aid, n=150):
        return [[round(t, 1), round(max(0, hi), 3), raw] for t, hi, raw in list(self.hist.get(aid, []))[-n:]]

    def close_episode(self, aid, outcome, t, detail=None):
        ep = self.episodes.pop(aid, None)
        if not ep: return None
        ep.update({"closed_t": round(t, 1), "outcome": outcome, **(detail or {})})
        if ep["first_warning_t"] is not None: ep["lead_time_s"] = round((ep.get("failure_t") or t) - ep["first_warning_t"], 1)
        self.closed.append(ep); self.closed = self.closed[-50:]
        return ep

    def proof(self):
        errs = list(self.errors)
        mae = round(sum(abs(p - t) for _, _, p, t in errs) / len(errs), 1) if errs else None
        rel = [abs(p - t) / max(t, 30) for _, _, p, t in errs]
        within = round(100 * sum(1 for x in rel if x < 0.25) / len(rel), 1) if rel else None
        leads = [e["lead_time_s"] for e in self.closed if e.get("lead_time_s") is not None]
        return {"predictions_scored": len(errs), "rul_mae_s": mae, "within_25pct": within, "episodes": self.closed[-10:],
                "failures_predicted": sum(1 for e in self.closed if e["first_warning_t"] is not None), "prevented": sum(1 for e in self.closed if e["outcome"] == "prevented"),
                "unpredicted_failures": sum(1 for e in self.closed if e["outcome"] == "failed" and e["first_warning_t"] is None),
                "mean_lead_time_s": round(sum(leads) / len(leads), 1) if leads else None,
                "note": "Scored against the simulator's hidden ground-truth wear, which the predictor never sees."}
