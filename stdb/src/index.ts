// GRIDPILOT SpacetimeDB module (maincloud/gridpilot).
// Live state the dashboard subscribes to. The Python backend worker (thermal model, CREAO, Cartesia, git config store)
// is the only writer: every mutating reducer checks the caller against the `writer` table, which only the database
// owner can change. Visitors connect anonymously and get read-only access (public tables, no write rights).
import { schema, table, t, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

const PlanAction = t.object('PlanAction', {
  kind: t.string(),          // migrate_workload | set_gpu_power_limit | set_cooling_fan
  workload: t.string(),
  fromRack: t.string(),
  toRack: t.string(),
  rack: t.string(),
  watts: t.f64(),
  fromWatts: t.f64(),
  unit: t.string(),
  fanPct: t.f64(),
  fromPct: t.f64(),
  text: t.string(),
});
const Migration = t.object('Migration', { workload: t.string(), fromRack: t.string(), toRack: t.string(), progress: t.f64() });

const rack = table({ name: 'rack', public: true }, {
  rackId: t.string().primaryKey(),
  idx: t.u32(),
  row: t.u32(),
  col: t.u32(),
  gpuCount: t.u32(),
  utilization: t.f64(),
  powerKw: t.f64(),
  gpuTempC: t.f64(),
  inletTempC: t.f64(),
  exhaustTempC: t.f64(),
  airflowM3s: t.f64(),
  coolingEfficiency: t.f64(),
  powerLimitW: t.f64(),
  zone: t.string(),
  reservePool: t.bool(),
  workloads: t.array(t.string()),
  workloadP95Ms: t.array(t.f64()),     // parallel to workloads
  workloadLimitMs: t.array(t.f64()),
  workloadProtected: t.array(t.bool()),
  status: t.string(),
  simT: t.f64(),
});

const coolingUnit = table({ name: 'cooling_unit', public: true }, {
  unitId: t.string().primaryKey(),
  idx: t.u32(),
  fanPct: t.f64(),
  effectiveness: t.f64(),
  flowFactor: t.f64(),
  supplyTempC: t.f64(),
  returnTempC: t.f64(),
  aisleSupplyM3s: t.f64(),
  aisleDemandM3s: t.f64(),
  aisleDeficit: t.f64(),
  status: t.string(),        // nominal | degraded | failed
  simT: t.f64(),
});

const facilitySummary = table({ name: 'facility_summary', public: true }, {
  id: t.u32().primaryKey(),  // always 1
  simT: t.f64(),
  wallTs: t.f64(),           // backend wall clock (epoch s) when published
  itKw: t.f64(),
  facilityKw: t.f64(),
  pue: t.f64(),
  gpuUtil: t.f64(),
  maxInletC: t.f64(),
  criticalRacks: t.u32(),
  elevatedRacks: t.u32(),
  slaBreaches: t.u32(),
  latencyP95Ms: t.f64(),
  electricityUsd: t.f64(),
  transferUsd: t.f64(),
  health: t.string(),
  speed: t.f64(),
  paused: t.bool(),
  migrations: t.array(Migration),
  seq: t.u64(),
});

// Coarse 2D thermal/airflow field (32 x 68 cells) for the heatmap and particles. Single row.
const thermalField = table({ name: 'thermal_field', public: true }, {
  id: t.u32().primaryKey(),
  simT: t.f64(),
  nx: t.u32(),
  nz: t.u32(),
  temp: t.array(t.f32()),
  u: t.array(t.f32()),
  w: t.array(t.f32()),
});

const incident = table({ name: 'incident', public: true }, {
  incidentId: t.string().primaryKey(),
  state: t.string(),
  severity: t.string(),
  rack: t.string(),
  title: t.string(),
  probableCause: t.string(),
  detectedSimT: t.f64(),
  createdTs: t.f64(),
  updatedTs: t.f64(),
  planId: t.string(),
  planVersion: t.u32(),
  mttrSimS: t.f64(),         // -1 until resolved
  summary: t.string(),
});

const plan = table({ name: 'plan', public: true }, {
  planKey: t.string().primaryKey(),   // PLN-045:1
  planId: t.string(),
  incidentId: t.string().index('btree'),
  version: t.u32(),
  status: t.string(),
  author: t.string(),
  actions: t.array(PlanAction),
  recoverS: t.f64(),         // -1 = no recovery within the forecast horizon
  finalGpuC: t.f64(),
  incrementalCostUsd: t.f64(),
  slaExposureUsd: t.f64(),
  baselineSlaUsd: t.f64(),
  operationalRisk: t.string(),
  policyDecision: t.string(),
  policyRules: t.array(t.string()),
  reasoning: t.string(),
  confidence: t.f64(),       // -1 = not given
  createdTs: t.f64(),
});

const auditEvent = table({ name: 'audit_event', public: true }, {
  eventId: t.u64().primaryKey(),      // backend events.id (append-only audit log stays in the backend DB)
  incidentId: t.string().index('btree'),
  ts: t.f64(),
  simT: t.f64(),
  kind: t.string(),
  actor: t.string(),
  summary: t.string(),
  detail: t.string(),        // opaque JSON payload of the audit record, kept verbatim
});

const writer = table({ name: 'writer' }, {     // private: who may write
  identity: t.identity().primaryKey(),
  label: t.string(),
  grantedBy: t.identity(),
  grantedAt: t.timestamp(),
});
const admin = table({ name: 'admin' }, {       // private: database owner, captured at publish
  identity: t.identity().primaryKey(),
});

// Who is subscribed right now: one row per connected identity (dashboards, agents). Maintained by the lifecycle reducers below.
const connection = table({ name: 'connection', public: true }, {
  identity: t.identity().primaryKey(),
  connectedAt: t.timestamp(),
  writer: t.bool(),
});

const spacetimedb = schema({ rack, coolingUnit, facilitySummary, thermalField, incident, plan, auditEvent, writer, admin, connection });
export default spacetimedb;

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;
const AUDIT_KEEP = 600;

function requireWriter(ctx: Ctx) {
  if (!ctx.db.writer.identity.find(ctx.sender)) throw new SenderError('read-only: this identity is not an authorized GridPilot writer');
}
function requireAdmin(ctx: Ctx) {
  if (!ctx.db.admin.identity.find(ctx.sender)) throw new SenderError('only the database owner can manage writers');
}
function upsertRack(ctx: Ctx, r: any) { if (ctx.db.rack.rackId.find(r.rackId)) ctx.db.rack.rackId.update(r); else ctx.db.rack.insert(r); }
function upsertUnit(ctx: Ctx, r: any) { if (ctx.db.coolingUnit.unitId.find(r.unitId)) ctx.db.coolingUnit.unitId.update(r); else ctx.db.coolingUnit.insert(r); }
function upsertIncident(ctx: Ctx, r: any) { if (ctx.db.incident.incidentId.find(r.incidentId)) ctx.db.incident.incidentId.update(r); else ctx.db.incident.insert(r); }
function upsertPlan(ctx: Ctx, r: any) { if (ctx.db.plan.planKey.find(r.planKey)) ctx.db.plan.planKey.update(r); else ctx.db.plan.insert(r); }

export const init = spacetimedb.init(ctx => {
  ctx.db.admin.insert({ identity: ctx.sender });
  ctx.db.writer.insert({ identity: ctx.sender, label: 'owner', grantedBy: ctx.sender, grantedAt: ctx.timestamp });
});

export const grantWriter = spacetimedb.reducer({ identity: t.identity(), label: t.string() }, (ctx, { identity, label }) => {
  requireAdmin(ctx);
  const row = { identity, label, grantedBy: ctx.sender, grantedAt: ctx.timestamp };
  if (ctx.db.writer.identity.find(identity)) ctx.db.writer.identity.update(row); else ctx.db.writer.insert(row);
});

export const revokeWriter = spacetimedb.reducer({ identity: t.identity() }, (ctx, { identity }) => {
  requireAdmin(ctx);
  ctx.db.writer.identity.delete(identity);
});

// One call per publish interval (1 Hz): the whole physical snapshot plus any incident/plan/audit changes since last tick.
export const publishTick = spacetimedb.reducer({
  facility: facilitySummary.rowType,
  racks: t.array(rack.rowType),
  cooling: t.array(coolingUnit.rowType),
  field: t.array(thermalField.rowType),      // 0 or 1 rows
  incidents: t.array(incident.rowType),
  plans: t.array(plan.rowType),
  events: t.array(auditEvent.rowType),
}, (ctx, a) => {
  requireWriter(ctx);
  if (ctx.db.facilitySummary.id.find(1)) ctx.db.facilitySummary.id.update({ ...a.facility, id: 1 }); else ctx.db.facilitySummary.insert({ ...a.facility, id: 1 });
  for (const r of a.racks) upsertRack(ctx, r);
  for (const u of a.cooling) upsertUnit(ctx, u);
  for (const f of a.field) { if (ctx.db.thermalField.id.find(1)) ctx.db.thermalField.id.update({ ...f, id: 1 }); else ctx.db.thermalField.insert({ ...f, id: 1 }); }
  for (const i of a.incidents) upsertIncident(ctx, i);
  for (const p of a.plans) upsertPlan(ctx, p);
  let newest = 0n;
  for (const e of a.events) { if (!ctx.db.auditEvent.eventId.find(e.eventId)) ctx.db.auditEvent.insert(e); if (e.eventId > newest) newest = e.eventId; }
  if (newest > BigInt(AUDIT_KEEP)) {
    const cutoff = newest - BigInt(AUDIT_KEEP);
    for (const e of [...ctx.db.auditEvent.iter()]) if (e.eventId < cutoff) ctx.db.auditEvent.eventId.delete(e.eventId);
  }
});

// Demo reset: the backend clears incidents/plans/audit mirrors when the simulation is reset.
export const clearOperational = spacetimedb.reducer(ctx => {
  requireWriter(ctx);
  for (const r of [...ctx.db.incident.iter()]) ctx.db.incident.incidentId.delete(r.incidentId);
  for (const r of [...ctx.db.plan.iter()]) ctx.db.plan.planKey.delete(r.planKey);
  for (const r of [...ctx.db.auditEvent.iter()]) ctx.db.auditEvent.eventId.delete(r.eventId);
});

export const onClientConnected = spacetimedb.clientConnected(ctx => {
  const row = { identity: ctx.sender, connectedAt: ctx.timestamp, writer: !!ctx.db.writer.identity.find(ctx.sender) };
  if (ctx.db.connection.identity.find(ctx.sender)) ctx.db.connection.identity.update(row); else ctx.db.connection.insert(row);
});

export const onClientDisconnected = spacetimedb.clientDisconnected(ctx => {
  ctx.db.connection.identity.delete(ctx.sender);
});

// Harmless write-permission probe: succeeds only for authorized writers and changes nothing. Read-only agents call it to prove they cannot write.
export const probeWrite = spacetimedb.reducer(ctx => {
  requireWriter(ctx);
});
