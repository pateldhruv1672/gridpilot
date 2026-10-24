
"""Deterministic financial model. All arithmetic lives here. Distinguishes ACTUAL (metered), FORECAST and ASSUMPTION."""
ELECTRICITY_USD_KWH = 0.118
RESERVE_LEASE_USD_GPU_HR = 3.85      # Atlas Capacity Exchange on-demand rate card
RESERVE_MIN_BLOCK_HR = 6.0           # rate card: leased reserve GPUs billed in 6-hour minimum blocks
INTER_ZONE_TRANSFER_USD_GB = 0.25    # rate card: SFO-01A <-> SFO-01B data transfer
ASSUMED_INCIDENT_HOURS = 2.0         # ASSUMPTION: C02 repair window used to extrapolate unresolved exposure
MONTHLY_BUDGET_USD = 186000.0

ASSUMPTIONS = [
    "Electricity $%.3f/kWh flat tariff (simulated)." % ELECTRICITY_USD_KWH,
    "Reserve GPU lease $%.2f/GPU-hr, %.0f h minimum block (synthetic rate card)." % (RESERVE_LEASE_USD_GPU_HR, RESERVE_MIN_BLOCK_HR),
    "Inter-zone transfer $%.2f/GB of workload state (synthetic rate card)." % INTER_ZONE_TRANSFER_USD_GB,
    "Unresolved breach is extrapolated for %.1f h (assumed C02 repair window)." % ASSUMED_INCIDENT_HOURS,
    "SLA exposure is conditional on breach; it is not a realized penalty. Avoided loss is an estimate, never revenue.",
]

def migration_costs(actions, cfg):
    lines = []
    for a in actions:
        if a.get("type") != "migrate_workload": continue
        wl = cfg["workloads"][a["workload"]]; to = a["to_rack"]
        if cfg["racks"][to].get("reserve_pool"):
            lease = wl["gpus"] * RESERVE_MIN_BLOCK_HR * RESERVE_LEASE_USD_GPU_HR
            lines.append({"kind": "reserve_lease", "workload": a["workload"], "rack": to, "gpus": wl["gpus"], "hours": RESERVE_MIN_BLOCK_HR,
                          "usd": round(lease, 2), "basis": "%d GPU x %.0f h x $%.2f" % (wl["gpus"], RESERVE_MIN_BLOCK_HR, RESERVE_LEASE_USD_GPU_HR)})
        if cfg["racks"][wl["rack"]]["zone"] != cfg["racks"][to]["zone"]:
            fee = wl["state_gb"] * INTER_ZONE_TRANSFER_USD_GB
            lines.append({"kind": "inter_zone_transfer", "workload": a["workload"], "gb": wl["state_gb"], "usd": round(fee, 2),
                          "basis": "%d GB x $%.2f" % (wl["state_gb"], INTER_ZONE_TRANSFER_USD_GB)})
    return lines

def evaluate(actions, cfg, forecast, customers):
    lines = migration_costs(actions, cfg)
    incremental = round(sum(l["usd"] for l in lines), 2)
    energy_delta_kwh = forecast["energy_kwh"] - forecast["baseline_energy_kwh"]
    energy_usd = round(energy_delta_kwh * ELECTRICITY_USD_KWH, 2)
    exposure = 0.0; detail = []
    for wid, mins in forecast["breach_minutes"].items():
        if mins <= 0: continue
        cust = customers[cfg["workloads"][wid]["customer"]]
        unresolved = forecast["breach_at_end"].get(wid, False)
        ext = (ASSUMED_INCIDENT_HOURS * 60 - forecast["horizon_min"]) if unresolved else 0
        usd = (mins + max(0, ext)) * cust["penalty_per_breach_min"]
        exposure += usd
        detail.append({"workload": wid, "customer": cfg["workloads"][wid]["customer"], "breach_min_forecast": round(mins, 1),
                       "extrapolated_min": round(max(0, ext), 1), "usd": round(usd, 2)})
    return {"incremental_cost_usd": incremental, "cost_lines": lines, "energy_delta_usd": energy_usd,
            "sla_exposure_usd": round(exposure, 2), "sla_exposure_detail": detail, "labels": {
                "incremental_cost_usd": "FORECAST (rate card x plan)", "energy_delta_usd": "FORECAST (simulated horizon)",
                "sla_exposure_usd": "CONDITIONAL EXPOSURE (not realized)"}}

def invoice_for(incident_id, usage, transfers):
    """Synthetic provider invoice built from metered usage records (deterministic)."""
    lines = []
    for u in usage:
        billed_h = max(u["gpu_hours"] / max(u["gpus"], 1), RESERVE_MIN_BLOCK_HR) * u["gpus"]
        lines.append({"sku": "RESERVE-GPU-H100", "description": "Reserve lease %s on %s" % (u["workload"], u["rack"]), "qty_gpu_hr": round(billed_h, 3),
                      "rate": RESERVE_LEASE_USD_GPU_HR, "usd": round(billed_h * RESERVE_LEASE_USD_GPU_HR, 2), "usage_ref": u["id"]})
    for t in transfers:
        lines.append({"sku": "XZONE-TRANSFER", "description": "Inter-zone transfer %s (%s)" % (t["workload"], t["route"]), "qty_gb": t["gb"],
                      "rate": INTER_ZONE_TRANSFER_USD_GB, "usd": round(t["gb"] * INTER_ZONE_TRANSFER_USD_GB, 2), "usage_ref": t["id"]})
    return {"provider": "Atlas Capacity Exchange", "incident_id": incident_id, "lines": lines, "total_usd": round(sum(l["usd"] for l in lines), 2)}

def reconcile(invoice, usage, transfers):
    """Expected = metered compute x rate card. Any variance must be explained by evidence records."""
    expected_compute = round(sum(max(u["gpu_hours"] / max(u["gpus"], 1), RESERVE_MIN_BLOCK_HR) * u["gpus"] * RESERVE_LEASE_USD_GPU_HR for u in usage), 2)
    variance = round(invoice["total_usd"] - expected_compute, 2)
    findings = []; explained = 0.0
    for l in invoice["lines"]:
        if l["sku"] == "XZONE-TRANSFER":
            rec = next((t for t in transfers if t["id"] == l["usage_ref"]), None)
            if rec:
                recomputed = round(rec["gb"] * INTER_ZONE_TRANSFER_USD_GB, 2)
                ok = abs(recomputed - l["usd"]) < 0.005
                findings.append({"line": l["description"], "usd": l["usd"], "evidence": "transfer record %s: %s GB %s at %s" % (rec["id"], rec["gb"], rec["route"], rec["ts"]),
                                 "recomputed_usd": recomputed, "matches": ok})
                if ok: explained += l["usd"]
    unexplained = round(variance - explained, 2)
    status = "RECONCILED" if abs(unexplained) < 0.01 else "DISCREPANCY"
    return {"expected_compute_usd": expected_compute, "invoice_total_usd": invoice["total_usd"], "variance_usd": variance,
            "explained_usd": round(explained, 2), "unexplained_usd": unexplained, "status": status, "findings": findings}
