// GRIDPILOT Audit Watcher: an independent agent that subscribes to SpacetimeDB like any dashboard (anonymous, read-only)
// and verifies, from the replicated data alone, that the backend obeyed its own rules:
//   1. approval chain: every "-> EXECUTING" of plan P vN is preceded by a human "-> APPROVED" of P vN, or by an AUTO_ALLOWED policy decision
//   2. no agent identity ever approves (an approval credited to creao:* is a violation)
//   3. detector coverage: a rack critical for more than 20 s with no open incident
//   4. feed freshness: facility_summary.seq keeps advancing
// It proves it cannot write by calling the harmless probe_write reducer, which the module rejects for non-writers.
// Findings are reported to the GRIDPILOT backend (loopback), which records them in its audit log; the backend stays the only writer.
import { DbConnection } from '../frontend/module_bindings/index.ts';
import fs from 'node:fs';

const API = process.env.GP_API || 'http://127.0.0.1:43437';
const UID = process.env.GP_UID || '';
const TOKEN_FILE = process.env.GP_WATCHER_TOKEN_FILE || '/workspace/gridpilot/state/watcher_stdb_token';
const S = { status: 'connecting', identity: null, seq: 0, lastSeqAt: 0, started: Date.now() / 1000, checks: 0, verified: [], findings: {}, probe: null, subscribers: 0, rows: {} };
let conn = null, retry = 0;

const HUMAN = /^(operator:|on-call:|controller:)/;
function verify() {
  if (!conn) return;
  const now = Date.now() / 1000; S.checks++;
  const ev = [...conn.db.auditEvent.iter()].sort((a, b) => (a.eventId < b.eventId ? -1 : 1));
  const byInc = {};
  for (const e of ev) if (e.kind === 'state_transition' && e.incidentId) (byInc[e.incidentId] = byInc[e.incidentId] || []).push(e);
  const verified = [];
  for (const [inc, list] of Object.entries(byInc)) {
    list.forEach((e, k) => {
      const m = e.summary.match(/^(\w+) → EXECUTING · execution (\S+) of (\S+) v(\d+)/); if (!m) return;
      const [, from, exe, pid, ver] = m; const prior = list.slice(0, k);
      const appr = prior.filter(p => new RegExp('→ APPROVED · .* of ' + pid + ' v' + ver + '\\b').test(p.summary)).pop();
      const auto = from === 'POLICY_CHECKED' && prior.some(p => /→ POLICY_CHECKED · decision AUTO_ALLOWED/.test(p.summary));
      const key = 'chain:' + inc + ':' + exe;
      if (appr && HUMAN.test(appr.actor)) verified.push({ incident: inc, execution: exe, plan: pid + ' v' + ver, approved_by: appr.actor, how: appr.summary.split(' · ')[1] || '' });
      else if (auto) verified.push({ incident: inc, execution: exe, plan: pid + ' v' + ver, approved_by: 'policy AUTO_ALLOWED', how: 'within autonomous limits' });
      else if (appr) S.findings[key] = S.findings[key] || { key, severity: 'high', incident: inc, ts: now, text: execText(exe, pid, ver) + ' was approved by a non-human identity (' + appr.actor + ')' };
      else if (prior.length) S.findings[key] = S.findings[key] || { key, severity: 'high', incident: inc, ts: now, text: execText(exe, pid, ver) + ' started with no matching human approval or AUTO_ALLOWED decision in the audit trail' };
    });
  }
  S.verified = verified.slice(-12);
  // detector coverage
  const open = new Set([...conn.db.incident.iter()].filter(i => i.state !== 'CLOSED').map(i => i.rack));
  for (const r of conn.db.rack.iter()) {
    const key = 'coverage:' + r.rackId;
    if (r.status === 'critical' && !open.has(r.rackId)) { S.rows[key] = S.rows[key] || now; if (now - S.rows[key] > 20 && !S.findings[key]) S.findings[key] = { key, severity: 'medium', ts: now, text: r.rackId + ' has been critical for over 20 s (' + r.gpuTempC.toFixed(1) + '°C) with no open incident' }; }
    else { delete S.rows[key]; if (S.findings[key] && !S.findings[key].cleared) S.findings[key].cleared = now; }
  }
  // freshness
  if (S.lastSeqAt && now - S.lastSeqAt > 10) { if (!S.findings.stale) S.findings.stale = { key: 'stale', severity: 'medium', ts: now, text: 'facility feed stopped at seq ' + S.seq + ' (no update for ' + Math.round(now - S.lastSeqAt) + ' s)' }; }
  else if (S.findings.stale && !S.findings.stale.cleared) S.findings.stale.cleared = now;
  S.subscribers = [...conn.db.connection.iter()].length;
}
const execText = (exe, pid, ver) => 'Execution ' + exe + ' of ' + pid + ' v' + ver;

async function report() {
  try { await fetch(API + '/api/stdb/watcher', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Sender-Uid': UID }, body: JSON.stringify({ ...S, findings: Object.values(S.findings), rows: undefined }) }); } catch (e) { }
}

function connect() {
  let token; try { token = fs.readFileSync(TOKEN_FILE, 'utf8').trim() || undefined; } catch (e) { }
  S.status = 'connecting';
  conn = DbConnection.builder().withUri('wss://maincloud.spacetimedb.com').withDatabaseName('gridpilot').withToken(token)
    .onConnect((c, identity, tok) => {
      retry = 0; S.identity = identity.toHexString(); try { fs.writeFileSync(TOKEN_FILE, tok, { mode: 0o600 }); } catch (e) { }
      c.db.facilitySummary.onUpdate((ctx, o, n) => { S.seq = Number(n.seq); S.lastSeqAt = Date.now() / 1000; });
      c.db.facilitySummary.onInsert((ctx, n) => { S.seq = Number(n.seq); S.lastSeqAt = Date.now() / 1000; });
      c.subscriptionBuilder().onApplied(async () => {
        S.status = 'live'; verify();
        try { await c.reducers.probeWrite({}); S.probe = { ok: false, at: Date.now() / 1000, result: 'ACCEPTED: this identity can write (guard broken)' }; S.findings.probe = { key: 'probe', severity: 'high', ts: Date.now() / 1000, text: 'write probe was accepted for an anonymous identity' }; }
        catch (e) { S.probe = { ok: true, at: Date.now() / 1000, result: 'rejected: ' + String(e && e.message || e).slice(0, 160) }; }
        report();
      }).onError((ctx, e) => { S.status = 'error: ' + String(e && e.message || e).slice(0, 120); })
        .subscribe(['SELECT * FROM facility_summary', 'SELECT * FROM incident', 'SELECT * FROM plan', 'SELECT * FROM audit_event', 'SELECT * FROM rack', 'SELECT * FROM connection']);
    })
    .onConnectError((ctx, e) => { S.status = 'reconnecting'; setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++)); })
    .onDisconnect(() => { S.status = 'reconnecting'; setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++)); })
    .build();
}
connect();
setInterval(verify, 2000);
setInterval(report, 5000);
