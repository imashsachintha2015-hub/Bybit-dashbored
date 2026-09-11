#!/usr/bin/env node
/**
 * STUDY 6 — Forced flow.
 *
 * The brief was to look for what ISN'T in the usual analysis. So: stop looking
 * for participants who are choosing to trade, and look for participants who
 * have no choice.
 *
 * A liquidation is not a decision. When a leveraged position crosses its
 * maintenance margin the exchange closes it at market, immediately, at any
 * price. That order is completely price-insensitive, and in a cascade it begets
 * more of them — each fill pushes price further, tripping the next tier.
 *
 * This matters because it is the one kind of flow with a KNOWN, MECHANICAL
 * reason to overshoot. Every other participant is trying to get a good price.
 * A liquidation engine is not trying at all. If price overshoots for mechanical
 * reasons, it should revert for mechanical reasons — and unlike a chart
 * pattern, this has a causal story behind it rather than a correlation.
 *
 * Exchanges publish liquidation feeds, but not deep history. The cascade
 * signature is however visible in bars alone, and unmistakable:
 *
 *   - an outsized move against recent volatility,
 *   - on volume far above normal,
 *   - with a long wick in the direction of the move (price went there and was
 *     rejected — the forced sellers were absorbed),
 *   - and a close well back from the extreme.
 *
 * The study measures forward returns after that signature, with the same
 * statistical discipline as Study 5: HAC standard errors for overlapping
 * windows, split-sample validation, and effect sizes stated against the cost
 * floor rather than in isolation.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');
const COST_FLOOR_BPS = 2.0;

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
/** Newey-West HAC t-test — mandatory with overlapping forward windows. */
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

/**
 * Detects the cascade signature on bar i.
 * @returns null, or { side: 'DOWN'|'UP', severity }
 */
function cascadeSignature(bars, i, cfg) {
  if (i < cfg.lookback + 1) return null;
  const win = bars.slice(i - cfg.lookback, i);
  const rets = [];
  for (let k = 1; k < win.length; k++) rets.push(Math.log(win[k].close / win[k - 1].close));
  const vol = stdev(rets);
  if (!vol) return null;
  const avgVol = mean(win.map(c => c.volume));
  if (!avgVol) return null;

  const b = bars[i];
  const move = Math.log(b.close / bars[i - 1].close);
  const z = move / vol;
  const relVol = b.volume / avgVol;
  const range = b.high - b.low;
  if (range <= 0) return null;

  const lowerWick = Math.min(b.open, b.close) - b.low;
  const upperWick = b.high - Math.max(b.open, b.close);

  // Downside cascade: violent down bar, huge volume, long lower tail.
  const fullMoveDown = (b.low - bars[i - 1].close) / bars[i - 1].close / vol;
  if (fullMoveDown < -cfg.zThreshold && relVol > cfg.volThreshold && lowerWick / range > cfg.wickShare) {
    return { side: 'DOWN', severity: Math.abs(fullMoveDown), relVol, wick: lowerWick / range, z };
  }
  const fullMoveUp = (b.high - bars[i - 1].close) / bars[i - 1].close / vol;
  if (fullMoveUp > cfg.zThreshold && relVol > cfg.volThreshold && upperWick / range > cfg.wickShare) {
    return { side: 'UP', severity: Math.abs(fullMoveUp), relVol, wick: upperWick / range, z };
  }
  return null;
}

function fwdBps(bars, i, h) {
  if (i + h >= bars.length) return null;
  return ((bars[i + h].close - bars[i].close) / bars[i].close) * 10000;
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';

  const line = '─'.repeat(88);
  console.log(`\n${line}\n  STUDY 6 — FORCED FLOW: what happens after price-insensitive selling\n${line}`);
  console.log(`
  Hypothesis: a liquidation cascade is the one flow with a mechanical reason to
  overshoot, because the seller is an exchange engine with no price preference.
  If so, the overshoot should revert. Tested with HAC errors and split-sample.
`);

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));
  const configs = [
    { name: 'moderate (2.5σ, 3x vol, 30% wick)', zThreshold: 2.5, volThreshold: 3, wickShare: 0.30, lookback: 96 },
    { name: 'severe   (4σ,   5x vol, 40% wick)', zThreshold: 4.0, volThreshold: 5, wickShare: 0.40, lookback: 96 },
    { name: 'extreme  (6σ,   8x vol, 50% wick)', zThreshold: 6.0, volThreshold: 8, wickShare: 0.50, lookback: 96 }
  ];
  const horizons = [1, 4, 16, 48];
  const allTests = [];

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const bars = bundle.series[tf];
    if (!bars || bars.length < 3000) continue;
    const days = ((bars[bars.length - 1].start - bars[0].start) / 86400000).toFixed(0);

    console.log(`\n  ${symbol} — ${bars.length} x ${tf}m bars, ${days} days\n`);
    console.log(`    ${'filter'.padEnd(36)}${'events'.padStart(8)}${'horizon'.padStart(9)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);

    for (const cfg of configs) {
      const events = [];
      for (let i = cfg.lookback + 1; i < bars.length; i++) {
        const sig = cascadeSignature(bars, i, cfg);
        if (sig) events.push({ i, sig });
      }
      if (events.length < 25) {
        console.log(`    ${cfg.name.padEnd(36)}${String(events.length).padStart(8)}${'—'.padStart(9)}${'too few events to test'.padStart(28)}`);
        continue;
      }
      const mid = Math.floor(events.length / 2);

      for (const h of horizons) {
        // The bet: fade the cascade. After forced DOWN selling, go long.
        const signed = [], first = [], second = [];
        events.forEach((e, idx) => {
          const fwd = fwdBps(bars, e.i, h);
          if (fwd == null) return;
          const v = e.sig.side === 'DOWN' ? fwd : -fwd;
          signed.push(v);
          (idx < mid ? first : second).push(v);
        });
        const t = hacT(signed, Math.max(0, h - 1));
        if (!t) continue;
        allTests.push({ symbol, cfg: cfg.name, h, ...t });
        const consistent = first.length > 10 && second.length > 10 &&
          Math.sign(mean(first)) === Math.sign(mean(second)) && Math.sign(mean(first)) === Math.sign(t.mean);
        console.log(`    ${cfg.name.padEnd(36)}${String(events.length).padStart(8)}${(h + ' bars').padStart(9)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${mean(first).toFixed(1).padStart(9)}${mean(second).toFixed(1).padStart(9)}${consistent && Math.abs(t.t) > 2 && Math.abs(t.mean) > COST_FLOOR_BPS ? '  <--' : ''}`);
      }
    }
  }

  // Pooled test: the same hypothesis across all symbols is one hypothesis, and
  // pooling is the highest-powered way to ask it.
  console.log(`\n${line}\n  POOLED ACROSS SYMBOLS\n${line}`);
  console.log(`  ${'filter'.padEnd(36)}${'horizon'.padStart(9)}${'tests'.padStart(7)}${'mean bps'.padStart(11)}${'median t'.padStart(11)}`);
  const grouped = {};
  for (const t of allTests) {
    const k = `${t.cfg}|${t.h}`;
    grouped[k] = grouped[k] || [];
    grouped[k].push(t);
  }
  for (const [k, ts] of Object.entries(grouped)) {
    const [cfg, h] = k.split('|');
    const medT = ts.map(x => x.t).sort((a, b) => a - b)[Math.floor(ts.length / 2)];
    console.log(`  ${cfg.padEnd(36)}${(h + ' bars').padStart(9)}${String(ts.length).padStart(7)}${mean(ts.map(x => x.mean)).toFixed(2).padStart(11)}${medT.toFixed(2).padStart(11)}`);
  }

  console.log(`
${line}
  Marked rows (<--) cleared all three bars at once: |t| > 2 on HAC errors, the
  same sign in both halves of the sample, and an effect larger than the ${COST_FLOOR_BPS} bps
  cost of acting on it. Anything unmarked did not, whatever its headline number.
${line}
`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'forced-flow.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), tf, tests: allTests }, null, 2));
}

main();
