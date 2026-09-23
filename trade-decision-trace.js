/**
 * Trade Decision Trace
 *
 * One trace ID follows a candidate through:
 * HTF/strategy -> Archeologist -> Red Team -> Supervisor -> Risk -> Execution.
 *
 * This module is intentionally framework-agnostic so the existing execution
 * path can call it without changing the decision semantics.
 */

const crypto = require('crypto');

const TERMINAL = new Set(['VETO', 'BLOCK', 'SUBMITTED', 'FILLED', 'CLOSED', 'EXPIRED']);
const traces = new Map();
const MAX_TRACES = 2000;
const TRACE_TTL_MS = 24 * 60 * 60 * 1000;

function makeTraceId({ symbol, direction, strategy = 'UNKNOWN' }) {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
  const suffix = crypto.randomBytes(3).toString('hex').toUpperCase();
  return `${strategy}-${symbol}-${direction}-${stamp}-${suffix}`;
}

function createTrace(candidate = {}) {
  const traceId = makeTraceId(candidate);
  const now = Date.now();
  const trace = {
    traceId,
    candidateId: candidate.candidateId || traceId,
    symbol: candidate.symbol,
    direction: String(candidate.direction || '').toUpperCase(),
    strategy: candidate.strategy || candidate.setupType || 'UNKNOWN',
    strategyMode: candidate.strategyMode || 'unknown',
    entry: Number(candidate.entry) || null,
    stop: Number(candidate.stop || candidate.stopLoss) || null,
    targets: Array.isArray(candidate.takeProfit) ? candidate.takeProfit : [],
    createdAt: now,
    updatedAt: now,
    terminal: false,
    stages: {
      candidate: { status: 'PASS', startedAt: now, completedAt: now },
      archaeologist: { status: 'PENDING' },
      redTeam: { status: 'PENDING' },
      supervisor: { status: 'PENDING' },
      risk: { status: 'PENDING' },
      execution: { status: 'PENDING' }
    },
    events: []
  };
  traces.set(traceId, trace);
  prune();
  return trace;
}

function updateStage(traceId, stage, status, details = {}) {
  const trace = traces.get(traceId);
  if (!trace) return null;
  const key = stage === 'redteam' ? 'redTeam' : stage;
  const now = Date.now();
  const current = trace.stages[key] || {};
  trace.stages[key] = {
    ...current,
    ...details,
    status: String(status || 'UNKNOWN').toUpperCase(),
    completedAt: ['PASS', 'CONFIRM', 'VETO', 'BLOCK', 'SUBMITTED', 'FILLED', 'CLOSED', 'SKIPPED', 'ERROR'].includes(String(status || '').toUpperCase()) ? now : current.completedAt
  };
  trace.updatedAt = now;
  trace.terminal = TERMINAL.has(trace.stages[key].status);
  trace.events.push({ at: now, stage: key, status: trace.stages[key].status, ...details });
  prune();
  return trace;
}

function getTrace(traceId) {
  return traces.get(traceId) || null;
}

function listTraces(limit = 100) {
  return [...traces.values()]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, Math.max(1, Math.min(500, Number(limit) || 100)));
}

function status(traceId) {
  const t = getTrace(traceId);
  if (!t) return null;
  return {
    traceId: t.traceId,
    symbol: t.symbol,
    direction: t.direction,
    strategy: t.strategy,
    strategyMode: t.strategyMode,
    stages: t.stages,
    terminal: t.terminal,
    updatedAt: t.updatedAt
  };
}

function prune() {
  const cutoff = Date.now() - TRACE_TTL_MS;
  for (const [id, trace] of traces) {
    if (trace.updatedAt < cutoff) traces.delete(id);
  }
  while (traces.size > MAX_TRACES) {
    const oldest = traces.keys().next().value;
    traces.delete(oldest);
  }
}

module.exports = {
  createTrace,
  updateStage,
  getTrace,
  listTraces,
  status
};
