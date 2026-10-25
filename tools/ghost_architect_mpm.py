# Ghost Architect (scheduled). A Mana agent observes GRIDPILOT and proposes orchestration v2.
# Reads the live prompt+digest from the GRIDPILOT backend, asks a fresh Mana chat, and posts the JSON blueprint back.
# Nothing is dispatched automatically: the operator approves each proposal in the Ghost architect tab.
import json, requests, mana
from env import get_user_id
port = json.load(open("/workspace/gridpilot/state/port.json"))["port"]
B = "http://127.0.0.1:%d" % port; H = {"X-Sender-Uid": get_user_id(), "Content-Type": "application/json"}
prompt = requests.get(B + "/api/ghost/prompt?local=1", headers=H, timeout=60).json()["prompt"]
cid = mana.mint_chat_id()
h = mana.ask(cid, prompt, path="apps/gridpilot", verbose=False)
events = h.result()
text = "".join(e.get("content", "") or "" for e in events if e.get("type") == "content")
r = requests.post(B + "/api/ghost/blueprint", headers=H, json={"raw": text, "source": "mana-scheduler", "chat_id": cid}, timeout=60).json()
print("ghost run", cid, "->", r)
if not r.get("ok"): raise SystemExit("ghost blueprint not parsed: " + str(r))
