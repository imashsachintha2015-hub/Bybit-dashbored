#!/usr/bin/env node
/**
 * STUDY 11 — How much INFORMATION is in an indicator set, versus how many
 * indicators are in it?
 *
 * A fair criticism of the 2,448-configuration grid: those ten indicators are
 * all functions of the same OHLCV series. RSI, rate-of-change, Bollinger
 * position, Donchian position and an EMA spread are different formulas applied
 * to one stream of numbers. No transform of a series can add information the
 * series does not contain — it can only re-present it.
 *
 * So the grid was not 2,448 independent attempts at prediction. It was 2,448
 * rotations of one piece of information, and finding that they all land at 50%
 * is much weaker evidence than it looked, because they were never independent.
 *
 * This study measures that directly:
 *
 *   1. The correlation matrix across the indicator set, and its EFFECTIVE RANK
 *      (via the participation ratio of the eigenvalue spectrum). If ten
 *      indicators have an effective rank near two, then there are about two
 *      independent things being measured and eight redundant restatements.
 *
 *   2. The same calculation with genuinely DIFFERENT data sources mixed in —
 *      open interest, funding, crowd positioning, measured taker flow,
 *      cross-venue basis. If those add rank, they are adding information, and
 *      information is the only thing that can move a forecast.
 *
 * The point being tested: the way to a better forecast is NEW DATA, not new
 * arithmetic on old data. This measures whether that claim is true here.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const RESEARCH = path.join(__dirname, 'data');

function sd(a) {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}
function ema(vals, p) {
  const k = 2 / (p + 1);
  let e = vals[0];
  for (let i = 1; i < vals.length; i++) e = vals[i] * k + e * (1 - k);
  return e;
}

/**
 * Effective rank via the participation ratio of the eigenvalue spectrum:
 *   (sum of eigenvalues)^2 / sum of (eigenvalues^2)
 * For k perfectly independent standardised series this equals k. For k
 * identical series it equals 1. It answers "how many independent things am I
 * actually looking at", which is the question that matters here.
 */
function effectiveRank(matrix) {
  const k = matrix.length;
  const C = [];
  for (let i = 0; i < k; i++) {
    C.push([]);
    for (let j = 0; j < k; j++) C[i][j] = i === j ? 1 : (S.corr(matrix[i], matrix[j]) || 0);
  }
  // Symmetric eigenvalues by Jacobi rotation — k is small, so this is ample.
  const A = C.map(r => r.slice());
  for (let sweep = 0; sweep < 100; sweep++) {
    let off = 0;
    for (let i = 0; i < k; i++) for (let j = i + 1; j < k; j++) off += A[i][j] * A[i][j];
    if (off < 1e-12) break;
    for (let p = 0; p < k; p++) {
      for (let q = p + 1; q < k; q++) {
        if (Math.abs(A[p][q]) < 1e-14) continue;
        const theta = (A[q][q] - A[p][p]) / (2 * A[p][q]);
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1), s = t * c;
        for (let i = 0; i < k; i++) {
          const aip = A[i][p], aiq = A[i][q];
          A[i][p] = c * aip - s * aiq;
          A[i][q] = s * aip + c * aiq;
        }
        for (let i = 0; i < k; i++) {
          const api = A[p][i], aqi = A[q][i];
          A[p][i] = c * api - s * aqi;
          A[q][i] = s * api + c * aqi;
        }
      }
    }
  }
  const eig = [];
  for (let i = 0; i < k; i++) eig.push(Math.max(0, A[i][i]));
  const sum = eig.reduce((a, b) => a + b, 0);
  const sumSq = eig.reduce((a, b) => a + b * b, 0);
  return { rank: sumSq > 0 ? (sum * sum) / sumSq : 0, eigenvalues: eig.sort((a, b) => b - a), corr: C };
}

function main() {
  const tf = '15';
  const line = '─'.repeat(96);
  console.log(`\n${line}\n  STUDY 11 — INFORMATION vs ARITHMETIC\n${line}`);

  const bundle = JSON.parse(fs.readFileSync(path.join(DATA, 'BTCUSDT.json'), 'utf8'));
  const bars = bundle.series[tf];

  // ── Price-only indicator set: the kind everyone builds. ──
  const priceFeatures = {
    'roc_14': i => {
      const rets = [];
      for (let k = i - 14; k < i; k++) rets.push(Math.log(bars[k + 1].close / bars[k].close));
      const v = sd(rets);
      return v ? Math.log(bars[i].close / bars[i - 14].close) / (v * Math.sqrt(14)) : null;
    },
    'rsi_14': i => {
      let g = 0, l = 0;
      for (let k = i - 13; k <= i; k++) {
        const d = bars[k].close - bars[k - 1].close;
        if (d > 0) g += d; else l -= d;
      }
      return (g + l) ? ((g / (g + l)) * 100 - 50) / 10 : 0;
    },
    'bollinger_20': i => {
      const w = bars.slice(i - 20, i).map(c => c.close);
      const m = w.reduce((x, y) => x + y, 0) / w.length;
      const s = sd(w);
      return s ? (bars[i].close - m) / s : null;
    },
    'donchian_20': i => {
      const w = bars.slice(i - 20, i);
      const hi = Math.max(...w.map(c => c.high)), lo = Math.min(...w.map(c => c.low));
      return hi > lo ? ((bars[i].close - lo) / (hi - lo) - 0.5) * 2 : null;
    },
    'emaCross_12': i => {
      const cl = bars.slice(i - 36, i + 1).map(c => c.close);
      const f = ema(cl, 12), s = ema(cl, 36);
      let tr = 0;
      for (let k = i - 14; k < i; k++) tr += Math.max(bars[k].high - bars[k].low, Math.abs(bars[k].high - bars[k - 1].close), Math.abs(bars[k].low - bars[k - 1].close));
      const a = tr / 14;
      return a ? (f - s) / a : null;
    },
    'efficiency_30': i => {
      const net = bars[i].close - bars[i - 30].close;
      let pl = 0;
      for (let k = i - 30; k < i; k++) pl += Math.abs(bars[k + 1].close - bars[k].close);
      return pl ? (net / pl) * 3 : null;
    },
    'accel_10': i => {
      const r1 = Math.log(bars[i].close / bars[i - 10].close);
      const r2 = Math.log(bars[i - 10].close / bars[i - 20].close);
      const rets = [];
      for (let k = i - 20; k < i; k++) rets.push(Math.log(bars[k + 1].close / bars[k].close));
      const v = sd(rets);
      return v ? (r1 - r2) / (v * Math.sqrt(10)) : null;
    },
    'volPush_20': i => {
      const w = bars.slice(i - 20, i);
      const av = w.reduce((x, c) => x + c.volume, 0) / w.length;
      const b = bars[i], rng = b.high - b.low;
      return (rng && av) ? (((b.close - b.low) / rng - 0.5) * 2) * (b.volume / av) : null;
    }
  };

  const idx = [];
  const cols = {};
  for (const name of Object.keys(priceFeatures)) cols[name] = [];
  for (let i = 300; i < bars.length; i++) {
    const row = {};
    let ok = true;
    for (const [name, fn] of Object.entries(priceFeatures)) {
      const v = fn(i);
      if (v == null || !isFinite(v)) { ok = false; break; }
      row[name] = v;
    }
    if (!ok) continue;
    idx.push(i);
    for (const name of Object.keys(priceFeatures)) cols[name].push(row[name]);
  }

  const names = Object.keys(priceFeatures);
  const mat = names.map(n => cols[n]);
  const er = effectiveRank(mat);

  console.log(`\n  PRICE-ONLY INDICATOR SET — ${names.length} indicators, ${idx.length} observations\n`);
  console.log(`    ${''.padEnd(14)}${names.map(n => n.slice(0, 8).padStart(9)).join('')}`);
  for (let i = 0; i < names.length; i++) {
    console.log(`    ${names[i].padEnd(14)}${er.corr[i].map(v => v.toFixed(2).padStart(9)).join('')}`);
  }

  const absCorrs = [];
  for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) absCorrs.push(Math.abs(er.corr[i][j]));
  console.log(`\n    mean |correlation| between indicators: ${S.mean(absCorrs).toFixed(3)}`);
  console.log(`    EFFECTIVE RANK: ${er.rank.toFixed(2)} out of ${names.length}`);
  console.log(`    eigenvalues: ${er.eigenvalues.map(v => v.toFixed(2)).join(', ')}`);
  console.log(`
    Effective rank answers "how many independent things am I actually looking
    at". ${names.length} indicators with an effective rank of ${er.rank.toFixed(1)} means roughly ${Math.round(er.rank)} genuinely
    distinct measurements and ${names.length - Math.round(er.rank)} restatements of those.

    This is why the grid's 2,448 configurations all landed at 50%: they were not
    2,448 independent attempts at prediction. Adding another price transform —
    however advanced its formula — adds arithmetic, not information.`);

  // ── Now add genuinely different data sources. ──
  const ccy = 'BTC';
  const derivFile = path.join(DATA, `${ccy}-derivs.json`);
  const indexFile = path.join(RESEARCH, `${ccy}-USDT-index.json`);
  if (!fs.existsSync(derivFile)) {
    console.log(`\n  (derivatives data missing — run backtest/fetch-derivatives.js)`);
    return;
  }
  const derivs = JSON.parse(fs.readFileSync(derivFile, 'utf8')).series;
  const indexRows = fs.existsSync(indexFile) ? JSON.parse(fs.readFileSync(indexFile, 'utf8')).rows : [];
  const indexMap = new Map(indexRows.map(r => [r.start, r.close]));

  // Align derivatives (hourly) onto the 15m grid by carrying the last known row.
  const dSorted = derivs.slice().sort((a, b) => a.ts - b.ts);
  let dp = 0;
  const extraCols = { oiChange6h: [], fundingZ: [], crowdRatio: [], takerImb: [], basisBps: [] };
  const keepIdx = [];

  const zOf = (arr, v) => {
    const s = sd(arr);
    const m = arr.reduce((a, b) => a + b, 0) / arr.length;
    return s ? (v - m) / s : 0;
  };

  for (let n = 0; n < idx.length; n++) {
    const i = idx[n];
    const ts = bars[i].start;
    while (dp + 1 < dSorted.length && dSorted[dp + 1].ts <= ts) dp++;
    const d = dSorted[dp];
    if (!d || d.ts > ts || dp < 30) continue;
    const win = dSorted.slice(Math.max(0, dp - 120), dp + 1);

    const oiNow = d.oiUsd, oiPast = dSorted[Math.max(0, dp - 6)].oiUsd;
    if (!oiNow || !oiPast) continue;
    const fundings = win.map(r => r.fundingRate).filter(v => v != null);
    const ls = d.longShortRatio;
    if (d.fundingRate == null || ls == null || d.takerBuy == null || d.takerSell == null) continue;

    const basis = indexMap.has(ts) ? ((bars[i].close - indexMap.get(ts)) / indexMap.get(ts)) * 10000 : null;
    if (basis == null) continue;

    keepIdx.push(n);
    extraCols.oiChange6h.push((oiNow - oiPast) / oiPast);
    extraCols.fundingZ.push(fundings.length > 10 ? zOf(fundings, d.fundingRate) : 0);
    extraCols.crowdRatio.push(ls);
    extraCols.takerImb.push((d.takerBuy - d.takerSell) / (d.takerBuy + d.takerSell));
    extraCols.basisBps.push(basis);
  }

  if (keepIdx.length < 500) {
    console.log(`\n  (only ${keepIdx.length} aligned rows with derivatives + index — too few)`);
    return;
  }

  const priceSub = names.map(n => keepIdx.map(k => cols[n][k]));
  const allNames = [...names, ...Object.keys(extraCols)];
  const allMat = [...priceSub, ...Object.keys(extraCols).map(k => extraCols[k])];
  const er2 = effectiveRank(allMat);
  const erPriceSub = effectiveRank(priceSub);

  console.log(`\n${line}\n  ADDING GENUINELY DIFFERENT DATA SOURCES\n${line}`);
  console.log(`\n  ${keepIdx.length} rows where price, derivatives and index data all align.\n`);
  console.log(`    price-only (${names.length} features):                    effective rank ${erPriceSub.rank.toFixed(2)}`);
  console.log(`    price + OI/funding/crowd/taker/basis (${allNames.length}):  effective rank ${er2.rank.toFixed(2)}`);
  console.log(`    information added by the 5 non-price sources:   +${(er2.rank - erPriceSub.rank).toFixed(2)} dimensions`);

  // How correlated is each new source with the price block? Low = new information.
  console.log(`\n  ${'non-price feature'.padEnd(20)}${'max |corr| with any price indicator'.padStart(38)}`);
  for (const k of Object.keys(extraCols)) {
    let mx = 0, which = '';
    for (let i = 0; i < names.length; i++) {
      const c = Math.abs(S.corr(extraCols[k], priceSub[i]) || 0);
      if (c > mx) { mx = c; which = names[i]; }
    }
    console.log(`  ${k.padEnd(20)}${(mx.toFixed(3) + '  (' + which + ')').padStart(38)}`);
  }

  console.log(`
  A non-price feature whose maximum correlation with the entire price block is
  low is measuring something the price series does not contain. Those are the
  only features that can change a forecast; everything else is a restatement.
${line}
`);
}

main();
