"""Finance desk proof: planted errors vs answer key, ledger integrity, controls. Run: python tests/test_finance_desk.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import engine as E, finance_desk as FD
db = E.DB(os.path.join(tempfile.mkdtemp(), "t.db")); desk = FD.Desk(db)
ok = 0; total = 0
def check(name, cond):
    global ok, total; total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name)
sc = desk.score()
check("answer key: %d/%d invoices decided correctly" % (sc["correct"], sc["total"]), sc["correct"] == sc["total"])
check("planted errors caught %d/%d" % (sc["caught"], sc["planted_errors"]), sc["caught"] == sc["planted_errors"])
check("no false flags on clean invoices", sc["false_flags"] == 0)
res = dict(desk.autonomous_pass())
check("agent approves only the clean invoice under its $250 limit", [k for k, v in res.items() if v == "approved_by_agent"] == ["ACX-88241"])
check("bank-detail change is held", res["ACX-88250"] == "held")
try: desk.controller_action("ACX-88250", "approve", "controller-uid", ""); check("held invoice cannot be approved without a recorded callback", False)
except ValueError: check("held invoice cannot be approved without a recorded callback", True)
try: desk.controller_action("ACX-88214", "approve", "controller-uid", ""); check("matcher dispute needs an explicit override", False)
except ValueError: check("matcher dispute needs an explicit override", True)
try: desk.post("2026-10-24", "bad", [{"acct": "5200", "dr": 10}, {"acct": "2000", "cr": 9}], "t", "t"); check("ledger refuses an unbalanced entry", False)
except ValueError: check("ledger refuses an unbalanced entry", True)
for i, a in (("BPC-2026-09", "approve"), ("ACX-88213", "approve"), ("ACX-88230", "dispute"), ("CTS-1107", "approve")): desk.controller_action(i, a, "controller-uid")
paid = {p["invoice_id"]: p["usd"] for p in desk.payment_run("controller-uid")}
check("March credit memo applied: ACX-88213 paid $46.20", abs(paid.get("ACX-88213", 0) - 46.20) < 0.01)
check("price variance: ACX-88230 paid at contract $46.20, $3.00 disputed", abs(paid.get("ACX-88230", 0) - 46.20) < 0.01)
check("payment run never pays a held invoice", "ACX-88250" not in paid and "HCS-2219" not in paid)
tb = desk.trial_balance()
check("trial balance balances (%.2f = %.2f)" % (tb["total_debit"], tb["total_credit"]), abs(tb["total_debit"] - tb["total_credit"]) < 0.01)
print("%d/%d checks passed" % (ok, total)); sys.exit(0 if ok == total else 1)
