#!/usr/bin/env node
/**
 * STUDY 25 — CME-X6: can the win rate be raised WITHOUT a new formula?
 *
 * The user's question: "can we increase the rate by inventing more formulas
 * instead of depending on one?" Parts XIV-XVIII say inventing more formulas of
 * the same kind does not work -- fifteen hand-built features lost to one. This
 * tests the two levers that need no new formula at all, on the model Part
 * XVIII left standing (momentum only):
 *
 *   LEVER 1 — SELECTIVITY. Every number in Parts XIV-XVIII came from the top
 *     DECILE of scored bars. Nothing forces that. Tightening the threshold
 *     should trade volume for win rate; the question is the exchange rate, and
 *     whether it holds up or just thins the sample until noise takes over.
 *
 *   LEVER 2 — THE MOMENTUM-SIGN GATE. Part XVII found the edge lives entirely
 *     in positive-momentum states (BTC 62.0% WR) and is absent when momentum is
 *     negative (47.8%, AUC 0.502). That gate was never actually applied. It is
 *     free: it uses a feature already in the model.
 *
 * Both are evaluated leave-one-asset-out across the 16-coin universe so the
 * numbers are comparable to Part XVIII's, and reported against the 52.0%
 * breakeven that 4bps of cost implies on a symmetric +/-0.5% barrier -- NOT
 * against 50%. A win rate that rises while expectancy falls is not an
 * improvement, so expR and trade count are reported alongside every rate.
 */
const M = require('./cme-x5-normalization.js');
const { IDX, BARRIER, FIT_HORIZON, EVAL_HORIZON, RIDGE_LAMBDA } = M;

const COST_BPS = 4;
const BREAKEVEN = (1 + COST_BPS / 100) / 2;
const FRACS = [0.10, 0.05, 0.02, 0.01];
const FEAT = [IDX.M_COH];

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }

const coins = M.CORE_COINS.concat(M.WIDE_EXTRA).filter(s => M.loadBars(s));
const uni = M.buildUniverse(coins);
const liq = uni.cross.medDollarVol;
const ranked = uni.coins.slice().sort((a, b) => liq[b] - liq[a]);
console.log(`\n  STUDY 25 — CME-X6: raising the rate without a new formula`);
console.log(`  ${uni.coins.length} coins, ${uni.alignedBars[uni.coins[0]].length} aligned bars, momentum-only model, ${COST_BPS}bps`);
console.log(`  Breakeven win rate = ${(BREAKEVEN * 100).toFixed(1)}% (symmetric ±0.5% barrier + cost). Below that, a higher rate still loses.\n`);

/** Leave-one-asset-out fit, returning per-coin scores so thresholds can be swept cheaply. */
function looScores(gatePositiveMomentum) {
  const out = {};
  for (const held of ranked) {
    const trainRows = [];
    for (const s of uni.coins) {
      if (s === held) continue;
      for (const r of uni.rows[s]) {
        if (gatePositiveMomentum && r.raw.M <= 0) continue;
        const y = M.barrierOutcome(uni.alignedBars[s], r.i, FIT_HORIZON);
        if (y != null) trainRows.push({ i: r.i, x: FEAT.map(j => r.x[j]), y });
      }
    }
    if (trainRows.length < 500) continue;
    const model = M.fitRidge(trainRows, RIDGE_LAMBDA);
    const m = new Map();
    for (const r of uni.rows[held]) {
      if (gatePositiveMomentum && r.raw.M <= 0) continue;   // gate applies at decision time too
      m.set(r.i, M.scoreVector(FEAT.map(j => r.x[j]), model));
    }
    out[held] = m;
  }
  return out;
}

for (const gate of [false, true]) {
  console.log(`  ${'─'.repeat(104)}`);
  console.log(`  ${gate ? 'LEVER 2 — positive-momentum gate APPLIED (Part XVII\'s untested finding)' : 'LEVER 1 — selectivity sweep, no gate (Part XVIII\'s model as-is)'}`);
  console.log(`  ${'─'.repeat(104)}`);
  const scores = looScores(gate);
  console.log(`  ${'top%'.padStart(6)}  ${'BTC WR'.padStart(8)}${'BTC n'.padStart(8)}${'ETH WR'.padStart(9)}${'ETH n'.padStart(8)}   ${'top5 WR'.padStart(8)}${'top5 expR'.padStart(11)}${'all16 WR'.padStart(10)}${'#>BE'.padStart(6)}`);
  for (const frac of FRACS) {
    const per = {};
    for (const s of ranked) {
      if (!scores[s]) continue;
      per[s] = M.evaluate(uni.alignedBars[s], scores[s], COST_BPS, BARRIER, frac);
    }
    const ok = s => per[s] && per[s].topWR != null;
    const top5 = ranked.slice(0, 5).filter(ok), all = ranked.filter(ok);
    const nBE = all.filter(s => per[s].topWR > BREAKEVEN).length;
    const f = (s, k) => ok(s) ? (k === 'wr' ? (per[s].topWR * 100).toFixed(1) + '%' : String(per[s].topN)) : '—';
    console.log(`  ${(frac * 100).toFixed(1).padStart(5)}%  ${f('BTCUSDT', 'wr').padStart(8)}${f('BTCUSDT', 'n').padStart(8)}${f('ETHUSDT', 'wr').padStart(9)}${f('ETHUSDT', 'n').padStart(8)}   ${((mean(top5.map(s => per[s].topWR)) * 100).toFixed(1) + '%').padStart(8)}${mean(top5.map(s => per[s].expR)).toFixed(3).padStart(11)}${((mean(all.map(s => per[s].topWR)) * 100).toFixed(1) + '%').padStart(10)}${String(nBE + '/' + all.length).padStart(6)}`);
  }
  console.log('');
}
console.log(`  Read: a lever works only if win rate rises AND expR stays positive AND n stays large enough`);
console.log(`  to trust. A rate that climbs while n collapses is the threshold selecting noise, not signal.\n`);
