"""Keep the GitHub repo in sync with the live GRIDPILOT app.
Pulls the control-room UI source from Firebase into frontend/, commits any change, and pushes with the connected GitHub account.
The token is passed per command (http.extraheader) and never written to git config or disk."""
import base64, subprocess, sys, time
REPO = "/workspace/gridpilot"
REMOTE = "https://github.com/pateldhruv1672/gridpilot.git"
UID = "NhYNhCrTGxgLBbOLkQ6QkRCEdhD3"

def sh(*a, check=True, extra=None):
    cmd = ["git"] + (extra or []) + list(a)
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if check and r.returncode != 0: raise RuntimeError("git %s: %s" % (a[0], r.stderr.strip()[-400:]))
    return r.stdout.strip()

def pull_frontend():
    from firebase_helper import firebase
    code = firebase.read(UID + "/file-system/apps/gridpilot/app/code")
    if code:
        p = REPO + "/frontend/GridpilotApp.jsx"
        old = open(p).read() if __import__("os").path.exists(p) else ""
        if old != code: open(p, "w").write(code)

def sync(message=None):
    pull_frontend()
    sh("add", "-A")
    if sh("status", "--porcelain"):
        sh("commit", "-q", "-m", message or ("sync: " + time.strftime("%Y-%m-%d %H:%M:%S %Z")))
    from mana_oauth import Connection
    import requests
    tok = Connection("github").bind()
    chk = requests.get("https://api.github.com/repos/pateldhruv1672/gridpilot", headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}, timeout=20)
    if chk.status_code != 200 or not chk.json().get("permissions", {}).get("push"):
        print("waiting: github.com/pateldhruv1672/gridpilot not reachable with push access (HTTP %s); local commits kept" % chk.status_code); return
    hdr = "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + tok).encode()).decode()
    ahead = sh("rev-list", "--count", "origin/main..main", check=False) if sh("ls-remote", "--heads", REMOTE, "main", extra=["-c", "http.extraheader=" + hdr], check=False) else "init"
    if ahead == "0":
        print("in sync:", sh("rev-parse", "--short", "HEAD")); return
    sh("push", "-u", REMOTE, "main:main", extra=["-c", "http.extraheader=" + hdr])
    sh("fetch", "-q", REMOTE, "main:refs/remotes/origin/main", extra=["-c", "http.extraheader=" + hdr], check=False)
    print("pushed:", sh("rev-parse", "--short", "HEAD"))

if __name__ == "__main__":
    sync(" ".join(sys.argv[1:]) or None)
