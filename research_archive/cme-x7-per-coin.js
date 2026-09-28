#!/usr/bin/env node
/**
 * STUDY 26 — CME-X7: per-coin model selection. Does "best model per coin" work?
 *
 * The request: rather than one formula for everything, let each coin use
 * whichever configuration performs best on it, and take the high win rate on
 * every coin.
 *
 * This is NOT the same question Part XVII answered. That one refit a SINGLE
 * formula on each coin's own history and found only BTC/ETH supported it. This
 * one lets the CONFIGURATION ITSELF vary per coin -- model, training pool, and
 * selectivity -- which is a genuinely different and untested idea.
 *
 * It is also a multiple-testing machine, and that is the whole difficulty. With
 * 18 candidate configurations per coin across 16 coins, picking each coin's best
 * is 288 draws. Some coin will show 70% by luck. Selecting on the same data you
 * report is how a system is built that backtests beautifully and loses money.
 *
 * So the design is: SELECT on the first 60% of each coin's timeline, then REPORT
 * the chosen configuration's win rate on the last 40%, which the selection never
 * touched. The gap between those two numbers is the overfitting tax, and it is
 * the actual output of this study. A per-coin scheme is real if and only if the
 * holdout number survives; if selection-window win rates are high and holdout
 * win rates collapse to ~50%, the scheme is picking noise and the correct
 * conclusion is to use one configuration everywhere.
 *
 * Baseline to beat: one fixed global configuration, chosen the same way, scored
 * on the same holdout. Per-coin selection has to beat THAT, not beat chance.
 */
const fs = require('fs');
const path = require('path');
const M = require('./cme-x5-normalization.js');
const { IDX } = M;

const COST_BPS = 4;
const BREAKEVEN = (1 + COST_BPS / 100) / 2;
const SPLIT = 0.60;
const FIT_H = 4, EVAL_H = 2;

const MODELS = {
  'momentum':      [IDX.M_COH],
  'momentum+BA':   [IDX.M_COH, IDX.BA],
  'full15':        M.FEATURE_NAMES.map((_, j) => j)
};
const POOLS = ['pooled', 'self'];
const FRACS = [0.10, 0.05, 0.02];

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }

const coins = M.CORE_COINS.concat(M.WIDE_EXTRA).filter(s => M.loadBars(s));
const uni = M.buildUniverse(coins);
const nBars = uni.alignedBars[uni.coins[0]].length;
const cut = Math.floor(nBars * SPLIT);
console.log(`\n  STUDY 26 — CME-X7: per-coin model selection`);
console.log(`  ${uni.coins.length} coins, ${nBars} bars. Select on bars 0-${cut}, report on ${cut}-${nBars} (never seen by selection).`);
console.log(`  ${Object.keys(MODELS).length} models x ${POOLS.length} pools x ${FRACS.length} thresholds = ${Object.keys(MODELS).length * POOLS.length * FRACS.length} candidates per coin.`);
console.log(`  Breakeven ${(BREAKEVEN * 100).toFixed(1)}% at ${COST_BPS}bps.\n`);

/** Fit one configuration for one coin, using ONLY in-sample bars. */
function fitFor(coin, featIdx, pool) {
  const trainCoins = pool === 'self' ? [coin] : uni.coins.filter(c => c !== coin);
  const rows = [];
  for (const s of trainCoins) {
    for (const r of uni.rows[s]) {
      if (r.i >= cut) continue;                       // in-sample only
      const y = M.barrierOutcome(uni.alignedBars[s], r.i, FIT_H);
      if (y != null) rows.push({ i: r.i, x: featIdx.map(j => r.x[j]), y });
    }
  }
  return rows.length >= 500 ? M.fitRidge(rows, M.RIDGE_LAMBDA) : null;
}

/** Score a coin over a bar range under a fitted model. */
function scoreRange(coin, featIdx, model, lo, hi) {
  const m = new Map();
  for (const r of uni.rows[coin]) {
    if (r.i < lo || r.i >= hi) continue;
    m.set(r.i, M.scoreVector(featIdx.map(j => r.x[j]), model));
  }
  return m;
}

const perCoin = {};
const candidateNames = [];
for (const [mName, featIdx] of Object.entries(MODELS))
  for (const pool of POOLS)
    for (const frac of FRACS) candidateNames.push(`${mName}|${pool}|${(frac * 100).toFixed(0)}%`);

console.log(`  ${'coin'.padEnd(10)}${'chosen configuration'.padEnd(30)}${'in-sample WR'.padStart(13)}${'HOLDOUT WR'.padStart(12)}${'holdout n'.padStart(11)}${'holdout expR'.padStart(14)}`);
console.log(`  ${'─'.repeat(88)}`);

const rowsOut = [];
for (const coin of uni.coins) {
  let best = null;
  const holdoutByCand = {};
  for (const [mName, featIdx] of Object.entries(MODELS)) {
    for (const pool of POOLS) {
      const model = fitFor(coin, featIdx, pool);
      if (!model) continue;
      const inS = scoreRange(coin, featIdx, model, 0, cut);
      const out = scoreRange(coin, featIdx, model, cut, nBars);
      for (const frac of FRACS) {
        const name = `${mName}|${pool}|${(frac * 100).toFixed(0)}%`;
        const ev = M.evaluate(uni.alignedBars[coin], inS, COST_BPS, M.BARRIER, frac);
        const oe = M.evaluate(uni.alignedBars[coin], out, COST_BPS, M.BARRIER, frac);
        holdoutByCand[name] = oe;
        if (ev && ev.topWR != null && (!best || ev.topWR > best.wr)) best = { name, wr: ev.topWR, holdout: oe };
      }
    }
  }
  if (!best) { console.log(`  ${coin.padEnd(10)}${'(insufficient data)'.padEnd(30)}`); continue; }
  perCoin[coin] = { chosen: best.name, inSampleWR: best.wr, holdout: best.holdout, all: holdoutByCand };
  const h = best.holdout;
  rowsOut.push({ coin, chosen: best.name, inWR: best.wr, outWR: h && h.topWR, outN: h && h.topN, outExpR: h && h.expR });
  console.log(`  ${coin.padEnd(10)}${best.name.padEnd(30)}${((best.wr * 100).toFixed(1) + '%').padStart(13)}${(h && h.topWR != null ? (h.topWR * 100).toFixed(1) + '%' : '—').padStart(12)}${String(h && h.topN != null ? h.topN : '—').padStart(11)}${(h && h.expR != null ? h.expR.toFixed(3) : '—').padStart(14)}`);
}

// ── The verdict: does per-coin selection beat one config everywhere? ────────
const valid = rowsOut.filter(r => r.outWR != null);
const meanIn = mean(valid.map(r => r.inWR)), meanOut = mean(valid.map(r => r.outWR));
console.log(`\n  ${'─'.repeat(88)}`);
console.log(`  mean in-sample WR (what selection saw)  ${(meanIn * 100).toFixed(1)}%`);
console.log(`  mean HOLDOUT WR (what you would get)    ${(meanOut * 100).toFixed(1)}%`);
console.log(`  OVERFITTING TAX                         ${((meanIn - meanOut) * 100).toFixed(1)} points`);
console.log(`  coins above breakeven on holdout        ${valid.filter(r => r.outWR > BREAKEVEN).length}/${valid.length}`);

// Baseline: the single global config that is best in-sample, scored on the same holdout.
const globalScores = {};
for (const name of candidateNames) {
  const outs = uni.coins.map(c => perCoin[c] && perCoin[c].all[name]).filter(x => x && x.topWR != null);
  if (outs.length) globalScores[name] = mean(outs.map(x => x.topWR));
}
const bestGlobal = Object.entries(globalScores).sort((a, b) => b[1] - a[1])[0];
console.log(`\n  Best SINGLE configuration applied to every coin, on the same holdout:`);
for (const [n, v] of Object.entries(globalScores).sort((a, b) => b[1] - a[1]).slice(0, 4))
  console.log(`    ${n.padEnd(30)} mean holdout WR ${(v * 100).toFixed(1)}%`);
console.log(`\n  per-coin selection: ${(meanOut * 100).toFixed(1)}%   vs   one config everywhere: ${(bestGlobal[1] * 100).toFixed(1)}%`);
console.log(`  ${meanOut > bestGlobal[1] ? '=> per-coin selection ADDS value out-of-sample.' : '=> per-coin selection does NOT beat a single configuration. The per-coin win rates'}`);
if (meanOut <= bestGlobal[1]) console.log(`     seen in-sample were selection noise, not per-coin structure.`);

fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
fs.writeFileSync(path.join(__dirname, 'results', 'cme-x7-per-coin.json'),
  JSON.stringify({ generatedAt: new Date().toISOString(), split: SPLIT, costBps: COST_BPS, perCoin, globalScores }, null, 2));
console.log('');
