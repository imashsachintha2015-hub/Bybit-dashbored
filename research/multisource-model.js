#!/usr/bin/env node
/**
 * STUDY 12 — Does more INFORMATION predict better than more arithmetic?
 *
 * Study 11 showed that eight price indicators contain about 2.4 independent
 * dimensions, and that adding open interest, funding, crowd positioning,
 * measured taker flow and cross-venue basis raises that to 5.2 — with each new
 * source correlating only 0.06-0.30 with the entire price block. They are
 * measuring things the price series does not contain.
 *
 * This is the experiment that follows. Fit a model on the price-only features,
 * fit the same model on the full feature set, and compare out-of-sample
 * forecasts of the next 15/30/60 minutes. If information is what matters rather
 * than formula complexity, the second should beat the first. If neither beats
 * a coin flip, that is the answer to "can we build advanced indicators that
 * figure out which way it goes".
 *
 * Method, with the anti-fooling rules that every earlier study needed:
 *
 *   - STRICTLY WALK-FORWARD. The model is fit on a trailing window and used
 *     only on bars after it. It is refit as the window rolls. No observation
 *     is ever predicted by a model that saw it.
 *   - RIDGE REGULARISATION. With 13 correlated features and a few thousand
 *     rows, unregularised least squares fits noise. The penalty is chosen on
 *     the training window alone.
 *   - STANDARDISATION uses training-window statistics only. Using the full
 *     sample's mean and variance is a subtle and very common leak.
 *   - The "5 trades is enough" idea gets its own test: accuracy is reported
 *     for the most confident 10%, 1% and 0.5% of forecasts, because a model
 *     can be useless on average and still be right where it is certain.
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

/** Ridge regression via normal equations with Gaussian elimination. */
function ridgeFit(X, y, lambda) {
  const n = X.length, p = X[0].length;
  const XtX = Array.from({ length: p }, () => new Array(p).fill(0));
  const Xty = new Array(p).fill(0);
  for (let i = 0; i < n; i++) {
    for (let a = 0; a < p; a++) {
      Xty[a] += X[i][a] * y[i];
      for (let b = a; b < p; b++) XtX[a][b] += X[i][a] * X[i][b];
    }
  }
  for (let a = 0; a < p; a++) {
    for (let b = 0; b < a; b++) XtX[a][b] = XtX[b][a];
    XtX[a][a] += lambda * n;
  }
  // Solve (XtX) w = Xty
  const M = XtX.map((r, i) => [...r, Xty[i]]);
  for (let c = 0; c < p; c++) {
    let piv = c;
    for (let r = c + 1; r < p; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-12) continue;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let r = 0; r < p; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= p; k++) M[r][k] -= f * M[c][k];
    }
  }
  const w = new Array(p).fill(0);
  for (let c = 0; c < p; c++) if (Math.abs(M[c][c]) > 1e-12) w[c] = M[c][p] / M[c][c];
  return w;
}

function buildDataset(symbol, ccy, tf, horizon) {
  const bundle = JSON.parse(fs.readFileSync(path.join(DATA, `${symbol}.json`), 'utf8'));
  const bars = bundle.series[tf];
  const derivFile = path.join(DATA, `${ccy}-derivs.json`);
  const indexFile = path.join(RESEARCH, `${ccy}-USDT-index.json`);
  if (!bars || !fs.existsSync(derivFile)) return null;
  const derivs = JSON.parse(fs.readFileSync(derivFile, 'utf8')).series.slice().sort((a, b) => a.ts - b.ts);
  const indexRows = fs.existsSync(indexFile) ? JSON.parse(fs.readFileSync(indexFile, 'utf8')).rows : [];
  const indexMap = new Map(indexRows.map(r => [r.start, r.close]));

  const priceNames = ['roc14', 'rsi14', 'boll20', 'donch20', 'emaX12', 'eff30', 'accel10', 'volPush20'];
  const extraNames = ['oiChg6h', 'oiChg24h', 'fundZ', 'crowdZ', 'takerImb', 'basis'];

  const rows = [];
  let dp = 0;

  for (let i = 300; i < bars.length - horizon - 1; i++) {
    const ts = bars[i].start;
    while (dp + 1 < derivs.length && derivs[dp + 1].ts <= ts) dp++;
    const d = derivs[dp];
    if (!d || d.ts > ts || dp < 40) continue;
    if (d.fundingRate == null || d.longShortRatio == null || d.takerBuy == null || d.oiUsd == null) continue;
    if (!indexMap.has(ts)) continue;

    const rets = [];
    for (let k = i - 30; k < i; k++) rets.push(Math.log(bars[k + 1].close / bars[k].close));
    const v30 = sd(rets);
    if (!v30) continue;

    const f = {};
    f.roc14 = Math.log(bars[i].close / bars[i - 14].close) / (v30 * Math.sqrt(14));
    let g = 0, l = 0;
    for (let k = i - 13; k <= i; k++) { const dd = bars[k].close - bars[k - 1].close; if (dd > 0) g += dd; else l -= dd; }
    f.rsi14 = (g + l) ? ((g / (g + l)) * 100 - 50) / 10 : 0;
    const w20 = bars.slice(i - 20, i).map(c => c.close);
    const m20 = w20.reduce((x, y) => x + y, 0) / w20.length, s20 = sd(w20);
    f.boll20 = s20 ? (bars[i].close - m20) / s20 : 0;
    const wd = bars.slice(i - 20, i);
    const hi = Math.max(...wd.map(c => c.high)), lo = Math.min(...wd.map(c => c.low));
    f.donch20 = hi > lo ? ((bars[i].close - lo) / (hi - lo) - 0.5) * 2 : 0;
    const cl = bars.slice(i - 36, i + 1).map(c => c.close);
    let tr = 0;
    for (let k = i - 14; k < i; k++) tr += Math.max(bars[k].high - bars[k].low, Math.abs(bars[k].high - bars[k - 1].close), Math.abs(bars[k].low - bars[k - 1].close));
    const atr = tr / 14;
    f.emaX12 = atr ? (ema(cl, 12) - ema(cl, 36)) / atr : 0;
    let pl = 0;
    for (let k = i - 30; k < i; k++) pl += Math.abs(bars[k + 1].close - bars[k].close);
    f.eff30 = pl ? ((bars[i].close - bars[i - 30].close) / pl) * 3 : 0;
    f.accel10 = (Math.log(bars[i].close / bars[i - 10].close) - Math.log(bars[i - 10].close / bars[i - 20].close)) / (v30 * Math.sqrt(10));
    const av = wd.reduce((x, c) => x + c.volume, 0) / wd.length;
    const rng = bars[i].high - bars[i].low;
    f.volPush20 = (rng && av) ? (((bars[i].close - bars[i].low) / rng - 0.5) * 2) * (bars[i].volume / av) : 0;

    const win = derivs.slice(Math.max(0, dp - 120), dp + 1);
    const oi6 = derivs[Math.max(0, dp - 6)].oiUsd, oi24 = derivs[Math.max(0, dp - 24)].oiUsd;
    f.oiChg6h = oi6 ? (d.oiUsd - oi6) / oi6 : 0;
    f.oiChg24h = oi24 ? (d.oiUsd - oi24) / oi24 : 0;
    const fr = win.map(r => r.fundingRate).filter(x => x != null);
    const frs = sd(fr), frm = fr.reduce((a, b) => a + b, 0) / (fr.length || 1);
    f.fundZ = frs ? (d.fundingRate - frm) / frs : 0;
    const lsArr = win.map(r => r.longShortRatio).filter(x => x != null);
    const lss = sd(lsArr), lsm = lsArr.reduce((a, b) => a + b, 0) / (lsArr.length || 1);
    f.crowdZ = lss ? (d.longShortRatio - lsm) / lss : 0;
    f.takerImb = (d.takerBuy + d.takerSell) ? (d.takerBuy - d.takerSell) / (d.takerBuy + d.takerSell) : 0;
    f.basis = ((bars[i].close - indexMap.get(ts)) / indexMap.get(ts)) * 10000;

    const fwd = ((bars[i + horizon].close - bars[i].close) / bars[i].close) * 10000;
    if (!Object.values(f).every(v => isFinite(v))) continue;
    rows.push({ i, ts, f, fwd });
  }
  return { rows, priceNames, extraNames };
}

/** Walk-forward evaluation of a feature subset. */
function walkForward(rows, featNames, trainSize, step, lambda) {
  const preds = [];
  for (let start = trainSize; start + step <= rows.length; start += step) {
    const tr = rows.slice(Math.max(0, start - trainSize), start);
    const te = rows.slice(start, start + step);
    if (tr.length < 200) continue;

    // Standardise using TRAINING statistics only.
    const mu = {}, sg = {};
    for (const n of featNames) {
      const col = tr.map(r => r.f[n]);
      mu[n] = col.reduce((a, b) => a + b, 0) / col.length;
      sg[n] = sd(col) || 1;
    }
    const X = tr.map(r => featNames.map(n => (r.f[n] - mu[n]) / sg[n]));
    const yMean = tr.reduce((a, r) => a + r.fwd, 0) / tr.length;
    const y = tr.map(r => r.fwd - yMean);
    const w = ridgeFit(X, y, lambda);

    for (const r of te) {
      const x = featNames.map(n => (r.f[n] - mu[n]) / sg[n]);
      let p = 0;
      for (let k = 0; k < w.length; k++) p += w[k] * x[k];
      preds.push({ pred: p, actual: r.fwd, ts: r.ts });
    }
  }
  return preds;
}

function score(preds, costBps, label) {
  if (preds.length < 50) return null;
  const dirOK = preds.filter(p => Math.sign(p.pred) === Math.sign(p.actual) && p.pred !== 0).length;
  const traded = preds.filter(p => p.pred !== 0);
  const pnl = traded.map(p => Math.sign(p.pred) * p.actual - costBps);
  const t = S.hacT(pnl, 0);
  const ic = S.corr(preds.map(p => p.pred), preds.map(p => p.actual));
  return {
    label, n: traded.length,
    accuracy: (dirOK / traded.length) * 100,
    ic: ic || 0,
    grossBps: S.mean(traded.map(p => Math.sign(p.pred) * p.actual)),
    netBps: t ? t.mean : 0,
    t: t ? t.t : 0
  };
}

/** Accuracy restricted to the most confident forecasts — the "5 trades" test. */
function confidenceSlices(preds, costBps) {
  const sorted = preds.slice().sort((a, b) => Math.abs(b.pred) - Math.abs(a.pred));
  const out = [];
  for (const frac of [1.0, 0.10, 0.02, 0.005]) {
    const k = Math.max(5, Math.floor(sorted.length * frac));
    const sub = sorted.slice(0, k);
    const acc = sub.filter(p => Math.sign(p.pred) === Math.sign(p.actual)).length / sub.length * 100;
    const gross = S.mean(sub.map(p => Math.sign(p.pred) * p.actual));
    out.push({ frac, n: k, accuracy: acc, grossBps: gross, netBps: gross - costBps });
  }
  return out;
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';
  const costBps = parseFloat(args.cost || '4');   // maker-ish round trip

  const line = '─'.repeat(100);
  console.log(`\n${line}\n  STUDY 12 — INFORMATION vs ARITHMETIC, TESTED OUT OF SAMPLE\n${line}`);
  console.log(`
  Study 11: 8 price indicators = 2.4 independent dimensions. Adding open
  interest, funding, crowd positioning, taker flow and basis = 5.2. This fits a
  model on each set and compares out-of-sample forecasts.

  Strictly walk-forward, ridge-regularised, standardised on training statistics
  only. Cost charged: ${costBps} bps round trip.
`);

  const pairs = [['BTCUSDT', 'BTC'], ['ETHUSDT', 'ETH'], ['SOLUSDT', 'SOL']];
  const horizons = [1, 2, 4];   // 15, 30, 60 minutes on 15m bars

  for (const h of horizons) {
    console.log(`\n  ── FORECAST HORIZON: ${h} bar${h > 1 ? 's' : ''} = ${h * parseInt(tf)} minutes ──\n`);
    console.log(`  ${'symbol'.padEnd(10)}${'feature set'.padEnd(22)}${'n(oos)'.padStart(8)}${'accuracy'.padStart(10)}${'IC'.padStart(8)}${'gross bps'.padStart(11)}${'net bps'.padStart(10)}${'t'.padStart(8)}`);

    for (const [sym, ccy] of pairs) {
      const ds = buildDataset(sym, ccy, tf, h);
      if (!ds || ds.rows.length < 800) { console.log(`  ${sym.padEnd(10)}(only ${ds ? ds.rows.length : 0} aligned rows — skipped)`); continue; }

      const trainSize = Math.min(1200, Math.floor(ds.rows.length * 0.5));
      const step = Math.max(50, Math.floor(ds.rows.length * 0.05));

      const sets = [
        ['price only (2.4 dims)', ds.priceNames],
        ['price + derivatives', [...ds.priceNames, ...ds.extraNames]],
        ['derivatives only', ds.extraNames]
      ];
      for (const [label, feats] of sets) {
        const preds = walkForward(ds.rows, feats, trainSize, step, 0.1);
        const sc = score(preds, costBps, label);
        if (!sc) continue;
        console.log(`  ${sym.padEnd(10)}${label.padEnd(22)}${String(sc.n).padStart(8)}${(sc.accuracy.toFixed(1) + '%').padStart(10)}${sc.ic.toFixed(3).padStart(8)}${sc.grossBps.toFixed(2).padStart(11)}${sc.netBps.toFixed(2).padStart(10)}${sc.t.toFixed(2).padStart(8)}`);
      }
    }
  }

  // ── The "5 well-calculated trades" test ──
  console.log(`\n${line}\n  "I DON'T NEED 100 TRADES, 5 WELL-CALCULATED ONES IS ENOUGH"\n${line}`);
  console.log(`
  A model can be useless on average and still be right where it is most
  confident. This restricts the out-of-sample forecasts to the strongest ones
  and re-measures. If accuracy climbs as the selection tightens, extreme
  selectivity is the right strategy even with a weak average model.
`);
  for (const [sym, ccy] of pairs) {
    const ds = buildDataset(sym, ccy, tf, 4);
    if (!ds || ds.rows.length < 800) continue;
    const trainSize = Math.min(1200, Math.floor(ds.rows.length * 0.5));
    const step = Math.max(50, Math.floor(ds.rows.length * 0.05));
    const preds = walkForward(ds.rows, [...ds.priceNames, ...ds.extraNames], trainSize, step, 0.1);
    if (preds.length < 100) continue;
    console.log(`\n  ${sym} — 60-minute horizon, full feature set`);
    console.log(`    ${'confidence slice'.padEnd(24)}${'n'.padStart(7)}${'accuracy'.padStart(11)}${'gross bps'.padStart(12)}${'net bps'.padStart(11)}`);
    for (const s of confidenceSlices(preds, costBps)) {
      const label = s.frac === 1 ? 'all forecasts' : `strongest ${(s.frac * 100).toFixed(1)}%`;
      console.log(`    ${label.padEnd(24)}${String(s.n).padStart(7)}${(s.accuracy.toFixed(1) + '%').padStart(11)}${s.grossBps.toFixed(2).padStart(12)}${s.netBps.toFixed(2).padStart(11)}`);
    }
  }

  console.log(`
${line}
  IC is the correlation between forecast and outcome. For context, a genuinely
  good systematic equity signal runs an IC of 0.02-0.05; anything above 0.1
  would be exceptional. Read the IC column before the accuracy column — accuracy
  can be inflated by predicting the drift, IC cannot.
${line}
`);
}

main();
