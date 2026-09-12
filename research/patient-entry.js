#!/usr/bin/env node
/**
 * STUDY 10 — Patience at the ENTRY, not the exit.
 *
 * Every earlier study, including the 2,448-configuration grid, entered at
 * market on the bar the signal fired. Holding period varied; the entry never
 * did. That is one narrow scenario tested many times, and it left the more
 * interesting question untouched.
 *
 * Waiting before entering does two separate things, and they must be measured
 * apart because only one of them is interesting:
 *
 *   1. IT LOWERS COST. A resting limit pays the maker fee instead of the taker
 *      fee and does not cross the spread. Already measured elsewhere: worth
 *      about +0.137R per trade. Real, but it is a rebate, not an edge.
 *
 *   2. IT SELECTS WHICH TRADES YOU GET. This is the part never tested. A limit
 *      below the market only fills if price comes back to it. So you
 *      systematically DON'T get the trades that ran away immediately, and you
 *      DO get the ones that hesitated. That is a filter on the population of
 *      trades, and a filter can change GROSS expectancy — unlike a fee, which
 *      cannot.
 *
 * The whole study turns on that distinction, so gross is reported everywhere
 * alongside net. If patience only moves net, it is a cost story already known.
 * If it moves GROSS, that is something genuinely new.
 *
 * Three kinds of patience are tested against instant entry:
 *   DELAY-k      — enter unconditionally k bars later. No selection, just lag.
 *                  This is the control: it isolates whether the signal decays.
 *   LIMIT-x      — rest a limit x bps better; trade only if filled within the
 *                  window. Selection + cost saving.
 *   PULLBACK-f   — wait for a retracement of fraction f of the recent swing.
 *                  A stronger version of the same selection.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');

function sd(a) {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

/** Signals: a small, deliberately ordinary set. The point of the study is the
 *  ENTRY method, so the signals are held simple and constant across it. */
const SIGNALS = {
  'momentum roc14': (b, i) => {
    if (i < 20) return 0;
    const rets = [];
    for (let k = i - 14; k < i; k++) rets.push(Math.log(b[k + 1].close / b[k].close));
    const v = sd(rets);
    if (!v) return 0;
    const z = Math.log(b[i].close / b[i - 14].close) / (v * Math.sqrt(14));
    return Math.abs(z) > 1 ? Math.sign(z) : 0;
  },
  'reversion roc14': (b, i) => {
    if (i < 20) return 0;
    const rets = [];
    for (let k = i - 14; k < i; k++) rets.push(Math.log(b[k + 1].close / b[k].close));
    const v = sd(rets);
    if (!v) return 0;
    const z = Math.log(b[i].close / b[i - 14].close) / (v * Math.sqrt(14));
    return Math.abs(z) > 1 ? -Math.sign(z) : 0;
  },
  'donchian break 20': (b, i) => {
    if (i < 25) return 0;
    const w = b.slice(i - 20, i);
    const hi = Math.max(...w.map(c => c.high)), lo = Math.min(...w.map(c => c.low));
    return b[i].close > hi ? 1 : b[i].close < lo ? -1 : 0;
  },
  'bollinger fade 20': (b, i) => {
    if (i < 25) return 0;
    const w = b.slice(i - 20, i).map(c => c.close);
    const m = w.reduce((x, y) => x + y, 0) / w.length;
    const s = sd(w);
    if (!s) return 0;
    const z = (b[i].close - m) / s;
    return Math.abs(z) > 1.5 ? -Math.sign(z) : 0;
  }
};

function atrAt(b, i, p = 14) {
  let tr = 0, n = 0;
  for (let k = Math.max(1, i - p); k < i; k++) {
    tr += Math.max(b[k].high - b[k].low, Math.abs(b[k].high - b[k - 1].close), Math.abs(b[k].low - b[k - 1].close));
    n++;
  }
  return n ? tr / n : 0;
}

/**
 * Resolves an entry method into { entryPrice, entryBar } or null if never filled.
 */
function resolveEntry(bars, i, dir, method, waitBars) {
  if (method.kind === 'instant') return { px: bars[i].close, bar: i };

  if (method.kind === 'delay') {
    const k = i + method.k;
    return k < bars.length ? { px: bars[k].close, bar: k } : null;
  }

  if (method.kind === 'limit') {
    // Better price than the signal close: below for a long, above for a short.
    const ref = bars[i].close;
    const limit = dir > 0 ? ref * (1 - method.bps / 10000) : ref * (1 + method.bps / 10000);
    for (let k = i + 1; k <= Math.min(i + waitBars, bars.length - 1); k++) {
      const b = bars[k];
      if (dir > 0 ? b.low <= limit : b.high >= limit) return { px: limit, bar: k };
    }
    return null;   // never came back — trade missed
  }

  if (method.kind === 'pullback') {
    // Wait for a retracement of `frac` of the move that produced the signal.
    const swingStart = bars[Math.max(0, i - 14)].close;
    const move = bars[i].close - swingStart;
    if (Math.sign(move) !== dir) return null;
    const target = bars[i].close - move * method.frac;
    for (let k = i + 1; k <= Math.min(i + waitBars, bars.length - 1); k++) {
      const b = bars[k];
      if (dir > 0 ? b.low <= target : b.high >= target) return { px: target, bar: k };
    }
    return null;
  }
  return null;
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';
  const takerBps = parseFloat(args.taker || '15');
  const makerBps = parseFloat(args.maker || '2');
  const hold = parseInt(args.hold || '48', 10);     // exit horizon in bars, from ENTRY
  const waitBars = parseInt(args.wait || '8', 10);  // how long a limit rests before it is abandoned

  const line = '─'.repeat(104);
  console.log(`\n${line}\n  STUDY 10 — PATIENCE AT THE ENTRY\n${line}`);
  console.log(`
  Every earlier study entered at market on the signal bar. This varies the ENTRY
  instead, and separates the two things waiting does: lowering cost (known, and
  only a rebate) from SELECTING which trades you get (untested, and the only one
  that can move gross expectancy).

  Exit: ${hold} bars after entry (${hold * parseInt(tf)} min). Limit orders rest ${waitBars} bars before being abandoned.
  Costs: taker ${takerBps} bps round trip, maker ${makerBps} bps.
`);

  const methods = [
    { name: 'INSTANT (market)', kind: 'instant', cost: takerBps },
    { name: 'delay 1 bar', kind: 'delay', k: 1, cost: takerBps },
    { name: 'delay 4 bars', kind: 'delay', k: 4, cost: takerBps },
    { name: 'limit  5 bps better', kind: 'limit', bps: 5, cost: makerBps },
    { name: 'limit 15 bps better', kind: 'limit', bps: 15, cost: makerBps },
    { name: 'limit 40 bps better', kind: 'limit', bps: 40, cost: makerBps },
    { name: 'pullback 25% of move', kind: 'pullback', frac: 0.25, cost: makerBps },
    { name: 'pullback 50% of move', kind: 'pullback', frac: 0.50, cost: makerBps }
  ];

  const symbols = fs.readdirSync(DATA).filter(f => f.endsWith('.json') && !f.includes('-derivs'))
    .map(f => f.replace('.json', ''));

  const all = [];

  for (const [sigName, sigFn] of Object.entries(SIGNALS)) {
    console.log(`\n  SIGNAL: ${sigName}`);
    console.log(`    ${'entry method'.padEnd(24)}${'signals'.padStart(9)}${'filled'.padStart(8)}${'fill%'.padStart(8)}${'GROSS bps'.padStart(11)}${'NET bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'win%'.padStart(7)}`);

    for (const m of methods) {
      const gross = [], net = [], firsts = [], seconds = [];
      let signals = 0, filled = 0, wins = 0;

      for (const sym of symbols) {
        const bundle = JSON.parse(fs.readFileSync(path.join(DATA, `${sym}.json`), 'utf8'));
        const bars = bundle.series[tf];
        if (!bars || bars.length < 5000) continue;
        const mid = Math.floor(bars.length / 2);

        for (let i = 200; i < bars.length - hold - waitBars - 2; i += 3) {
          const dir = sigFn(bars, i);
          if (!dir) continue;
          signals++;
          const e = resolveEntry(bars, i, dir, m, waitBars);
          if (!e) continue;            // limit never filled: the trade is simply missed
          const exitBar = e.bar + hold;
          if (exitBar >= bars.length) continue;
          filled++;
          const g = dir * ((bars[exitBar].close - e.px) / e.px) * 10000;
          const n = g - m.cost;
          gross.push(g); net.push(n);
          if (n > 0) wins++;
          (i < mid ? firsts : seconds).push(n);
        }
      }

      if (net.length < 100) continue;
      const t = S.hacT(net, Math.max(0, hold - 1));
      if (!t) continue;
      all.push({ signal: sigName, method: m.name, n: t.n, gross: S.mean(gross), net: t.mean, t: t.t, p: t.p,
                 fillPct: signals ? (filled / signals) * 100 : 0, winRate: (wins / net.length) * 100,
                 first: S.mean(firsts), second: S.mean(seconds) });
      const row = all[all.length - 1];
      const flag = m.kind === 'instant' ? '   <- baseline' : '';
      console.log(`    ${m.name.padEnd(24)}${String(signals).padStart(9)}${String(filled).padStart(8)}${row.fillPct.toFixed(0).padStart(7)}%${row.gross.toFixed(2).padStart(11)}${row.net.toFixed(2).padStart(10)}${row.t.toFixed(2).padStart(9)}${row.winRate.toFixed(1).padStart(6)}%${flag}`);
    }
  }

  // ── The decisive comparison: did patience move GROSS, or only NET? ──
  console.log(`\n${line}\n  DID PATIENCE MOVE GROSS EXPECTANCY, OR ONLY COST?\n${line}`);
  console.log(`  ${'entry method'.padEnd(24)}${'mean GROSS bps'.padStart(16)}${'vs instant'.padStart(12)}${'mean NET bps'.padStart(14)}${'vs instant'.padStart(12)}${'mean fill%'.padStart(12)}`);
  const byMethod = {};
  for (const r of all) (byMethod[r.method] = byMethod[r.method] || []).push(r);
  const baseG = S.mean((byMethod['INSTANT (market)'] || []).map(r => r.gross));
  const baseN = S.mean((byMethod['INSTANT (market)'] || []).map(r => r.net));
  for (const m of methods) {
    const g = byMethod[m.name];
    if (!g) continue;
    const mg = S.mean(g.map(r => r.gross)), mn = S.mean(g.map(r => r.net));
    console.log(`  ${m.name.padEnd(24)}${mg.toFixed(2).padStart(16)}${((mg - baseG >= 0 ? '+' : '') + (mg - baseG).toFixed(2)).padStart(12)}${mn.toFixed(2).padStart(14)}${((mn - baseN >= 0 ? '+' : '') + (mn - baseN).toFixed(2)).padStart(12)}${S.mean(g.map(r => r.fillPct)).toFixed(0).padStart(11)}%`);
  }

  const { survivors, tested } = S.fdr(all, 0.10);
  const solid = survivors.filter(s => s.net > 0 && Math.sign(s.first) === Math.sign(s.second) && Math.sign(s.first) === Math.sign(s.net));
  console.log(`\n  FDR across ${tested} signal x entry-method combinations: ${survivors.length} pass, ${solid.length} positive and stable across both halves.`);
  if (solid.length) {
    console.log(`\n  ${'signal'.padEnd(22)}${'entry'.padEnd(24)}${'n'.padStart(7)}${'net bps'.padStart(10)}${'t'.padStart(8)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);
    for (const s of solid.sort((a, b) => b.net - a.net)) {
      console.log(`  ${s.signal.padEnd(22)}${s.method.padEnd(24)}${String(s.n).padStart(7)}${s.net.toFixed(2).padStart(10)}${s.t.toFixed(2).padStart(8)}${s.first.toFixed(1).padStart(9)}${s.second.toFixed(1).padStart(9)}`);
    }
  }

  console.log(`
  The two "vs instant" columns are the whole study. If only the NET column
  improves, patience is a rebate — real money, but nothing new, and already
  measured at +0.137R per trade elsewhere. If the GROSS column improves, then
  waiting is selecting better trades rather than just cheaper ones, which is a
  different and far more interesting claim.
${line}
`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'patient-entry.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), tf, hold, waitBars, results: all }, null, 2));
}

main();
