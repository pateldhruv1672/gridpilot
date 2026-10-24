
"""Versioned configuration store: a real git repository of YAML infrastructure config.
Candidates are built in an isolated git worktree; only validated candidates are committed to main."""
import os, subprocess, shutil, yaml, json, time, uuid
REPO = "/workspace/gridpilot/config-repo"

def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd or REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout

def _files_from_config(cfg):
    files = {}
    for rid, rk in cfg["racks"].items():
        files["infrastructure/racks/%s.yaml" % rid] = {"rack_id": rid, **rk}
    files["infrastructure/scheduler/workloads.yaml"] = {"workloads": cfg["workloads"]}
    files["infrastructure/cooling/policies.yaml"] = {"units": cfg["cooling"], "fan_pct_range": [60, 130]}
    return files

APPROVAL_RULES = {
    "auto_allowed": ["set_cooling_fan within fan_pct_range", "set_gpu_power_limit reduction <= 10%", "restart_noncritical_workload", "telemetry/diagnosis"],
    "approval_required": ["migrate_workload", "set_gpu_power_limit reduction > 10%", "incremental_cost_usd > 100", "affects protected SLA"],
    "forbidden": ["delete_*", "shutdown_rack (outside emergency policy)", "firmware_*", "billing_*"],
}

def write_tree(root, cfg):
    for path, data in _files_from_config(cfg).items():
        p = os.path.join(root, path); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: yaml.safe_dump(data, f, sort_keys=False)
    p = os.path.join(root, "infrastructure/policies/approval-rules.yaml"); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f: yaml.safe_dump(APPROVAL_RULES, f, sort_keys=False)

def read_tree(root=None):
    root = root or REPO
    racks = {}
    d = os.path.join(root, "infrastructure/racks")
    for fn in sorted(os.listdir(d)):
        data = yaml.safe_load(open(os.path.join(d, fn)))
        rid = data.pop("rack_id"); racks[rid] = data
    wl = yaml.safe_load(open(os.path.join(root, "infrastructure/scheduler/workloads.yaml")))["workloads"]
    cool = yaml.safe_load(open(os.path.join(root, "infrastructure/cooling/policies.yaml")))["units"]
    return {"racks": racks, "workloads": wl, "cooling": cool}

def init(cfg, reset=False):
    if reset and os.path.exists(REPO):
        shutil.rmtree(REPO)
    if os.path.exists(os.path.join(REPO, ".git")):
        return head()
    os.makedirs(REPO, exist_ok=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "gridpilot@atlas.compute"); git("config", "user.name", "GRIDPILOT backend")
    write_tree(REPO, cfg)
    git("add", "-A"); git("commit", "-q", "-m", "baseline: SFO-01 pod configuration")
    git("tag", "baseline")
    return head()

def head(): return git("rev-parse", "HEAD").strip()

def build_candidate(cfg_new, label):
    """Write the candidate config into an isolated worktree; return (worktree_path, diff_text, changed_paths)."""
    wt = "/tmp/gridpilot-wt-%s" % uuid.uuid4().hex[:8]
    branch = "candidate/%s" % label
    git("worktree", "add", "-q", "-b", branch, wt, "HEAD")
    write_tree(wt, cfg_new)
    git("add", "-A", cwd=wt)
    diff = git("diff", "--cached", "--unified=2", cwd=wt)
    changed = [l for l in git("diff", "--cached", "--name-only", cwd=wt).splitlines() if l]
    return wt, branch, diff, changed

def commit_candidate(wt, branch, message):
    git("commit", "-q", "-m", message, cwd=wt)
    sha = git("rev-parse", "HEAD", cwd=wt).strip()
    git("merge", "-q", "--ff-only", branch)
    discard_candidate(wt, branch)
    return sha

def discard_candidate(wt, branch):
    git("worktree", "remove", "--force", wt, check=False)
    git("branch", "-D", branch, check=False)

def snapshot(name):
    sha = head(); git("tag", "-f", name, sha); return sha

def restore(snapshot_sha, message):
    """Rollback = new commit whose tree equals the snapshot; returns (sha, diff)."""
    before = head()
    git("checkout", snapshot_sha, "--", "infrastructure")
    git("add", "-A")
    diff = git("diff", "--cached", "--unified=2")
    git("commit", "-q", "--allow-empty", "-m", message)
    return head(), diff, before

def log(n=30):
    out = git("log", "-n", str(n), "--pretty=format:%H|%h|%ct|%s")
    return [dict(zip(["sha", "short", "ts", "message"], l.split("|", 3))) for l in out.splitlines() if l]

def show_diff(sha):
    return git("show", "--unified=2", "--format=", sha)
