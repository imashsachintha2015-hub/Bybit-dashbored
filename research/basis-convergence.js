#!/usr/bin/env node
/**
 * ATTACK 1 — Perpetual basis convergence.
 *
 * This is the most mechanically forced quantity in the entire market, and
 * nothing in the earlier studies looked at it.
 *
 * A perpetual future has no expiry, so nothing makes it converge to spot the
 * way a dated future does. Exchanges substitute a funding payment: when the
 * perp trades above the index, longs pay shorts; below, shorts pay longs. The
 * payment is proportional to the gap. It is a literal negative-feedback
 * controller wired into the contract.
 *
 * So unlike every hypothesis tested so far, this one does not require anyone to
 * be wrong, or a pattern to repeat, or a crowd to behave the same way twice. It
 * requires only that the controller works. The testable question is whether it
 * OVERSHOOTS or LAGS enough to leave anything on the table after costs.
 *
 * Two distinct bets are tested:
 *
 *   A. BASIS REVERSION. When the perp is unusually rich to its index, does the
 *      perp underperform the index over the following bars? This is a spread
 *      trade — long one leg, short the other — with no directional exposure,
 *      which is why it can be interesting at effect sizes that would be
 *      useless directionally.
 *
 *   B. BASIS AS A DIRECTIONAL SIGNAL. Extreme positive basis means leveraged
 *      longs are paying up for exposure. Does that crowding predict the PERP's
 *      own return? This is the cheaper trade to execute (one leg) but it is a
 *      behavioural claim, not a mechanical one, so it deserves more suspicion.
 *
 * All four gates from lib-stats apply.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, 'data');
const KLINES = path.join(__dirname, '..', 'backtest', 'data');
const COST_BPS = 2.0;

function loadPair(symbol, instId) {
  const kf = path.join(KLINES, `${symbol}.json`);
  const inf = path.join(DATA, `${instId}-index.json`);
  if (!fs.existsSync(kf) || !fs.existsSync(inf)) return null;
  const perp = JSON.parse(fs.readFileSync(kf, 'utf8')).series['15'];
  const index = JSON.parse(fs.readFileSync(inf, 'utf8')).rows;
  if (!perp || !index || perp.length < 1000 || index.length < 1000) return null;

  // Align strictly on identical bar timestamps. Interpolating here would invent
  // a basis that never existed.
  const idx = new Map(index.map(r => [r.start, r]));
  const rows = [];
  for (const p of perp) {
    const i = idx.get(p.start);
    if (!i || !i.close) continue;
    rows.push({ ts: p.start, perp: p.close, index: i.close, basisBps: ((p.close - i.close) / i.close) * 10000 });
  }
  return rows;
}

function main() {
  const line = '─'.repeat(90);
  console.log(`\n${line}\n  ATTACK 1 — PERPETUAL BASIS: the market's own negative-feedback controller\n${line}`);
  console.log(`
  Unlike every earlier hypothesis, this one needs nobody to be wrong. Funding
  mechanically pulls perp toward index. The only question is whether the
  controller leaves anything on the table after ${COST_BPS} bps of cost.
`);

  const pairs = [['BTCUSDT', 'BTC-USDT'], ['ETHUSDT', 'ETH-USDT'], ['SOLUSDT', 'SOL-USDT']];
  const allTests = [];

  for (const [symbol, instId] of pairs) {
    const rows = loadPair(symbol, instId);
    if (!rows) { console.log(`\n  ${symbol}: index data unavailable — skipped`); continue; }

    const bases = rows.map(r => r.basisBps);
    const sorted = bases.slice().sort((a, b) => a - b);
    const pct = p => sorted[Math.max(0, Math.floor(sorted.length * p) - 1)];
    const days = ((rows[rows.length - 1].ts - rows[0].ts) / 86400000).toFixed(0);

    console.log(`\n  ${symbol} — ${rows.length} aligned bars, ${days} days`);
    console.log(`    basis distribution (bps): p1 ${pct(0.01).toFixed(1)}  p25 ${pct(0.25).toFixed(1)}  median ${pct(0.5).toFixed(1)}  p75 ${pct(0.75).toFixed(1)}  p99 ${pct(0.99).toFixed(1)}`);
    console.log(`    mean ${S.mean(bases).toFixed(2)} bps, sd ${S.stdev(bases).toFixed(2)} bps\n`);

    console.log(`    ${'test'.padEnd(44)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'verdict'.padStart(22)}`);

    const mid = Math.floor(rows.length / 2);

    for (const h of [1, 4, 16]) {
      // ── A. Spread trade: perp return minus index return. ──
      // Fade an extreme basis and collect the convergence.
      for (const [loP, hiP, tag] of [[0.99, 1.01, 'p99'], [0.95, 1.05, 'p95'], [0.90, 1.10, 'p90']]) {
        const hiCut = pct(Math.min(0.999, loP));
        const loCut = pct(Math.max(0.001, 1 - loP));
        const spread = [], f = [], s = [];
        for (let i = 0; i < rows.length - h; i++) {
          const b = rows[i].basisBps;
          let dir = 0;
          if (b >= hiCut) dir = -1;         // perp rich -> short perp, long index
          else if (b <= loCut) dir = +1;    // perp cheap -> long perp, short index
          if (!dir) continue;
          const perpRet = ((rows[i + h].perp - rows[i].perp) / rows[i].perp) * 10000;
          const idxRet = ((rows[i + h].index - rows[i].index) / rows[i].index) * 10000;
          const v = dir * (perpRet - idxRet);
          spread.push(v);
          (i < mid ? f : s).push(v);
        }
        const t = S.hacT(spread, Math.max(0, h - 1));
        if (!t) continue;
        allTests.push({ symbol, kind: 'spread', h, tag, ...t });
        const v = S.verdict(t, S.mean(f), S.mean(s), COST_BPS);
        console.log(`    ${`spread: fade basis beyond ${tag}, ${h}b`.padEnd(44)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${v.padStart(22)}${v === 'SURVIVES' ? '  <--' : ''}`);
        void hiP;
      }

      // ── B. Directional: does extreme basis predict the perp itself? ──
      const hiCut = pct(0.95), loCut = pct(0.05);
      const dirRet = [], f2 = [], s2 = [];
      for (let i = 0; i < rows.length - h; i++) {
        const b = rows[i].basisBps;
        let dir = 0;
        if (b >= hiCut) dir = -1;
        else if (b <= loCut) dir = +1;
        if (!dir) continue;
        const v = dir * ((rows[i + h].perp - rows[i].perp) / rows[i].perp) * 10000;
        dirRet.push(v);
        (i < mid ? f2 : s2).push(v);
      }
      const t2 = S.hacT(dirRet, Math.max(0, h - 1));
      if (t2) {
        allTests.push({ symbol, kind: 'directional', h, tag: 'p95', ...t2 });
        const v2 = S.verdict(t2, S.mean(f2), S.mean(s2), COST_BPS);
        console.log(`    ${`directional: fade basis extreme, ${h}b`.padEnd(44)}${String(t2.n).padStart(7)}${t2.mean.toFixed(2).padStart(10)}${t2.t.toFixed(2).padStart(9)}${v2.padStart(22)}${v2 === 'SURVIVES' ? '  <--' : ''}`);
      }
    }
  }

  const { survivors, tested, threshold } = S.fdr(allTests, 0.10);
  console.log(`\n${line}`);
  console.log(`  FDR across all ${tested} basis tests: ${survivors.length} survive (threshold p <= ${threshold ? threshold.toExponential(2) : 'n/a'})`);
  if (survivors.length) {
    for (const s of survivors.slice(0, 12)) {
      console.log(`    ${s.symbol} ${s.kind} ${s.tag} ${s.h}b: ${s.mean.toFixed(2)} bps, t=${s.t.toFixed(2)}, p=${s.p.toExponential(2)}`);
    }
  }
  console.log(`
  A spread result is worth more than a directional one at the same effect size:
  it carries no market exposure, so it is not competing with every directional
  trader on the venue. It also costs two legs instead of one, which is why the
  ${COST_BPS} bps floor above is if anything too generous for it.
${line}
`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'basis-convergence.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), tests: allTests }, null, 2));
}

main();
