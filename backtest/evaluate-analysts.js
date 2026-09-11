#!/usr/bin/env node
/**
 * Standalone predictive-value test for each analyst.
 *
 * Trade-level statistics cannot settle whether an analyst is any good: a run
 * produces a few dozen trades, and a few dozen observations cannot separate
 * skill from luck. This measures each analyst directly against forward returns
 * over every bar instead, producing thousands of observations per analyst.
 *
 * For each bar, each analyst's directional read is recorded, then scored
 * against what price actually did over the next N bars. The output is the hit
 * rate (did the read point the right way) and the average forward move in ATR
 * units conditioned on that read — the second matters more, because an analyst
 * that is right 51% of the time on large moves is more valuable than one right
 * 58% of the time on noise.
 *
 * A read with no edge lands at a ~50% hit rate and ~0 conditional move. An
 * analyst materially below 50% is not useless — it is inverted, and worth
 * knowing about.
 *
 * Usage: node backtest/evaluate-analysts.js [--horizon 8] [--step 2]
 */
const fs = require('fs');
const path = require('path');
const I = require('../agents/indicators.js');
const Panel = require('../swarm/panel.js');
const Debate = require('../swarm/debate.js');
const { FlowProxy } = require('./flow-proxy.js');

const DATA_DIR = path.join(__dirname, 'data');

function resample(series, factor) {
  const out = [];
  for (let i = 0; i + factor <= series.length; i += factor) {
    const c = series.slice(i, i + factor);
    out.push({ start: c[0].start, open: c[0].open,
      high: Math.max(...c.map(x => x.high)), low: Math.min(...c.map(x => x.low)),
      close: c[c.length - 1].close, volume: c.reduce((a, x) => a + x.volume, 0), confirm: true });
  }
  return out;
}

function sideOf(read) {
  if (read === 'BULLISH' || read === 'DRAW_UP') return 'LONG';
  if (read === 'BEARISH' || read === 'DRAW_DOWN') return 'SHORT';
  return null;
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const horizon = parseInt(args.horizon || '8', 10);   // MTF bars forward
  const step = parseInt(args.step || '2', 10);

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));
  const stats = {};   // analystId -> { n, hits, sumMove, byRegime }

  const bump = (id, regime, correct, moveAtr, conviction, half) => {
    stats[id] = stats[id] || { n: 0, hits: 0, sumMove: 0, sumConv: 0, byRegime: {}, halves: { first: { n: 0, sumMove: 0, hits: 0 }, second: { n: 0, sumMove: 0, hits: 0 } } };
    const s = stats[id];
    s.n++; s.sumConv += conviction;
    if (correct) s.hits++;
    s.sumMove += moveAtr;
    if (half) {
      s.halves[half].n++;
      s.halves[half].sumMove += moveAtr;
      if (correct) s.halves[half].hits++;
    }
    s.byRegime[regime] = s.byRegime[regime] || { n: 0, hits: 0, sumMove: 0 };
    s.byRegime[regime].n++;
    if (correct) s.byRegime[regime].hits++;
    s.byRegime[regime].sumMove += moveAtr;
  };

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const derivFile = path.join(DATA_DIR, `${symbol.replace(/USDT$/, '')}-derivs.json`);
    const derivs = fs.existsSync(derivFile) ? JSON.parse(fs.readFileSync(derivFile, 'utf8')).series : [];

    const m15 = bundle.series['15'];
    const m5 = bundle.series['5'];
    if (!m15 || m15.length < 500) continue;
    const h1 = resample(m15, 4);
    const starts = h1.map(c => c.start);
    const seekH = ts => { let lo = 0, hi = starts.length; while (lo < hi) { const m = (lo + hi) >> 1; if (starts[m] < ts) lo = m + 1; else hi = m; } return lo; };

    const flow = new FlowProxy();
    let dp = 0;
    const midpoint = Math.floor((m15.length + 400) / 2);

    process.stdout.write(`  ${symbol}: `);
    for (let i = 400; i < m15.length - horizon - 1; i += step) {
      const bar = m15[i];
      const close = bar.start + 15 * 60 * 1000;
      for (let k = (i - step < 400 ? i : i - step); k <= i; k++) flow.push(m15[k]);
      while (dp < derivs.length && derivs[dp].ts <= close) dp++;
      const visible = derivs.slice(0, dp);
      flow.setTakerData(visible);

      const half = i < midpoint ? 'first' : 'second';
      const window = m15.slice(Math.max(0, i - 299), i + 1);
      const atr = I.atr(window, 14);
      if (!atr) continue;
      const price = bar.close;

      const htfSlice = h1.slice(Math.max(0, seekH(close) - 200), seekH(close));
      if (htfSlice.length < 50) continue;

      // Regime label for bucketing (cheap version of the regime agent).
      const closes = htfSlice.map(c => c.close);
      const er = I.efficiencyRatio(closes, 20);
      const adx = I.adx(htfSlice, 14);
      const regime = (er >= 0.38 && adx >= 22) ? 'TREND' : (er < 0.28 && adx < 20) ? 'CHOP' : 'MIXED';

      // Bias, for the analysts whose validity is conditional on it.
      const e21 = I.ema(closes, 21), e50 = I.ema(closes, 50);
      const lastC = closes[closes.length - 1];
      const bias = regime === 'TREND'
        ? (lastC > e21 && e21 > e50 ? 'LONG' : lastC < e21 && e21 < e50 ? 'SHORT' : 'NEUTRAL')
        : 'NEUTRAL';

      const ctx = { mtf: window, htf: htfSlice, ltf: m5, price, atrMtf: atr,
                    derivs: visible, flowAgent: flow, now: close, symbol, regime, bias };

      let reads;
      try { reads = Panel.run(ctx); } catch (e) { continue; }

      // Forward outcome.
      const future = m15[i + horizon];
      if (!future) continue;
      const moveAtr = (future.close - price) / atr;

      for (const r of reads) {
        const side = sideOf(r.read);
        if (!side || !r.conviction) continue;
        const correct = side === 'LONG' ? moveAtr > 0 : moveAtr < 0;
        const directional = side === 'LONG' ? moveAtr : -moveAtr;
        bump(r.id, regime, correct, directional, r.conviction, half);
      }

      // Score the debate's own output the same way.
      try {
        const v = Debate.deliberate(reads);
        if (v.thesis) {
          const correct = v.thesis === 'LONG' ? moveAtr > 0 : moveAtr < 0;
          bump('__DEBATE__', regime, correct, v.thesis === 'LONG' ? moveAtr : -moveAtr, v.conviction, half);
        }
      } catch (e) { /* ignore */ }
    }
    process.stdout.write('done\n');
  }

  const rows = Object.entries(stats).map(([id, s]) => ({
    analyst: id, n: s.n,
    hitRate: +((s.hits / s.n) * 100).toFixed(1),
    avgFwdAtr: +(s.sumMove / s.n).toFixed(4),
    avgConviction: +(s.sumConv / s.n).toFixed(2),
    byRegime: s.byRegime,
    firstHalf: s.halves.first.n ? +(s.halves.first.sumMove / s.halves.first.n).toFixed(4) : null,
    secondHalf: s.halves.second.n ? +(s.halves.second.sumMove / s.halves.second.n).toFixed(4) : null,
    firstN: s.halves.first.n, secondN: s.halves.second.n
  })).sort((a, b) => b.avgFwdAtr - a.avgFwdAtr);

  const line = '─'.repeat(76);
  console.log(`\n${line}\n  ANALYST PREDICTIVE VALUE — ${horizon} x 15m bars forward\n${line}`);
  console.log(`  ${'analyst'.padEnd(16)}${'n'.padStart(8)}${'hit %'.padStart(9)}${'avg fwd (ATR)'.padStart(16)}${'avg conv'.padStart(10)}`);
  for (const r of rows) {
    const flag = r.avgFwdAtr > 0.02 ? '  <- edge' : r.avgFwdAtr < -0.02 ? '  <- INVERTED' : '';
    console.log(`  ${r.analyst.padEnd(16)}${String(r.n).padStart(8)}${String(r.hitRate).padStart(9)}${String(r.avgFwdAtr).padStart(16)}${String(r.avgConviction).padStart(10)}${flag}`);
  }

  console.log(`\n  Per-regime forward move (ATR), by analyst:`);
  console.log(`  ${'analyst'.padEnd(16)}${'TREND'.padStart(14)}${'MIXED'.padStart(14)}${'CHOP'.padStart(14)}`);
  for (const r of rows) {
    const cell = g => {
      const b = r.byRegime[g];
      return b && b.n > 30 ? `${(b.sumMove / b.n).toFixed(3)} (${b.n})` : '—';
    };
    console.log(`  ${r.analyst.padEnd(16)}${cell('TREND').padStart(14)}${cell('MIXED').padStart(14)}${cell('CHOP').padStart(14)}`);
  }
  console.log(`\n  SPLIT-SAMPLE CHECK — the design rules above were chosen while looking at`);
  console.log(`  this data, so the full-sample figure flatters them. An edge present in`);
  console.log(`  BOTH halves is evidence of something real; one that appears in only one`);
  console.log(`  half is noise that happened to average positive.`);
  console.log(`  ${'analyst'.padEnd(16)}${'first half'.padStart(14)}${'second half'.padStart(14)}${'consistent?'.padStart(14)}`);
  for (const r of rows) {
    const consistent = (r.firstHalf != null && r.secondHalf != null)
      ? ((r.firstHalf > 0.01 && r.secondHalf > 0.01) ? 'yes' : (r.firstHalf < -0.01 && r.secondHalf < -0.01) ? 'yes (neg)' : 'NO')
      : '—';
    console.log(`  ${r.analyst.padEnd(16)}${String(r.firstHalf).padStart(14)}${String(r.secondHalf).padStart(14)}${consistent.padStart(14)}`);
  }

  console.log(`\n  Read "avg fwd (ATR)" as: on average, price moved this far in the direction\n  the analyst pointed, over the next ${horizon} bars. Zero means no edge. The hit\n  rate alone is not sufficient — being right often on tiny moves loses money.\n${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'analyst-evaluation.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), horizon, step, rows }, null, 2));
}

main();
