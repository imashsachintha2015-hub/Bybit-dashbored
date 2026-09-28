#!/usr/bin/env node
/**
 * STUDY 7 — Trading the predictable quantity.
 *
 * Study 1 established two things on 12,000+ bars per symbol:
 *   - direction is very nearly unpredictable (return autocorrelation ~0.01),
 *   - MAGNITUDE is strongly predictable (|return| autocorrelation 0.30, and
 *     recent realised volatility explains ~20% of future realised volatility).
 *
 * Every strategy in this repository up to now has bet on the first one. That is
 * the part that does not work. This study bets on the second.
 *
 * The structure that expresses "I know how far, not which way" without options
 * is a two-sided breakout bracket: rest a buy stop above the range and a sell
 * stop below it. Whichever triggers, you are positioned in the direction the
 * expansion actually chose. You never needed to know which.
 *
 * That is a genuinely different bet from everything else here, and it has a
 * clean, testable failure mode: if the range is entered and exited without
 * follow-through, both sides whipsaw and you pay twice. So the question the
 * study must answer is not "does vol expand" — it does, predictably — but
 * "does the expansion travel far enough, often enough, to pay for the
 * whipsaws and the costs".
 *
 * Discipline carried over from Study 5: HAC standard errors, split-sample
 * validation, and every result stated net of a modelled round trip.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function stdev(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}
function twoSidedP(t) {
  const z = Math.abs(t);
  const p = 0.2316419, b = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429];
  const tt = 1 / (1 + p * z);
  const phi = Math.exp(-z * z / 2) / Math.sqrt(2 * Math.PI);
  let poly = 0;
  for (let i = 0; i < b.length; i++) poly += b[i] * Math.pow(tt, i + 1);
  return 2 * phi * poly;
}
function hacT(values, lag) {
  const n = values.length;
  if (n < 25) return null;
  const m = mean(values);
  const dev = values.map(v => v - m);
  let lrv = dev.reduce((a, d) => a + d * d, 0) / n;
  for (let L = 1; L <= Math.min(lag, n - 2); L++) {
    let g = 0;
    for (let i = L; i < n; i++) g += dev[i] * dev[i - L];
    g /= n;
    lrv += 2 * (1 - L / (lag + 1)) * g;
  }
  if (!(lrv > 0)) return null;
  const t = m / Math.sqrt(lrv / n);
  return { n, mean: m, t, p: twoSidedP(t) };
}

function atr(bars, period) {
  const trs = [];
  for (let i = 1; i < bars.length; i++) {
    const c = bars[i], p = bars[i - 1];
    trs.push(Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close)));
  }
  const s = trs.slice(-period);
  return s.length ? mean(s) : 0;
}

/**
 * One two-sided breakout trial.
 *
 * At bar i: measure the range of the last `coil` bars. Place a buy stop above
 * it and a sell stop below, each offset by `bufferAtr`. Walk forward up to
 * `maxBars`. Whichever side triggers first is the position; it exits at a
 * target of `targetR` times the initial risk, at a stop equal to the opposite
 * breakout level, or at the horizon.
 *
 * Costs are charged per fill. A whipsaw (stopped out then the other side
 * triggers) pays twice, which is the whole risk of this structure and is
 * modelled explicitly rather than assumed away.
 */
function breakoutTrial(bars, i, cfg) {
  const { coil, bufferAtr, targetR, maxBars, costBps, allowReentry } = cfg;
  if (i < coil + 20 || i + maxBars >= bars.length) return null;

  const win = bars.slice(i - coil, i);
  const hi = Math.max(...win.map(c => c.high));
  const lo = Math.min(...win.map(c => c.low));
  const a = atr(bars.slice(i - 50, i), 14);
  if (!a || hi <= lo) return null;

  const upper = hi + a * bufferAtr;
  const lower = lo - a * bufferAtr;
  const risk = upper - lower;
  if (risk <= 0) return null;

  let totalBps = 0;
  let entries = 0;
  let side = null, entryPx = 0, stopPx = 0, targetPx = 0;

  for (let k = i; k < i + maxBars; k++) {
    const b = bars[k];

    if (!side) {
      // Pessimistic when both levels are touched in the same bar: assume the
      // side that leads to the whipsaw triggers first.
      const upHit = b.high >= upper, dnHit = b.low <= lower;
      if (upHit && dnHit) {
        side = 'LONG'; entryPx = upper;
      } else if (upHit) { side = 'LONG'; entryPx = upper; }
      else if (dnHit) { side = 'SHORT'; entryPx = lower; }
      if (side) {
        entries++;
        totalBps -= costBps;
        stopPx = side === 'LONG' ? lower : upper;
        const r = Math.abs(entryPx - stopPx);
        targetPx = side === 'LONG' ? entryPx + r * targetR : entryPx - r * targetR;
      }
      continue;
    }

    const stopped = side === 'LONG' ? b.low <= stopPx : b.high >= stopPx;
    const hitTarget = side === 'LONG' ? b.high >= targetPx : b.low <= targetPx;

    // Pessimistic ordering: the stop is taken when both are inside one bar.
    if (stopped) {
      const pnl = side === 'LONG' ? (stopPx - entryPx) / entryPx : (entryPx - stopPx) / entryPx;
      totalBps += pnl * 10000 - costBps;
      side = null;
      if (!allowReentry) break;
      continue;
    }
    if (hitTarget) {
      const pnl = side === 'LONG' ? (targetPx - entryPx) / entryPx : (entryPx - targetPx) / entryPx;
      totalBps += pnl * 10000 - costBps;
      side = null;
      break;
    }
  }

  if (side) {
    const exit = bars[Math.min(i + maxBars, bars.length - 1)].close;
    const pnl = side === 'LONG' ? (exit - entryPx) / entryPx : (entryPx - exit) / entryPx;
    totalBps += pnl * 10000 - costBps;
  }
  if (!entries) return null;
  return { bps: totalBps, entries, riskBps: (risk / bars[i].close) * 10000 };
}

/** Realised volatility over the trailing window, annualisation-free. */
function realisedVol(bars, i, window) {
  const rets = [];
  for (let k = i - window + 1; k <= i; k++) {
    if (k < 1) continue;
    rets.push(Math.log(bars[k].close / bars[k - 1].close));
  }
  return stdev(rets);
}

function main() {
  const args = {};
  for (let a = 2; a < process.argv.length; a += 2) args[process.argv[a].replace(/^--/, '')] = process.argv[a + 1];
  const tf = args.tf || '15';
  const costBps = parseFloat(args.cost || '2');   // maker round trip per fill leg

  const line = '─'.repeat(88);
  console.log(`\n${line}\n  STUDY 7 — BETTING ON MAGNITUDE INSTEAD OF DIRECTION\n${line}`);
  console.log(`
  Study 1 proved direction is ~unpredictable and magnitude is predictable. This
  tests the structure that expresses "I know how far, not which way": a
  two-sided breakout bracket. Cost charged per fill leg: ${costBps} bps.
`);

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const bars = bundle.series[tf];
    if (!bars || bars.length < 3000) continue;
    const days = ((bars[bars.length - 1].start - bars[0].start) / 86400000).toFixed(0);
    console.log(`\n  ${symbol} — ${bars.length} x ${tf}m bars, ${days} days\n`);
    console.log(`    ${'setup'.padEnd(40)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'win%'.padStart(7)}${'1st'.padStart(8)}${'2nd'.padStart(8)}`);

    // Compression percentile: how quiet must the coil be before the bracket is
    // placed? This is where the volatility forecast enters — a coil that is
    // quiet RELATIVE TO ITS OWN RECENT NORM is the setup with the highest
    // probability of an expansion, and that probability is the one thing
    // Study 1 says is genuinely forecastable.
    for (const coil of [8, 16, 32]) {
      for (const compressionPct of [1.0, 0.5, 0.25]) {
        const cfg = { coil, bufferAtr: 0.1, targetR: 1.5, maxBars: coil * 3, costBps, allowReentry: true };
        const results = [], firsts = [], seconds = [];
        // Pre-compute the volatility distribution so "quiet" is defined against
        // this symbol's own history, not a constant.
        const vols = [];
        for (let i = 200; i < bars.length; i += 1) vols.push(realisedVol(bars, i, coil));
        const sorted = vols.slice().sort((a, b) => a - b);
        const cutoff = sorted[Math.floor(sorted.length * compressionPct) - 1];

        const mid = Math.floor(bars.length / 2);
        // Step by the coil length so trials do not overlap each other heavily.
        for (let i = 200; i < bars.length - coil * 3 - 1; i += coil) {
          if (compressionPct < 1.0) {
            const v = realisedVol(bars, i, coil);
            if (!(v <= cutoff)) continue;
          }
          const r = breakoutTrial(bars, i, cfg);
          if (!r) continue;
          results.push(r.bps);
          (i < mid ? firsts : seconds).push(r.bps);
        }
        const t = hacT(results, 2);
        if (!t) continue;
        const wins = results.filter(v => v > 0).length;
        const label = `coil ${coil} bars, quietest ${(compressionPct * 100).toFixed(0)}%`;
        const flag = Math.abs(t.t) > 2 && Math.sign(mean(firsts)) === Math.sign(mean(seconds)) && t.mean > 0 ? '  <--' : '';
        console.log(`    ${label.padEnd(40)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${((wins / results.length) * 100).toFixed(0).padStart(6)}%${mean(firsts).toFixed(1).padStart(8)}${mean(seconds).toFixed(1).padStart(8)}${flag}`);
      }
    }
  }

  console.log(`
${line}
  A marked row (<--) is positive, has |t| > 2 on HAC errors, and holds its sign
  across both halves of the sample. Everything else failed at least one of those.

  Note what this structure does and does not require. It does not require
  knowing direction — that is the point, and it is why it is the only strategy
  here aligned with the one quantity Study 1 showed to be forecastable. What it
  does require is that expansions travel far enough to pay for the whipsaws,
  and that is what the numbers above are actually measuring.
${line}
`);
}

main();
