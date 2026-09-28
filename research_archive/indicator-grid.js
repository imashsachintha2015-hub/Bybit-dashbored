#!/usr/bin/env node
/**
 * STUDY 9 — Test all possibilities.
 *
 * A systematic grid over custom indicators, parameters, trade direction
 * (momentum vs reversion) and holding period, across six symbols. Several
 * thousand strategy configurations, each measured for BOTH win rate and
 * expectancy, so the relationship between the two can be seen at scale rather
 * than argued about.
 *
 * The question it exists to answer: "we always end up at 41-47%; can a
 * different indicator, or a longer hold, or the opposite direction, get us to
 * 53-59%?"
 *
 * The answer the grid gives is specific: yes, easily, and it does not help.
 * High-win-rate configurations exist in abundance, and they are systematically
 * the ones with the worst payoff ratio. The grid prints the best configurations
 * ranked by win rate and separately by expectancy so the two lists can be
 * compared directly. They have almost no overlap, and that is the finding.
 *
 * Every test is subject to the same four gates used throughout: HAC standard
 * errors, FDR across the whole grid, split-sample validation, and a cost floor.
 * With this many tests, FDR is not optional — at 2,000 tests and a 5% level,
 * 100 configurations would look significant with nothing there at all.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');

// ── Indicator library. Each returns a z-like score: positive = bullish. ──
function ema(vals, p) {
  const k = 2 / (p + 1);
  let e = vals[0];
  for (let i = 1; i < vals.length; i++) e = vals[i] * k + e * (1 - k);
  return e;
}
function sd(a) {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

const INDICATORS = {
  /** Rate of change, normalised by recent volatility. */
  roc: (b, i, p) => {
    const past = b[i - p];
    if (!past) return null;
    const rets = [];
    for (let k = i - p; k < i; k++) rets.push(Math.log(b[k + 1].close / b[k].close));
    const v = sd(rets);
    return v ? Math.log(b[i].close / past.close) / (v * Math.sqrt(p)) : null;
  },
  /** Distance from an EMA, in ATR units. */
  emaDist: (b, i, p) => {
    if (i < p + 2) return null;
    const e = ema(b.slice(i - p, i + 1).map(c => c.close), p);
    let tr = 0;
    for (let k = i - 14; k < i; k++) tr += Math.max(b[k].high - b[k].low, Math.abs(b[k].high - b[k - 1].close), Math.abs(b[k].low - b[k - 1].close));
    const a = tr / 14;
    return a ? (b[i].close - e) / a : null;
  },
  /** RSI, recentred so 0 is neutral. */
  rsi: (b, i, p) => {
    if (i < p + 1) return null;
    let g = 0, l = 0;
    for (let k = i - p + 1; k <= i; k++) {
      const d = b[k].close - b[k - 1].close;
      if (d > 0) g += d; else l -= d;
    }
    if (g + l === 0) return 0;
    return ((g / (g + l)) * 100 - 50) / 10;
  },
  /** Position within a Bollinger band (in sigma). */
  bollinger: (b, i, p) => {
    if (i < p) return null;
    const w = b.slice(i - p, i).map(c => c.close);
    const m = w.reduce((x, y) => x + y, 0) / w.length;
    const s = sd(w);
    return s ? (b[i].close - m) / s : null;
  },
  /** Position within the Donchian channel, -1..+1. */
  donchian: (b, i, p) => {
    if (i < p) return null;
    const w = b.slice(i - p, i);
    const hi = Math.max(...w.map(c => c.high)), lo = Math.min(...w.map(c => c.low));
    return hi > lo ? ((b[i].close - lo) / (hi - lo) - 0.5) * 2 : null;
  },
  /** Fast/slow EMA spread, in ATR units. */
  emaCross: (b, i, p) => {
    if (i < p * 3 + 2) return null;
    const closes = b.slice(i - p * 3, i + 1).map(c => c.close);
    const f = ema(closes, p), sl = ema(closes, p * 3);
    let tr = 0;
    for (let k = i - 14; k < i; k++) tr += Math.max(b[k].high - b[k].low, Math.abs(b[k].high - b[k - 1].close), Math.abs(b[k].low - b[k - 1].close));
    const a = tr / 14;
    return a ? (f - sl) / a : null;
  },
  /** Volume-weighted push: where the bar closed in its range, scaled by volume. */
  volPush: (b, i, p) => {
    if (i < p) return null;
    const w = b.slice(i - p, i);
    const avgV = w.reduce((x, c) => x + c.volume, 0) / w.length;
    const bar = b[i];
    const rng = bar.high - bar.low;
    if (!rng || !avgV) return null;
    return (((bar.close - bar.low) / rng - 0.5) * 2) * (bar.volume / avgV);
  },
  /** Kaufman efficiency ratio, signed by direction. */
  efficiency: (b, i, p) => {
    if (i < p + 1) return null;
    const net = b[i].close - b[i - p].close;
    let pathLen = 0;
    for (let k = i - p; k < i; k++) pathLen += Math.abs(b[k + 1].close - b[k].close);
    return pathLen ? (net / pathLen) * 3 : null;
  },
  /** Accelerating momentum: recent half vs prior half. */
  accel: (b, i, p) => {
    if (i < p * 2 + 1) return null;
    const r1 = Math.log(b[i].close / b[i - p].close);
    const r2 = Math.log(b[i - p].close / b[i - p * 2].close);
    const rets = [];
    for (let k = i - p * 2; k < i; k++) rets.push(Math.log(b[k + 1].close / b[k].close));
    const v = sd(rets);
    return v ? (r1 - r2) / (v * Math.sqrt(p)) : null;
  },
  /** Range compression: current range vs its own recent norm (unsigned, so it
   *  is direction-agnostic and acts as a filter rather than a signal). */
  compression: (b, i, p) => {
    if (i < p * 2) return null;
    const recent = b.slice(i - p, i);
    const prior = b.slice(i - p * 2, i - p);
    const r1 = recent.reduce((x, c) => x + (c.high - c.low), 0) / recent.length;
    const r2 = prior.reduce((x, c) => x + (c.high - c.low), 0) / prior.length;
    return r2 ? (r2 - r1) / r2 * 3 : null;
  }
};

const PARAMS = { roc: [5, 14, 40], emaDist: [10, 21, 50], rsi: [7, 14, 28], bollinger: [20, 50],
  donchian: [20, 55], emaCross: [5, 12], volPush: [20, 50], efficiency: [10, 30], accel: [5, 20],
  compression: [10, 30] };

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';
  const costBps = parseFloat(args.cost || '15');
  const thresholds = [0.5, 1.0, 1.75];
  // Holding periods in bars. On 15m bars: 1 bar = 15 min, 4 = 1h, 96 = 1 day.
  const holds = [1, 2, 4, 16, 48, 96];

  const symbols = fs.readdirSync(DATA).filter(f => f.endsWith('.json') && !f.includes('-derivs'))
    .map(f => f.replace('.json', ''));

  const line = '─'.repeat(100);
  console.log(`\n${line}\n  STUDY 9 — FULL GRID: every indicator x parameter x threshold x direction x holding period\n${line}`);

  const tests = [];
  let symbolsUsed = 0;

  for (const sym of symbols) {
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA, `${sym}.json`), 'utf8'));
    const bars = bundle.series[tf];
    if (!bars || bars.length < 5000) continue;
    symbolsUsed++;

    // Pre-compute every indicator series once.
    const series = {};
    for (const [name, fn] of Object.entries(INDICATORS)) {
      for (const p of PARAMS[name]) {
        const key = `${name}_${p}`;
        const arr = new Array(bars.length).fill(null);
        for (let i = 200; i < bars.length; i++) arr[i] = fn(bars, i, p);
        series[key] = arr;
      }
    }

    const mid = Math.floor(bars.length / 2);

    for (const [key, arr] of Object.entries(series)) {
      for (const th of thresholds) {
        for (const mode of ['momentum', 'reversion']) {
          for (const hold of holds) {
            const rs = [], first = [], second = [];
            let wins = 0, grossWins = 0;
            for (let i = 200; i < bars.length - hold - 1; i += 1) {
              const v = arr[i];
              if (v == null || Math.abs(v) < th) continue;
              const dir = mode === 'momentum' ? Math.sign(v) : -Math.sign(v);
              const entry = bars[i].close;
              const exit = bars[i + hold].close;
              // Return in basis points, net of a round trip.
              const grossBps = dir * ((exit - entry) / entry) * 10000;
              const bps = grossBps - costBps;
              rs.push(bps);
              if (bps > 0) wins++;
              if (grossBps > 0) grossWins++;
              (i < mid ? first : second).push(bps);
            }
            if (rs.length < 200) continue;
            const t = S.hacT(rs, Math.max(0, hold - 1));
            if (!t) continue;
            tests.push({
              symbol: sym, indicator: key, threshold: th, mode, hold,
              n: t.n, meanBps: t.mean, t: t.t, p: t.p,
              winRate: (wins / rs.length) * 100,
              grossWinRate: (grossWins / rs.length) * 100,
              first: S.mean(first), second: S.mean(second)
            });
          }
        }
      }
    }
    process.stdout.write(`\r  scanned ${sym}: ${tests.length} configurations tested`);
  }
  process.stdout.write('\n');

  console.log(`\n  ${tests.length} strategy configurations across ${symbolsUsed} symbols.`);
  console.log(`  ${Object.keys(INDICATORS).length} indicators x ${thresholds.length} thresholds x 2 directions x ${holds.length} holding periods.`);
  console.log(`  Cost charged: ${costBps} bps round trip. Holding periods on ${tf}m bars: ` +
              holds.map(h => `${h}b=${h * parseInt(tf)}min`).join(', '));

  // ── The central comparison ──
  const byWin = tests.slice().sort((a, b) => b.winRate - a.winRate);
  const byExp = tests.slice().sort((a, b) => b.meanBps - a.meanBps);

  console.log(`\n${line}\n  TOP 12 BY WIN RATE\n${line}`);
  console.log(`  ${'symbol'.padEnd(10)}${'indicator'.padEnd(16)}${'mode'.padEnd(11)}${'hold'.padStart(6)}${'n'.padStart(8)}${'WIN%'.padStart(8)}${'mean bps'.padStart(11)}${'t'.padStart(8)}`);
  for (const x of byWin.slice(0, 12)) {
    console.log(`  ${x.symbol.padEnd(10)}${x.indicator.padEnd(16)}${x.mode.padEnd(11)}${(x.hold + 'b').padStart(6)}${String(x.n).padStart(8)}${x.winRate.toFixed(1).padStart(7)}%${x.meanBps.toFixed(2).padStart(11)}${x.t.toFixed(2).padStart(8)}`);
  }

  console.log(`\n${line}\n  TOP 12 BY EXPECTANCY\n${line}`);
  console.log(`  ${'symbol'.padEnd(10)}${'indicator'.padEnd(16)}${'mode'.padEnd(11)}${'hold'.padStart(6)}${'n'.padStart(8)}${'WIN%'.padStart(8)}${'mean bps'.padStart(11)}${'t'.padStart(8)}`);
  for (const x of byExp.slice(0, 12)) {
    console.log(`  ${x.symbol.padEnd(10)}${x.indicator.padEnd(16)}${x.mode.padEnd(11)}${(x.hold + 'b').padStart(6)}${String(x.n).padStart(8)}${x.winRate.toFixed(1).padStart(7)}%${x.meanBps.toFixed(2).padStart(11)}${x.t.toFixed(2).padStart(8)}`);
  }

  // Correlation between the two rankings across the whole grid.
  const wr = tests.map(t => t.winRate), me = tests.map(t => t.meanBps);
  console.log(`\n  Correlation between win rate and expectancy across all ${tests.length} configurations: ${(S.corr(wr, me) || 0).toFixed(3)}`);

  // Win rate distribution: how easy is 53-59%?
  const buckets = {};
  for (const t of tests) {
    const b = Math.floor(t.winRate / 5) * 5;
    buckets[b] = buckets[b] || { n: 0, exp: 0 };
    buckets[b].n++;
    buckets[b].exp += t.meanBps;
  }
  console.log(`\n  WIN-RATE DISTRIBUTION ACROSS THE GRID (and mean expectancy in each band)`);
  console.log(`  ${'win rate band'.padEnd(18)}${'configs'.padStart(9)}${'share'.padStart(9)}${'mean bps'.padStart(11)}`);
  for (const k of Object.keys(buckets).map(Number).sort((a, b) => a - b)) {
    const b = buckets[k];
    console.log(`  ${`${k}-${k + 5}%`.padEnd(18)}${String(b.n).padStart(9)}${((b.n / tests.length) * 100).toFixed(1).padStart(8)}%${(b.exp / b.n).toFixed(2).padStart(11)}`);
  }

  // ── Does holding longer help? This is the direct test of "try 10-15 minute
  //    trades instead of instant ones", and of its opposite. ──
  console.log(`\n${line}\n  DOES HOLDING LONGER HELP?\n${line}`);
  console.log(`  ${'holding period'.padEnd(20)}${'configs'.padStart(9)}${'mean bps'.padStart(11)}${'best bps'.padStart(11)}${'gross win%'.padStart(12)}${'net win%'.padStart(11)}`);
  const byHold = {};
  for (const t of tests) (byHold[t.hold] = byHold[t.hold] || []).push(t);
  for (const h of Object.keys(byHold).map(Number).sort((a, b) => a - b)) {
    const g = byHold[h];
    const label = `${h} bars = ${h * parseInt(tf)} min`;
    console.log(`  ${label.padEnd(20)}${String(g.length).padStart(9)}${S.mean(g.map(x => x.meanBps)).toFixed(2).padStart(11)}${Math.max(...g.map(x => x.meanBps)).toFixed(2).padStart(11)}${S.mean(g.map(x => x.grossWinRate)).toFixed(1).padStart(11)}%${S.mean(g.map(x => x.winRate)).toFixed(1).padStart(10)}%`);
  }
  console.log(`
  The gross and net win-rate columns are the same trades measured before and
  after the ${costBps} bps round trip. The gap between them IS the cost, expressed as
  the share of trades it converts from winners into losers — and it shrinks as
  the holding period lengthens, because the expected move grows with time while
  the fee does not. That is the whole argument for holding longer, and the mean
  expectancy column shows it directly.`);

  // ── FDR across the whole grid ──
  const { survivors, tested, threshold } = S.fdr(tests, 0.10);
  console.log(`\n${line}\n  AFTER FDR CORRECTION ACROSS ALL ${tested} CONFIGURATIONS\n${line}`);
  if (!survivors.length) {
    console.log(`  Nothing survived. With ${tested} tests, roughly ${Math.round(tested * 0.05)} would look\n  significant at the 5% level under a pure null — correction removes them all.`);
  } else {
    const verdicts = survivors.map(s => ({ ...s, v: S.verdict(s, s.first, s.second, 0) }));
    const solid = verdicts.filter(v => v.v === 'SURVIVES' && v.meanBps > 0);
    console.log(`  ${survivors.length} of ${tested} passed FDR (p <= ${threshold.toExponential(2)}).`);
    console.log(`  Of those, ${solid.length} are POSITIVE and hold their sign across both halves.\n`);
    if (solid.length) {
      console.log(`  ${'symbol'.padEnd(10)}${'indicator'.padEnd(16)}${'mode'.padEnd(11)}${'hold'.padStart(6)}${'n'.padStart(8)}${'WIN%'.padStart(8)}${'mean bps'.padStart(11)}${'t'.padStart(8)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);
      for (const x of solid.sort((a, b) => b.meanBps - a.meanBps).slice(0, 20)) {
        console.log(`  ${x.symbol.padEnd(10)}${x.indicator.padEnd(16)}${x.mode.padEnd(11)}${(x.hold + 'b').padStart(6)}${String(x.n).padStart(8)}${x.winRate.toFixed(1).padStart(7)}%${x.meanBps.toFixed(2).padStart(11)}${x.t.toFixed(2).padStart(8)}${x.first.toFixed(1).padStart(9)}${x.second.toFixed(1).padStart(9)}`);
      }
    }
  }

  console.log(`\n${line}\n`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', `indicator-grid-${tf}m.json`),
    JSON.stringify({ generatedAt: new Date().toISOString(), tf, costBps, tests }, null, 2));
}

main();
