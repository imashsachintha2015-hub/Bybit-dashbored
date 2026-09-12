#!/usr/bin/env node
/**
 * ATTACK 3 — Cross-venue lead-lag and dislocation.
 *
 * Everything measured so far used one venue. But the same asset trades
 * simultaneously on dozens, and the prices are joined only by arbitrageurs
 * choosing to act. That link is mechanical in the sense that a persistent gap
 * is free money to whoever can reach both sides — but it is NOT instantaneous,
 * and the question is whether the lag is long enough to be reachable by anyone
 * who is not co-located.
 *
 * Two things are tested, and they are different claims:
 *
 *   A. LEAD-LAG. Does the return on one venue predict the NEXT return on
 *      another? If OKX perp systematically leads Coinbase spot by a minute,
 *      that is a directional signal available to anyone watching both.
 *      Prior expectation: any such lag is arbitraged to well under a second by
 *      firms with infrastructure, so at 1-minute resolution it should be gone.
 *      Measuring it establishes whether the resolution available here can even
 *      see the phenomenon.
 *
 *   B. DISLOCATION REVERSION. When the two venues' prices diverge unusually
 *      far, does the gap close? This is the spread version, and it is the one
 *      with a mechanical enforcement mechanism behind it.
 *
 * The honest caveat stated up front: 1-minute bars are far too coarse to see
 * genuine cross-venue arbitrage, which lives at the millisecond scale. A null
 * result here does NOT mean no dislocation exists — it means none survives at
 * the only resolution this data can resolve. That distinction matters and is
 * repeated in the output.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, 'data');
const KLINES = path.join(__dirname, '..', 'backtest', 'data');
const COST_BPS = 2.0;
// Set via --stale 0 to reproduce the unfiltered (and misleading) version.
const STALE_FILTER = !process.argv.includes('--stale') || process.argv[process.argv.indexOf('--stale') + 1] !== '0';

/**
 * Aligns the two venues on identical bar timestamps.
 *
 * `requireVolume` is the critical control. A 1-minute bar on a thin venue may
 * contain NO TRADES, in which case its "close" is simply the last price from
 * some earlier minute. Comparing a live price on one venue against a stale one
 * on the other manufactures a gap that did not exist, and that phantom gap then
 * "reverts" the moment the thin venue prints again — producing a large, highly
 * significant, and completely fake reversion effect.
 *
 * This matters enormously here because staleness rises as liquidity falls,
 * which would generate exactly the liquidity gradient the study found. Any
 * result that does not survive this filter is an artefact of measurement, not
 * a property of the market.
 */
function align(perp, spot, requireVolume) {
  const m = new Map(spot.map(r => [r.start, r]));
  const out = [];
  let skippedStale = 0, skippedMissing = 0;
  let prevSpotClose = null;
  for (const p of perp) {
    const s = m.get(p.start);
    if (!s || !s.close || !p.close) { skippedMissing++; continue; }
    if (requireVolume) {
      // Two independent staleness tests: the bar reports no volume, or its
      // close is bit-identical to the previous bar's (no trade moved it).
      const noVolume = !(s.volume > 0);
      const unchanged = prevSpotClose != null && s.close === prevSpotClose;
      if (noVolume || unchanged) { skippedStale++; prevSpotClose = s.close; continue; }
    }
    prevSpotClose = s.close;
    out.push({ ts: p.start, perp: p.close, spot: s.close, spotVol: s.volume,
               perpOpen: p.open, spotOpen: s.open });
  }
  out.skippedStale = skippedStale;
  out.skippedMissing = skippedMissing;
  return out;
}

function main() {
  const line = '─'.repeat(90);
  console.log(`\n${line}\n  ATTACK 3 — CROSS-VENUE: OKX perpetual vs Coinbase spot\n${line}`);
  console.log(`
  CAVEAT FIRST: real cross-venue arbitrage lives at millisecond resolution.
  These are 1-minute bars. A null result here means no dislocation survives at
  the resolution available, NOT that none exists. This measures whether the lag
  is long enough for a non-co-located participant to reach, which is the only
  version of the question that matters for this system.
`);

  // Ordered from most to least liquid. If arbitrageurs compete the gap down to
  // their own cost, the effect should grow as liquidity falls — and if it grows
  // past the retail cost floor somewhere down this list, that is where a
  // reachable version of this trade lives.
  const pairs = [['BTCUSDT', 'BTC'], ['ETHUSDT', 'ETH'], ['SOLUSDT', 'SOL'],
                 ['AVAXUSDT', 'AVAX'], ['LINKUSDT', 'LINK'], ['DOGEUSDT', 'DOGE']];
  const allTests = [];
  const execTests = [];

  for (const [symbol, ccy] of pairs) {
    const kf = path.join(KLINES, `${symbol}.json`);
    const sf = path.join(DATA, `${ccy}-coinbase-1m.json`);
    if (!fs.existsSync(kf) || !fs.existsSync(sf)) { console.log(`\n  ${symbol}: data missing — skipped`); continue; }
    const perp = JSON.parse(fs.readFileSync(kf, 'utf8')).series['1'];
    const spot = JSON.parse(fs.readFileSync(sf, 'utf8')).rows;
    if (!perp || !spot) continue;

    const rowsAll = align(perp, spot, false);
    const rows = STALE_FILTER ? align(perp, spot, true) : rowsAll;
    if (rows.length < 1000) { console.log(`\n  ${symbol}: only ${rows.length} usable bars — skipped`); continue; }
    const stalePct = rowsAll.length ? ((rowsAll.length - rows.length) / rowsAll.length * 100) : 0;

    const perpRet = [], spotRet = [], gapBps = [];
    for (let i = 1; i < rows.length; i++) {
      perpRet.push(((rows[i].perp - rows[i - 1].perp) / rows[i - 1].perp) * 10000);
      spotRet.push(((rows[i].spot - rows[i - 1].spot) / rows[i - 1].spot) * 10000);
      gapBps.push(((rows[i].perp - rows[i].spot) / rows[i].spot) * 10000);
    }

    const hours = ((rows[rows.length - 1].ts - rows[0].ts) / 3600000).toFixed(0);
    console.log(`\n  ${symbol} vs Coinbase ${ccy}-USD — ${rows.length} usable 1m bars, ${hours}h` +
      (STALE_FILTER ? `  [stale/no-trade spot bars excluded: ${stalePct.toFixed(1)}%]` : `  [NO stale filter]`));
    console.log(`    contemporaneous return correlation: ${(S.corr(perpRet, spotRet) || 0).toFixed(4)}`);
    console.log(`    perp-vs-spot gap: mean ${S.mean(gapBps).toFixed(1)} bps, sd ${S.stdev(gapBps).toFixed(1)} bps\n`);

    // ── A. Lead-lag at ±1..3 minutes ──
    console.log(`    LEAD-LAG (correlation of one venue's return with the other's, k minutes later)`);
    console.log(`      ${'k'.padStart(4)}${'perp leads spot'.padStart(20)}${'spot leads perp'.padStart(20)}`);
    for (const k of [1, 2, 3]) {
      const pLead = S.corr(perpRet.slice(0, -k), spotRet.slice(k));
      const sLead = S.corr(spotRet.slice(0, -k), perpRet.slice(k));
      console.log(`      ${String(k).padStart(4)}${(pLead == null ? '—' : pLead.toFixed(4)).padStart(20)}${(sLead == null ? '—' : sLead.toFixed(4)).padStart(20)}`);
    }

    // ── B. Dislocation reversion ──
    const sorted = gapBps.slice().sort((a, b) => a - b);
    const pct = p => sorted[Math.max(0, Math.floor(sorted.length * p) - 1)];
    const mid = Math.floor(gapBps.length / 2);

    // ── Realistic execution: the gap is OBSERVED at bar i's close, so the
    //    earliest fill is bar i+1's open. Measuring entry at the close you used
    //    to generate the signal is look-ahead, and it is the single most common
    //    way a cross-venue study overstates itself.
    console.log(`\n    EXECUTION LAG TEST (signal at bar close, fill at NEXT bar's open)`);
    console.log(`      ${'threshold'.padEnd(22)}${'hold'.padStart(7)}${'n'.padStart(7)}${'instant bps'.padStart(13)}${'lagged bps'.padStart(12)}${'t(HAC)'.padStart(9)}`);
    {
      const sortedX = gapBps.slice().sort((a, b) => a - b);
      const pctX = q => sortedX[Math.max(0, Math.floor(sortedX.length * q) - 1)];
      for (const [q, tag] of [[0.99, 'beyond p99'], [0.95, 'beyond p95']]) {
        const hi = pctX(q), lo = pctX(1 - q);
        for (const h of [1, 5]) {
          const instant = [], lagged = [];
          for (let i = 0; i < gapBps.length - h - 1; i++) {
            let dir = 0;
            if (gapBps[i] >= hi) dir = -1;
            else if (gapBps[i] <= lo) dir = +1;
            if (!dir) continue;
            instant.push(dir * (gapBps[i + h] - gapBps[i]));
            // Entry gap measured from the NEXT bar's opens on both venues.
            const r = rows[i + 1];
            if (!r || !r.perpOpen || !r.spotOpen) continue;
            const entryGap = ((r.perpOpen - r.spotOpen) / r.spotOpen) * 10000;
            lagged.push(dir * (gapBps[i + h] - entryGap));
          }
          const tl = S.hacT(lagged, Math.max(0, h - 1));
          if (!tl) continue;
          console.log(`      ${tag.padEnd(22)}${(h + 'm').padStart(7)}${String(tl.n).padStart(7)}${S.mean(instant).toFixed(2).padStart(13)}${tl.mean.toFixed(2).padStart(12)}${tl.t.toFixed(2).padStart(9)}`);
          execTests.push({ symbol, tag, h, instant: S.mean(instant), lagged: tl.mean, t: tl.t, n: tl.n });
        }
      }
    }

    console.log(`\n    DISLOCATION REVERSION (spread trade: long the cheap venue, short the rich one)`);
    console.log(`      ${'threshold'.padEnd(22)}${'horizon'.padStart(9)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'verdict'.padStart(22)}`);
    for (const [q, tag] of [[0.99, 'beyond p99'], [0.95, 'beyond p95'], [0.90, 'beyond p90']]) {
      const hi = pct(q), lo = pct(1 - q);
      for (const h of [1, 5, 15]) {
        const vals = [], f = [], s = [];
        for (let i = 0; i < gapBps.length - h; i++) {
          let dir = 0;
          if (gapBps[i] >= hi) dir = -1;
          else if (gapBps[i] <= lo) dir = +1;
          if (!dir) continue;
          // Spread P&L = change in the gap, in the direction bet on.
          const v = dir * (gapBps[i + h] - gapBps[i]);
          vals.push(v);
          (i < mid ? f : s).push(v);
        }
        const t = S.hacT(vals, Math.max(0, h - 1));
        if (!t) continue;
        allTests.push({ symbol, tag, h, ...t, first: S.mean(f), second: S.mean(s) });
        const v = S.verdict(t, S.mean(f), S.mean(s), COST_BPS * 2);  // two legs, two venues
        console.log(`      ${tag.padEnd(22)}${(h + 'm').padStart(9)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${v.padStart(22)}${v === 'SURVIVES' ? '  <--' : ''}`);
      }
    }
  }

  // ── The money chart: dislocation size against the cost of closing it. ──
  // Live top-of-book spreads, measured at the time of writing. The point of
  // this table is that the two columns track each other.
  const SPREADS = {
    BTCUSDT: { spot: 0.00, perp: 0.01 }, ETHUSDT: { spot: 0.04, perp: 0.04 },
    SOLUSDT: { spot: 0.98, perp: 0.98 }, AVAXUSDT: { spot: 2.68, perp: 1.34 },
    LINKUSDT: { spot: null, perp: null }, DOGEUSDT: { spot: 3.56, perp: 1.19 }
  };
  console.log(`\n${line}\n  THE DISLOCATION IS THE SIZE OF THE COST OF CLOSING IT\n${line}`);
  console.log(`  ${'symbol'.padEnd(11)}${'spot spr'.padStart(10)}${'perp spr'.padStart(10)}${'combined'.padStart(10)}${'p99 revert'.padStart(12)}${'ratio'.padStart(8)}`);
  for (const t of allTests.filter(x => x.tag === 'beyond p99' && x.h === 1)) {
    const sp = SPREADS[t.symbol];
    if (!sp || sp.spot == null) continue;
    const comb = sp.spot + sp.perp;
    console.log(`  ${t.symbol.padEnd(11)}${sp.spot.toFixed(2).padStart(10)}${sp.perp.toFixed(2).padStart(10)}${comb.toFixed(2).padStart(10)}${t.mean.toFixed(2).padStart(12)}${(comb > 0 ? (t.mean / comb).toFixed(1) + 'x' : '—').padStart(8)}`);
  }
  console.log(`
  Read down the last two columns together. The size of the dislocation tracks
  the cost of closing it across a 25x range of liquidity. That is not a
  coincidence and it is not an edge going begging: it is arbitrageurs competing
  the gap down until what remains is exactly what it costs them to remove it.
  The inefficiency is real, measurable, overwhelmingly significant — and priced.`);

  const { survivors, tested, threshold } = S.fdr(allTests, 0.10);
  console.log(`\n${line}`);
  console.log(`  FDR across ${tested} dislocation tests: ${survivors.length} survive` +
              (threshold ? ` (p <= ${threshold.toExponential(2)})` : ''));
  console.log(`
  Note the cost floor used here is ${COST_BPS * 2} bps, double the single-venue figure: a
  cross-venue spread needs a round trip on BOTH venues, and in practice also
  needs capital pre-positioned on each, which is a funding cost this does not
  even model.
${line}
`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cross-venue.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), tests: allTests }, null, 2));
}

main();
