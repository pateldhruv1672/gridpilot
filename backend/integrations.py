
"""Sponsor adapters. Secrets never touch this process: Mana's symbolic proxy injects them server-side.
CREAO: app-scoped API Trigger (capi_ key). Cartesia: Managed Agents (sk_car_ key). Contracts from official docs (2026-10)."""
import json, time, requests, symbolic_proxy

CARTESIA_VERSION = "2026-08-14"

class ProviderError(Exception):
    pass

class _Proxy:
    def __init__(self, name, url, method, headers):
        self.name, self.url, self.method, self.headers = name, url, method, headers
        self._p = None; self.error = None
    def get(self):
        if self._p is None:
            try:
                self._p = symbolic_proxy.private({"name": self.name, "url": self.url, "method": self.method,
                                                  "allow_subpaths": True, "headers": self.headers})
                self.error = None
            except Exception as e:
                self.error = str(e)[:300]
                raise ProviderError(self.error)
        return self._p
    def call(self, sub="", json_body=None, timeout=30):
        p = self.get()
        r = requests.request(self.method, p.url + sub, headers=p.headers, json=json_body, timeout=timeout, allow_redirects=False)
        try: body = r.json()
        except Exception: body = {"raw": r.text[:2000]}
        if r.status_code >= 400:
            raise ProviderError("%s %s -> HTTP %s: %s" % (self.method, sub, r.status_code, json.dumps(body)[:600]))
        return body

_creao_h = {"authorization": "Bearer ${secrets.CREAO_API_KEY}", "content-type": "application/json"}
CREAO_POST = _Proxy("gp_creao_post", "https://agent.creao.ai/api/v1/apps", "POST", _creao_h)
CREAO_GET = _Proxy("gp_creao_get", "https://agent.creao.ai/api/v1/apps", "GET", {"authorization": "Bearer ${secrets.CREAO_API_KEY}"})

def creao_start_run(app_id, inputs, webhook_url=None):
    body = {"inputs": inputs}
    if webhook_url: body["webhookUrl"] = webhook_url
    return CREAO_POST.call("/%s/runs" % app_id, body)          # {id, status, appId, appName}

def creao_get_run(app_id, run_id):
    return CREAO_GET.call("/%s/runs/%s" % (app_id, run_id))

def creao_list_runs(app_id):
    return CREAO_GET.call("/%s/runs" % app_id)

_cart_h = {"authorization": "Bearer ${secrets.CARTESIA_API_KEY}", "cartesia-version": CARTESIA_VERSION, "content-type": "application/json"}
CART = {m: _Proxy("gp_cartesia_" + m.lower(), "https://api.cartesia.ai", m, dict(_cart_h)) for m in ("GET", "POST", "PATCH")}

def cartesia(method, path, body=None, timeout=30):
    return CART[method].call(path, body, timeout)

# ---- CREAO CLI (account API, cr_sk_ key): create personal agents on the fly and run them. Docs: docs.creao.ai/developer-api
_cli_h = {"authorization": "Bearer ${secrets.CREAO_CLI_KEY}", "content-type": "application/json"}
CLI_POST = _Proxy("gp_creao_cli_post", "https://developer.creao.ai/v1", "POST", _cli_h)
CLI_GET = _Proxy("gp_creao_cli_get", "https://developer.creao.ai/v1", "GET", {"authorization": "Bearer ${secrets.CREAO_CLI_KEY}"})

def cli_available():
    try: CLI_GET.get(); return True
    except ProviderError: return False

def cli_create_agent(description, webhook_url=None, model=None):
    body = {"input": description}
    if webhook_url: body["webhook_url"] = webhook_url
    if model: body["model"] = model
    return CLI_POST.call("/agents", body, timeout=60)        # {run_id, status, agent_id: null, conversation_id, model}

def cli_start_run(agent_id, inp, webhook_url=None, conversation_id=None, model=None):
    body = {"agent_id": agent_id, "input": inp}
    if webhook_url: body["webhook_url"] = webhook_url
    if conversation_id: body["conversation_id"] = conversation_id
    if model: body["model"] = model
    return CLI_POST.call("/runs", body, timeout=60)          # {run_id, status, agent_id, conversation_id, model}

def cli_get_run(run_id):
    return CLI_GET.call("/runs/%s" % run_id)                # {run_id, status, agent_id, result{text, agent_id?}, usage}

def cli_list_agents():
    return CLI_GET.call("/agents")
