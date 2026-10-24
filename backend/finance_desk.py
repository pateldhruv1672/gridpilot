"""Atlas Compute finance desk (Hyphenate track): the autonomous AP analyst + cost controller seats.

Real: double-entry ledger with enforced balance, deterministic three-way matching (contract x metered usage x invoice),
segregation of duties, approval limits, payment runs, budget alerts, audit trail, scoring against an answer key.
Simulated: the vendors, their invoices (with planted errors), the bank feed and the company history.
"""
import json, time, hashlib, secrets, datetime as dt

ACCOUNTS = {
    "1000": ("Cash - Mercury operating", "asset"), "1400": ("Vendor credits receivable", "asset"),
    "2000": ("Accounts payable", "liability"), "2100": ("Accrued liabilities", "liability"),
    "5100": ("COGS - Electricity", "expense"), "5200": ("COGS - Burst GPU capacity", "expense"),
    "5300": ("COGS - Network transfer", "expense"), "5400": ("Facilities maintenance", "expense"),
    "5900": ("Disputed / suspense", "expense"),
}
VENDORS = {
    "ACX": {"name": "Atlas Capacity Exchange", "account": "5200", "remit": "Silicon Valley Bank ••4417", "phone_on_file": "+1 (415) 555-0142", "domain": "atlascapacity.exchange", "terms_days": 30},
    "BPC": {"name": "Bay Power Co-op", "account": "5100", "remit": "Wells Fargo ••9023", "phone_on_file": "+1 (510) 555-0199", "domain": "baypower.coop", "terms_days": 20},
    "NBT": {"name": "Northbeam Transit", "account": "5300", "remit": "Chase ••3381", "phone_on_file": "+1 (650) 555-0111", "domain": "northbeam.net", "terms_days": 30},
    "CTS": {"name": "CoolTech Services", "account": "5400", "remit": "Bank of America ••7710", "phone_on_file": "+1 (408) 555-0177", "domain": "cooltech.services", "terms_days": 30},
}
CONTRACTS = {
    "ACX-MSA-2026": {"vendor": "ACX", "sku": "RESERVE-GPU-H100", "rate": 3.85, "unit": "GPU-hr", "minimum_block_h": 6, "effective": "2026-01-01", "source": "Master Services Agreement §4.2, signed 2025-12-18"},
    "BPC-TARIFF-E20": {"vendor": "BPC", "sku": "ENERGY-KWH", "rate": 0.118, "unit": "kWh", "effective": "2026-01-01", "source": "Tariff E-20 flat industrial rate"},
    "NBT-IZ-2026": {"vendor": "NBT", "sku": "INTERZONE-GB", "rate": 0.25, "unit": "GB", "effective": "2026-01-01", "source": "Inter-zone transfer schedule v3"},
    "CTS-PO-7731": {"vendor": "CTS", "sku": "CRAH-REPAIR", "rate": 4850.00, "unit": "job", "effective": "2026-10-20", "source": "PO-7731 requested by Maya Chen (Facilities), C02 coil and fan module repair"},
}
POLICY = {"materiality_usd": 25.0, "agent_approval_limit_usd": 250.0, "controller_limit_usd": 25000.0,
          "budgets": {"5100": 64000.0, "5200": 1200.0, "5300": 150.0, "5400": 6000.0}}
PEOPLE = {"controller": "Dhruv Patel (Controller)", "facilities": "Maya Chen (Facilities, requester of PO-7731)", "agent": "GRIDPILOT AP analyst (autonomous)"}

def now(): return time.time()
def today(): return dt.date.today().isoformat()

class Desk:
    def __init__(self, db, notify=lambda: None, event=lambda *a, **k: None):
        self.db = db; self.notify = notify; self.event = event
        self.db.c.executescript("""
        create table if not exists gl_entries(id text primary key, date text, memo text, source text, actor text, approved_by text, status text, reverses text, created real, lines text);
        create table if not exists ap_invoices(id text primary key, vendor text, number text, date text, due text, period text, amount real, status text, created real, data text);
        create table if not exists ap_decisions(id integer primary key autoincrement, invoice_id text, ts real, actor text, decision text, amount real, reason text, evidence text, kind text);
        create table if not exists credit_memos(id text primary key, vendor text, amount real, issued text, status text, data text);
        create table if not exists meters(id text primary key, kind text, vendor text, ref text, period text, qty real, data text);
        create table if not exists bank_feed(id text primary key, date text, amount real, counterparty text, memo text, matched text);
        create table if not exists fin_alerts(id text primary key, ts real, kind text, severity text, text text, data text, status text);
        create table if not exists answer_key(invoice_id text primary key, expected text, expected_amount real, planted text);
        """); self.db.c.commit()
        if not self.db.one("select id from ap_invoices limit 1"): self.seed()

    # ------------------------------------------------------------ ledger (double entry, enforced)
    def post(self, date, memo, lines, source, actor, approved_by=None, reverses=None):
        dr = round(sum(l.get("dr", 0) for l in lines), 2); cr = round(sum(l.get("cr", 0) for l in lines), 2)
        if abs(dr - cr) > 0.005: raise ValueError("unbalanced entry: debits %.2f != credits %.2f" % (dr, cr))
        for l in lines:
            if l["acct"] not in ACCOUNTS: raise ValueError("unknown account " + l["acct"])
        jid = "JE-%s" % secrets.token_hex(3).upper()
        self.db.ex("insert into gl_entries values(?,?,?,?,?,?,?,?,?,?)", (jid, date, memo, source, actor, approved_by, "posted", reverses, now(), json.dumps(lines)))
        return jid

    def trial_balance(self):
        bal = {a: 0.0 for a in ACCOUNTS}
        for e in self.db.all("select lines from gl_entries where status='posted'"):
            for l in json.loads(e["lines"]): bal[l["acct"]] += l.get("dr", 0) - l.get("cr", 0)
        rows = [{"acct": a, "name": ACCOUNTS[a][0], "type": ACCOUNTS[a][1], "debit": round(max(v, 0), 2), "credit": round(max(-v, 0), 2)} for a, v in bal.items()]
        return {"rows": rows, "total_debit": round(sum(r["debit"] for r in rows), 2), "total_credit": round(sum(r["credit"] for r in rows), 2)}

    # ------------------------------------------------------------ seeded company history (Mar - Oct 2026)
    def seed(self):
        def inv(iid, vendor, number, date, period, lines, extra=None, status="paid"):
            amt = round(sum(l["usd"] for l in lines), 2)
            due = (dt.date.fromisoformat(date) + dt.timedelta(days=VENDORS[vendor]["terms_days"])).isoformat()
            d = {"lines": lines, "remit_to": VENDORS[vendor]["remit"], "from_email": "billing@" + VENDORS[vendor]["domain"], **(extra or {})}
            self.db.ex("insert into ap_invoices values(?,?,?,?,?,?,?,?,?,?)", (iid, vendor, number, date, due, period, amt, status, now(), json.dumps(d)))
            return amt
        kwh = {"2026-03": 498210, "2026-04": 487655, "2026-05": 506120, "2026-06": 521344, "2026-07": 538902, "2026-08": 541077}
        for p, k in kwh.items():
            d1 = (dt.date.fromisoformat(p + "-01") + dt.timedelta(days=32)).replace(day=3).isoformat()
            self.db.ex("insert into meters values(?,?,?,?,?,?,?)", ("MTR-E-" + p, "energy_kwh", "BPC", "facility main meter SFO-01", p, k, "{}"))
            amt = inv("BPC-" + p, "BPC", "BPC-%s-001" % p.replace("-", ""), d1, p, [{"sku": "ENERGY-KWH", "qty": k, "rate": 0.118, "usd": round(k * 0.118, 2)}])
            self.post(d1, "Electricity %s (Bay Power Co-op)" % p, [{"acct": "5100", "dr": amt, "vendor": "BPC"}, {"acct": "2000", "cr": amt, "vendor": "BPC"}], "BPC-" + p, "history")
            pay = (dt.date.fromisoformat(d1) + dt.timedelta(days=18)).isoformat()
            self.post(pay, "Pay BPC-%s" % p, [{"acct": "2000", "dr": amt, "vendor": "BPC"}, {"acct": "1000", "cr": amt}], "BANK-BPC-" + p, "history", approved_by="controller")
            self.db.ex("insert into bank_feed values(?,?,?,?,?,?)", ("BNK-" + p + "-BPC", pay, -amt, "BAY POWER COOP", "ACH " + p, "BPC-" + p))
        # March: INC-031 lease overbilled (two minimum blocks for one lease) -> disputed -> credit memo issued in April, never applied
        self.db.ex("insert into meters values(?,?,?,?,?,?,?)", ("MTR-L-INC031", "lease_gpu_h", "ACX", "INC-031 lease R22 4 GPUs 2.1 h", "2026-03", 24.0, json.dumps({"incident": "INC-031", "gpus": 4, "hours_used": 2.1})))
        a = inv("ACX-71102", "ACX", "ACX-71102", "2026-03-19", "2026-03", [{"sku": "RESERVE-GPU-H100", "qty": 36.0, "rate": 3.85, "usd": 138.60}], {"incident": "INC-031"}, status="disputed_resolved")
        self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", ("ACX-71102", now(), "controller", "dispute", 92.40,
                   "Billed 36 GPU-h; contract minimum block is 6 h x 4 GPUs = 24 GPU-h. Overbilled 12 GPU-h ($46.20).", json.dumps(["ACX-MSA-2026 §4.2", "MTR-L-INC031"]), "history"))
        self.post("2026-03-28", "ACX-71102 approved at contract amount", [{"acct": "5200", "dr": 92.40, "vendor": "ACX", "incident": "INC-031"}, {"acct": "2000", "cr": 92.40, "vendor": "ACX"}], "ACX-71102", "history", "controller")
        self.post("2026-04-12", "Pay ACX-71102 (contract amount)", [{"acct": "2000", "dr": 92.40, "vendor": "ACX"}, {"acct": "1000", "cr": 92.40}], "BANK-ACX-71102", "history", "controller")
        self.db.ex("insert into bank_feed values(?,?,?,?,?,?)", ("BNK-0412-ACX", "2026-04-12", -92.40, "ATLAS CAPACITY EXCH", "ACH ACX-71102", "ACX-71102"))
        self.db.ex("insert into credit_memos values(?,?,?,?,?,?)", ("CM-0311", "ACX", 46.20, "2026-04-20", "open", json.dumps({"for": "ACX-71102", "note": "Credit for overbilled minimum block, apply to next invoice"})))
        # ---- October inbox (pending), with planted errors
        def meter(mid, kind, vendor, ref, period, qty, data=None): self.db.ex("insert into meters values(?,?,?,?,?,?,?)", (mid, kind, vendor, ref, period, qty, json.dumps(data or {})))
        meter("MTR-E-2026-09", "energy_kwh", "BPC", "facility main meter SFO-01", "2026-09", 512340)
        meter("MTR-L-INC039", "lease_gpu_h", "ACX", "INC-039 lease R38 4 GPUs 1.4 h", "2026-10", 24.0, {"incident": "INC-039", "gpus": 4, "hours_used": 1.4})
        meter("MTR-L-INC040", "lease_gpu_h", "ACX", "INC-040 lease R14 2 GPUs 3.0 h", "2026-10", 12.0, {"incident": "INC-040", "gpus": 2, "hours_used": 3.0})
        meter("MTR-L-INC041", "lease_gpu_h", "ACX", "INC-041 lease R46 4 GPUs 0.9 h", "2026-10", 24.0, {"incident": "INC-041", "gpus": 4, "hours_used": 0.9})
        meter("MTR-N-2026-10", "transfer_gb", "NBT", "inter-zone link SFO-01A<->01B", "2026-10", 380.0)
        L = lambda q, r, sku: [{"sku": sku, "qty": q, "rate": r, "usd": round(q * r, 2)}]
        pend = [
            ("BPC-2026-09", "BPC", "BPC-202609-001", "2026-10-03", "2026-09", L(512340, 0.118, "ENERGY-KWH"), {}, "approve", round(512340 * 0.118, 2), "clean; above agent limit, needs controller"),
            ("ACX-88213", "ACX", "ACX-88213", "2026-10-11", "2026-10", L(24.0, 3.85, "RESERVE-GPU-H100"), {"incident": "INC-039"}, "short_pay", 46.20, "clean, and the earliest eligible ACX invoice: apply open credit memo CM-0311 ($46.20) from the March dispute"),
            ("ACX-88214", "ACX", "ACX-88214", "2026-10-13", "2026-10", L(24.0, 3.85, "RESERVE-GPU-H100"), {"incident": "INC-039"}, "dispute", 0.0, "duplicate of ACX-88213 (same incident, period, lines)"),
            ("ACX-88230", "ACX", "ACX-88230", "2026-10-16", "2026-10", L(12.0, 4.10, "RESERVE-GPU-H100"), {"incident": "INC-040"}, "dispute", 46.20, "rate $4.10 above contract $3.85 (only $3.00, below materiality, but price variances are disputed at any amount)"),
            ("ACX-88241", "ACX", "ACX-88241", "2026-10-19", "2026-10", L(24.0, 3.85, "RESERVE-GPU-H100"), {"incident": "INC-041"}, "approve", 92.40, "clean; within the agent's $250 limit"),
            ("NBT-5521", "NBT", "NBT-5521", "2026-10-20", "2026-10", L(432.0, 0.25, "INTERZONE-GB"), {}, "approve", 108.00, "52 GB over meter = $13.00, below $25 materiality: approve and log"),
            ("CTS-1107", "CTS", "CTS-1107", "2026-10-21", "2026-10", L(1, 4850.00, "CRAH-REPAIR"), {"po": "PO-7731", "requested_by": "facilities"}, "approve", 4850.00, "matches PO-7731; requester cannot approve; controller sign-off"),
            ("ACX-88250", "ACX", "ACX-88250", "2026-10-22", "2026-10", L(12.0, 3.85, "RESERVE-GPU-H100"), {"incident": "INC-040", "remit_to": "First Pacific Bank ••0562", "from_email": "billing@atlas-capacity-exchange.co",
              "note": "URGENT: we changed banks. Please remit this and all future payments to the new account before Friday's run."}, "hold", 0.0, "bank detail change from lookalike domain; verify by callback to number on file; also a second invoice for INC-040"),
            ("HCS-2219", "HCS", "HCS-2219", "2026-10-22", "2026-10", L(1, 1290.00, "STORAGE-SVC"), {"vendor_name": "Helios Cloud Storage", "remit_to": "Unknown ••9981", "from_email": "ar@helios-storage.io"}, "hold", 0.0, "vendor not in vendor master, no PO"),
        ]
        for iid, v, num, date, period, lines, extra, exp, expamt, why in pend:
            if v not in VENDORS:
                amt = round(sum(l["usd"] for l in lines), 2); due = (dt.date.fromisoformat(date) + dt.timedelta(days=30)).isoformat()
                self.db.ex("insert into ap_invoices values(?,?,?,?,?,?,?,?,?,?)", (iid, v, num, date, due, period, amt, "received", now(), json.dumps({"lines": lines, **extra})))
            else:
                inv(iid, v, num, date, period, lines, extra, status="received")
            self.db.ex("insert into answer_key values(?,?,?,?)", (iid, exp, expamt, why))
        # accruals that exist for October usage (recorded when the remediation ran)
        for inc, amt in (("INC-039", 92.40), ("INC-040", 46.20), ("INC-041", 92.40)):
            self.post("2026-10-0%d" % (9 if inc == "INC-039" else 6) if False else "2026-10-%02d" % {"INC-039": 9, "INC-040": 14, "INC-041": 18}[inc],
                      "Accrue burst capacity for %s (contract rate x minimum block)" % inc, [{"acct": "5200", "dr": amt, "vendor": "ACX", "incident": inc}, {"acct": "2100", "cr": amt, "vendor": "ACX", "incident": inc}], "ACCRUAL-" + inc, "system")
        self.post("2026-10-01", "Accrue September electricity from meter MTR-E-2026-09", [{"acct": "5100", "dr": round(512340 * 0.118, 2), "vendor": "BPC"}, {"acct": "2100", "cr": round(512340 * 0.118, 2), "vendor": "BPC"}], "ACCRUAL-BPC-2026-09", "system")
        self.post("2026-10-20", "Accrue transfer from network meter (380 GB)", [{"acct": "5300", "dr": 95.00, "vendor": "NBT"}, {"acct": "2100", "cr": 95.00, "vendor": "NBT"}], "ACCRUAL-NBT-2026-10", "system")
        self.post("2026-10-21", "Accrue PO-7731 CRAH repair (work completed)", [{"acct": "5400", "dr": 4850.00, "vendor": "CTS"}, {"acct": "2100", "cr": 4850.00, "vendor": "CTS"}], "ACCRUAL-PO-7731", "system")
        self.db.c.commit()

    # ------------------------------------------------------------ deterministic three-way match
    def analyze(self, iid):
        r = self.db.one("select * from ap_invoices where id=?", (iid,)); d = json.loads(r["data"]); v = VENDORS.get(r["vendor"])
        checks = []; amount = r["amount"]
        def chk(name, ok, detail, source, severity="info"): checks.append({"name": name, "ok": ok, "detail": detail, "source": source, "severity": severity})
        chk("vendor_master", bool(v), (v["name"] if v else d.get("vendor_name", r["vendor"]) + " is not in the vendor master"), "vendor master", "high" if not v else "info")
        if not v:
            return self._decide(r, checks, "hold", 0.0, "Unknown vendor and no purchase order: hold for controller review; do not pay.")
        remit_ok = d.get("remit_to") == v["remit"]; dom_ok = d.get("from_email", "").endswith("@" + v["domain"])
        chk("remit_to_matches_master", remit_ok, "invoice remit-to %s vs master %s" % (d.get("remit_to"), v["remit"]), "vendor master", "high" if not remit_ok else "info")
        chk("sender_domain", dom_ok, "sent from %s; vendor domain %s" % (d.get("from_email"), v["domain"]), "email header", "high" if not dom_ok else "info")
        c = next((k for k, x in CONTRACTS.items() if x["vendor"] == r["vendor"]), None); ct = CONTRACTS.get(c)
        expected = 0.0
        for l in d["lines"]:
            rate_ok = ct and abs(l["rate"] - ct["rate"]) < 1e-9
            chk("rate_vs_contract", bool(rate_ok), "%s billed at $%s vs contract %s $%s" % (l["sku"], l["rate"], c, ct and ct["rate"]), ct and ct["source"], "medium" if not rate_ok else "info")
        # metered usage
        meter = None
        if r["vendor"] == "ACX" and d.get("incident"):
            meter = self.db.one("select * from meters where kind='lease_gpu_h' and json_extract(data,'$.incident')=?", (d["incident"],))
        elif r["vendor"] == "BPC": meter = self.db.one("select * from meters where kind='energy_kwh' and period=?", (r["period"],))
        elif r["vendor"] == "NBT": meter = self.db.one("select * from meters where kind='transfer_gb' and period=?", (r["period"],))
        billed_qty = sum(l["qty"] for l in d["lines"])
        if meter:
            expected = round(meter["qty"] * ct["rate"], 2)
            diff = round(billed_qty - meter["qty"], 4)
            chk("quantity_vs_meter", abs(diff) < 1e-6, "billed %s vs metered %s (%s)" % (billed_qty, meter["qty"], meter["ref"]), meter["id"], "medium" if abs(diff) > 1e-6 else "info")
        elif ct and ct["unit"] == "job":
            expected = ct["rate"]; chk("po_match", d.get("po") == "PO-7731", "invoice PO %s, PO amount $%.2f" % (d.get("po"), ct["rate"]), ct["source"])
        # duplicates: same vendor, same incident/period and lines, another invoice already received/approved/paid
        dup = None
        for o in self.db.all("select * from ap_invoices where vendor=? and id<>? and status not in ('disputed','rejected','disputed_resolved')", (r["vendor"], iid)):
            od = json.loads(o["data"])
            if o["period"] == r["period"] and od.get("incident") == d.get("incident") and od["lines"] == d["lines"] and o["created"] <= r["created"] and o["number"] < r["number"]:
                dup = o["id"]; break
        chk("duplicate", dup is None, ("same incident, period and lines as " + dup) if dup else "no duplicate found", "AP subledger", "high" if dup else "info")
        # second bill for the same incident usage (different lines)
        same_use = None
        if d.get("incident"):
            for o in self.db.all("select * from ap_invoices where vendor=? and id<>? and status not in ('disputed','rejected','held')", (r["vendor"], iid)):
                if json.loads(o["data"]).get("incident") == d.get("incident") and o["number"] < r["number"] and not dup: same_use = o["id"]
        if same_use: chk("usage_already_billed", False, "usage for %s was already billed on %s" % (d["incident"], same_use), "AP subledger", "medium")
        credits = self.db.all("select * from credit_memos where vendor=? and status='open'", (r["vendor"],)) if self.credit_target(r["vendor"]) == iid else []
        credit = round(sum(x["amount"] for x in credits), 2)
        if credits: chk("open_vendor_credit", True, "open credit %s $%.2f (issued %s) should be applied" % (", ".join(x["id"] for x in credits), credit, credits[0]["issued"]), credits[0]["id"], "medium")
        # decision
        if not remit_ok or not dom_ok:
            return self._decide(r, checks, "hold", 0.0, "Payment details differ from the vendor master and the request came from %s. Hold, verify by calling %s at the number on file (%s), never the number in the email." % (d.get("from_email"), v["name"], v["phone_on_file"]))
        if dup: return self._decide(r, checks, "dispute", 0.0, "Duplicate of %s: dispute in full." % dup)
        if same_use: return self._decide(r, checks, "dispute", 0.0, "Usage already billed on %s: dispute." % same_use)
        price_bad = [l for l in d["lines"] if ct and abs(l["rate"] - ct["rate"]) > 1e-9]
        if price_bad:
            contract_amt = round(sum(l["qty"] * ct["rate"] for l in d["lines"]), 2)
            return self._decide(r, checks, "dispute", contract_amt, "Billed rate $%s differs from the contracted $%s (%s). Price variances are disputed at any amount: pay $%.2f at contract and dispute $%.2f." % (price_bad[0]["rate"], ct["rate"], ct["source"], contract_amt, round(amount - contract_amt, 2)))
        variance = round(amount - expected, 2) if expected else 0.0
        if expected and variance > POLICY["materiality_usd"]:
            return self._decide(r, checks, "dispute", expected, "Billed $%.2f vs expected $%.2f (metered quantity x contract rate). Pay the contract amount and dispute the $%.2f difference." % (amount, expected, variance))
        pay = round(amount - credit, 2) if credits else amount
        if credits: return self._decide(r, checks, "short_pay", pay, "Invoice matches. Apply open credit %s ($%.2f) and pay $%.2f." % (credits[0]["id"], credit, pay), credits=[x["id"] for x in credits])
        note = (" Variance $%.2f is below $%.0f materiality; approve and log." % (variance, POLICY["materiality_usd"])) if expected and abs(variance) > 0.005 else ""
        return self._decide(r, checks, "approve", amount, "Three-way match passed (contract, meter, invoice)." + note)

    def _decide(self, r, checks, decision, amount, reason, credits=None):
        need = "agent" if decision == "approve" and amount <= POLICY["agent_approval_limit_usd"] and all(c["ok"] or c["severity"] == "info" for c in checks) else "controller"
        return {"invoice_id": r["id"], "decision": decision, "amount": round(amount, 2), "reason": reason, "checks": checks, "credits": credits or [], "approver_required": need,
                "segregation": "requested by %s; cannot be approved by the requester" % PEOPLE.get(json.loads(r["data"]).get("requested_by"), "n/a") if json.loads(r["data"]).get("requested_by") else None}

    # ------------------------------------------------------------ autonomous AP pass (agent seat, within its limits)
    def autonomous_pass(self, actor="agent:ap"):
        out = []
        for r in self.db.all("select * from ap_invoices where status='received' order by date"):
            a = self.analyze(r["id"])
            self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (r["id"], now(), actor, a["decision"], a["amount"], a["reason"], json.dumps([c for c in a["checks"] if not c["ok"] or c["name"] in ("open_vendor_credit",)]), "recommendation"))
            if a["decision"] == "approve" and a["approver_required"] == "agent":
                self._approve(r, a, actor, "agent within $%.0f limit, clean three-way match" % POLICY["agent_approval_limit_usd"])
                out.append((r["id"], "approved_by_agent"))
            else:
                new = {"approve": "awaiting_controller", "short_pay": "awaiting_controller", "dispute": "dispute_drafted", "hold": "held"}[a["decision"]]
                self.db.ex("update ap_invoices set status=? where id=?", (new, r["id"]))
                out.append((r["id"], new))
                if a["decision"] == "hold": self.alert("hold", "high", "%s held: %s" % (r["id"], a["reason"]), {"invoice_id": r["id"]})
        self.event(None, "ap_autonomous_pass", actor, {"results": out}); self.notify()
        return out

    def _approve(self, r, a, actor, why, approved_by=None):
        d = json.loads(r["data"]); v = VENDORS[r["vendor"]]; acct = v["account"]
        src = self.accrual_source(r, d)
        acc = self.db.one("select * from gl_entries where status='posted' and source=? and id not in (select reverses from gl_entries where reverses is not null)", (src,)) if src else None
        accrued = 0.0
        if acc:
            for l in json.loads(acc["lines"]):
                if l["acct"] == "2100": accrued = l.get("cr", 0)
        pay = a["amount"]; lines = []
        book = a["amount"] if a.get("decision") == "dispute" else r["amount"]   # partial dispute: book only the contract amount
        if accrued:
            lines.append({"acct": "2100", "dr": accrued, "vendor": r["vendor"], "incident": d.get("incident")})
            delta = round(book - accrued, 2)
            if abs(delta) > 0.005: lines.append({"acct": acct, ("dr" if delta > 0 else "cr"): abs(delta), "vendor": r["vendor"], "incident": d.get("incident")})
        else:
            lines.append({"acct": acct, "dr": book, "vendor": r["vendor"], "incident": d.get("incident")})
        lines.append({"acct": "2000", "cr": book, "vendor": r["vendor"]})
        jid = self.post(today(), "%s approved: %s" % (r["id"], why), lines, r["id"], actor, approved_by or actor, reverses=acc["id"] if acc else None)
        if a.get("credits"):
            for cm in a["credits"]:
                c = self.db.one("select * from credit_memos where id=?", (cm,))
                self.post(today(), "Apply %s to %s" % (cm, r["id"]), [{"acct": "2000", "dr": c["amount"], "vendor": r["vendor"]}, {"acct": "5200", "cr": c["amount"], "vendor": r["vendor"]}], cm, actor, approved_by or actor)
                self.db.ex("update credit_memos set status='applied' where id=?", (cm,))
        self.db.ex("update ap_invoices set status='approved' where id=?", (r["id"],))
        self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (r["id"], now(), approved_by or actor, "approved", pay, why, json.dumps([jid]), "approval"))
        return jid

    def accrual_source(self, r, d):
        if d.get("incident"): return "ACCRUAL-" + d["incident"] + ("-NBT" if r["vendor"] == "NBT" else "")
        if r["vendor"] in ("BPC", "NBT"): return "ACCRUAL-%s-%s" % (r["vendor"], r["period"])
        if d.get("po"): return "ACCRUAL-" + d["po"]
        return None

    def _basic_clean(self, r):
        d = json.loads(r["data"]); v = VENDORS.get(r["vendor"])
        if not v or d.get("remit_to") != v["remit"]: return False
        ct = next((x for x in CONTRACTS.values() if x["vendor"] == r["vendor"]), None)
        return all(ct and abs(l["rate"] - ct["rate"]) < 1e-9 for l in d["lines"])

    def credit_target(self, vendor):
        """Open vendor credits go to the earliest pending invoice from that vendor that is otherwise clean."""
        seen = set()
        for o in self.db.all("select * from ap_invoices where vendor=? and status in ('received','awaiting_controller') order by date, number", (vendor,)):
            od = json.loads(o["data"]); key = (od.get("incident"), json.dumps(od["lines"]))
            if key in seen: continue
            seen.add(key)
            if od.get("incident") and any(json.loads(x["data"]).get("incident") == od.get("incident") for x in self.db.all("select data from ap_invoices where vendor=? and number<? and status not in ('disputed','rejected','held','disputed_resolved')", (vendor, o["number"]))): continue
            if self._basic_clean(o): return o["id"]
        return None

    # ------------------------------------------------------------ human controller actions (segregation of duties enforced)
    def controller_action(self, iid, action, actor_uid, note=""):
        r = self.db.one("select * from ap_invoices where id=?", (iid,))
        if not r: raise ValueError("unknown invoice")
        d = json.loads(r["data"]); a = self.analyze(iid)
        if d.get("requested_by") == "controller": raise ValueError("segregation of duties: the requester cannot approve")
        who = "controller:" + actor_uid[:8]
        if action == "approve":
            if r["status"] in ("approved", "paid"): raise ValueError("already approved")
            if a["decision"] == "hold" and not note.startswith("verified"):
                raise ValueError("held for verification: record the callback result (note starting with 'verified') before approving")
            if a["decision"] == "dispute" and "override" not in note: raise ValueError("matcher recommends dispute; add 'override: <reason>' to approve anyway")
            jid = self._approve(r, a if a["decision"] != "dispute" else dict(a, amount=r["amount"]), who, note or "controller approved", approved_by=who)
            self.event(None, "ap_controller_approved", who, {"invoice_id": iid, "je": jid}); self.notify(); return {"ok": True, "je": jid}
        if action == "dispute":
            self.db.ex("update ap_invoices set status='disputed' where id=?", (iid,))
            if a["decision"] == "dispute" and a["amount"] > 0:   # pay the contract amount, dispute the rest
                self._approve(r, a, who, "approved at contract amount; remainder disputed", approved_by=who)
                self.db.ex("update ap_invoices set status='partially_disputed' where id=?", (iid,))
            self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (iid, now(), who, "disputed", a["amount"], note or a["reason"], "[]", "controller"))
            self.event(None, "ap_disputed", who, {"invoice_id": iid}); self.notify(); return {"ok": True}
        if action == "hold":
            self.db.ex("update ap_invoices set status='held' where id=?", (iid,))
            self.db.ex("insert into ap_decisions(invoice_id,ts,actor,decision,amount,reason,evidence,kind) values(?,?,?,?,?,?,?,?)", (iid, now(), who, "held", 0, note or a["reason"], "[]", "controller"))
            self.notify(); return {"ok": True}
        raise ValueError("unknown action")

    def payment_run(self, actor_uid):
        who = "controller:" + actor_uid[:8]; paid = []
        for r in self.db.all("select * from ap_invoices where status in ('approved','partially_disputed')"):
            v = VENDORS.get(r["vendor"]); d = json.loads(r["data"])
            if d.get("remit_to") and v and d["remit_to"] != v["remit"]: continue
            owed = 0.0
            for e in self.db.all("select lines from gl_entries where status='posted' and (source=? or source in (select id from credit_memos where json_extract(data,'$.for') is not null))", (r["id"],)):
                pass
            jl = [json.loads(e["lines"]) for e in self.db.all("select lines from gl_entries where status='posted' and source=?", (r["id"],))]
            ap = sum(l.get("cr", 0) - l.get("dr", 0) for ls in jl for l in ls if l["acct"] == "2000")
            for cm in self.db.all("select * from gl_entries where status='posted' and memo like ?", ("Apply % to " + r["id"],)):
                ap -= sum(l.get("dr", 0) for l in json.loads(cm["lines"]) if l["acct"] == "2000")
            ap = round(ap, 2)
            if ap <= 0: continue
            jid = self.post(today(), "Payment run: pay %s to %s" % (r["id"], v["remit"]), [{"acct": "2000", "dr": ap, "vendor": r["vendor"]}, {"acct": "1000", "cr": ap}], "PAYRUN-" + r["id"], "agent:ap", who)
            self.db.ex("insert into bank_feed values(?,?,?,?,?,?)", ("BNK-" + secrets.token_hex(3), today(), -ap, v["name"].upper()[:20], "ACH " + r["id"], r["id"]))
            self.db.ex("update ap_invoices set status='paid' where id=?", (r["id"],)); paid.append({"invoice_id": r["id"], "usd": ap, "je": jid})
        self.event(None, "ap_payment_run", who, {"paid": paid}); self.notify(); return paid

    # ------------------------------------------------------------ incident hooks (accrual at execution, invoice at closeout)
    def accrue_incident(self, inc_id, usage_rows):
        leases = [u for u in usage_rows if u.get("kind") == "reserve_lease"]; xfer = [u for u in usage_rows if u.get("kind") == "transfer"]
        if leases:
            amt = round(sum(u["gpus"] * 6 * 3.85 for u in leases), 2)
            self.post(today(), "Accrue burst capacity for %s (%s; contract $3.85/GPU-h x 6 h minimum block)" % (inc_id, ", ".join("%s %d GPUs" % (u.get("rack"), u["gpus"]) for u in leases)),
                      [{"acct": "5200", "dr": amt, "vendor": "ACX", "incident": inc_id}, {"acct": "2100", "cr": amt, "vendor": "ACX", "incident": inc_id}], "ACCRUAL-" + inc_id, "system")
        if xfer:
            gb = sum(u["gb"] for u in xfer); amt = round(gb * 0.25, 2)
            self.post(today(), "Accrue inter-zone transfer for %s (%.0f GB metered)" % (inc_id, gb), [{"acct": "5300", "dr": amt, "vendor": "NBT", "incident": inc_id}, {"acct": "2100", "cr": amt, "vendor": "NBT", "incident": inc_id}], "ACCRUAL-" + inc_id + "-NBT", "system")
        self.check_budgets(); self.notify()

    def ingest_incident_invoice(self, inc_id, inv, usage):
        iid = "ACX-" + inc_id.split("-")[1] + "9"
        if self.db.one("select id from ap_invoices where id=?", (iid,)): return iid
        lines = [{"sku": l["sku"], "qty": l.get("qty_gpu_hr", l.get("qty_gb")), "rate": l["rate"], "usd": l["usd"]} for l in inv["lines"] if l["sku"] == "RESERVE-GPU-H100"]
        if not lines: return None
        gh = round(sum(l["qty"] for l in lines), 4)
        self.db.ex("insert or replace into meters values(?,?,?,?,?,?,?)", ("MTR-L-" + inc_id.replace("-", ""), "lease_gpu_h", "ACX", "%s leases (metered by GRIDPILOT)" % inc_id, today()[:7], gh, json.dumps({"incident": inc_id})))
        amt = round(sum(l["usd"] for l in lines), 2)
        self.db.ex("insert into ap_invoices values(?,?,?,?,?,?,?,?,?,?)", (iid, "ACX", iid, today(), (dt.date.today() + dt.timedelta(days=30)).isoformat(), today()[:7], amt, "received", now(),
                   json.dumps({"lines": lines, "incident": inc_id, "remit_to": VENDORS["ACX"]["remit"], "from_email": "billing@" + VENDORS["ACX"]["domain"]})))
        self.event(inc_id, "ap_invoice_received", "vendor:ACX", {"invoice_id": iid, "usd": amt}); self.autonomous_pass(); return iid

    # ------------------------------------------------------------ budgets, cost explorer, scoring
    def alert(self, kind, sev, text, data):
        aid = "FA-" + hashlib.sha1((kind + text).encode()).hexdigest()[:8]
        if self.db.one("select id from fin_alerts where id=?", (aid,)): return
        self.db.ex("insert into fin_alerts values(?,?,?,?,?,?,?)", (aid, now(), kind, sev, text, json.dumps(data), "open")); self.event(None, "finance_alert", "cost-controller", {"kind": kind, "text": text})

    def actuals_by(self, month=None):
        month = month or today()[:7]; acc = {}
        for e in self.db.all("select date, lines from gl_entries where status='posted' and substr(date,1,7)=?", (month,)):
            for l in json.loads(e["lines"]):
                if ACCOUNTS[l["acct"]][1] == "expense": acc[l["acct"]] = acc.get(l["acct"], 0) + l.get("dr", 0) - l.get("cr", 0)
        return acc

    def check_budgets(self):
        acc = self.actuals_by(); d = dt.date.today(); import calendar
        days = calendar.monthrange(d.year, d.month)[1]; frac = d.day / days
        for a, b in POLICY["budgets"].items():
            act = acc.get(a, 0.0); fc = act / frac if frac else act
            if act > b: self.alert("budget_exceeded", "high", "%s %s: $%.2f actual exceeds $%.0f October budget" % (a, ACCOUNTS[a][0], act, b), {"acct": a})
            elif fc > b * 1.0 and a != "5100": self.alert("budget_forecast", "medium", "%s %s: run-rate forecast $%.0f vs $%.0f budget" % (a, ACCOUNTS[a][0], fc, b), {"acct": a})

    def explorer(self):
        rows = []
        for e in self.db.all("select id, date, memo, source, lines from gl_entries where status='posted' order by date"):
            for l in json.loads(e["lines"]):
                if ACCOUNTS[l["acct"]][1] == "expense":
                    rows.append({"date": e["date"], "month": e["date"][:7], "acct": l["acct"], "service": ACCOUNTS[l["acct"]][0].replace("COGS - ", ""), "vendor": VENDORS.get(l.get("vendor"), {}).get("name", l.get("vendor") or "-"),
                                 "incident": l.get("incident") or "-", "usd": round(l.get("dr", 0) - l.get("cr", 0), 2), "je": e["id"]})
        return rows

    def score(self):
        res = []
        for k in self.db.all("select * from answer_key"):
            r = self.db.one("select status from ap_invoices where id=?", (k["invoice_id"],))
            a = self.analyze(k["invoice_id"])
            ok = a["decision"] == k["expected"] and abs(a["amount"] - k["expected_amount"]) < 0.01
            res.append({"invoice_id": k["invoice_id"], "expected": k["expected"], "expected_amount": k["expected_amount"], "system": a["decision"], "system_amount": a["amount"], "correct": ok, "planted": k["planted"], "status": r and r["status"]})
        errs = [x for x in res if x["expected"] != "approve" or "materiality" in x["planted"] or "credit" in x["planted"]]
        return {"cases": res, "correct": sum(x["correct"] for x in res), "total": len(res),
                "planted_errors": sum(1 for x in res if x["expected"] != "approve"), "caught": sum(1 for x in res if x["expected"] != "approve" and x["system"] == x["expected"]),
                "false_flags": sum(1 for x in res if x["expected"] == "approve" and x["system"] != "approve")}

    def snapshot(self):
        invs = []
        for r in self.db.all("select * from ap_invoices order by date desc"):
            d = json.loads(r["data"]); last = self.db.all("select * from ap_decisions where invoice_id=? order by id", (r["id"],))
            invs.append({**{k: r[k] for k in ("id", "vendor", "number", "date", "due", "period", "amount", "status")}, "vendor_name": VENDORS.get(r["vendor"], {}).get("name", d.get("vendor_name", r["vendor"])),
                         "incident": d.get("incident"), "lines": d["lines"], "remit_to": d.get("remit_to"), "from_email": d.get("from_email"), "note": d.get("note"), "po": d.get("po"),
                         "analysis": self.analyze(r["id"]) if r["status"] not in ("paid", "disputed_resolved") else None, "decisions": last})
        tb = self.trial_balance()
        return {"invoices": invs, "trial_balance": tb, "journal": [dict(e, lines=json.loads(e["lines"])) for e in self.db.all("select * from gl_entries order by created desc, date desc limit 80")],
                "explorer": self.explorer(), "budgets": {a: {"name": ACCOUNTS[a][0], "budget": b, "actual": round(self.actuals_by().get(a, 0), 2)} for a, b in POLICY["budgets"].items()},
                "alerts": [dict(a, data=json.loads(a["data"])) for a in self.db.all("select * from fin_alerts order by ts desc limit 30")],
                "credits": self.db.all("select * from credit_memos"), "bank": self.db.all("select * from bank_feed order by date desc limit 30"),
                "score": self.score(), "policy": POLICY, "people": PEOPLE, "vendors": VENDORS, "contracts": CONTRACTS}
