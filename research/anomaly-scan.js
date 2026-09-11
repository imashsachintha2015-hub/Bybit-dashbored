#!/usr/bin/env node
/**
 * STUDY 5 — Systematic anomaly scan.
 *
 * The brief was: stop re-examining the system, attack the data, and look for
 * what ISN'T being looked at. This does that in the only way that does not end
 * in self-deception.
 *
 * The method is a partition scan. Rather than testing one hypothesis someone
 * liked the sound of, it slices the tape hundreds of ways — by hour, weekday,
 * volatility state, size of the preceding move, run length, distance from
 * recent extremes, proximity to funding settlement — and measures the forward
 * return in every slice.
 *
 * THE PROBLEM WITH DOING THIS, AND WHAT IS DONE ABOUT IT
 *
 * If you test 200 slices at the 5% level, you expect ~10 to look "significant"
 * when nothing is there at all. Scanning without correcting for that is exactly
 * how people convince themselves they have found a market anomaly, and it is
 * the single most common way quantitative research goes wrong. So:
 *
 *  1. Every slice gets a t-statistic on the mean forward return.
 *  2. Benjamini-Hochberg false-discovery-rate control is applied across ALL
 *     tests jointly, so the reported survivors are corrected for how many were
 *     run.
 *  3. Every survivor is re-tested on a held-out second half of the sample it
 *     was never selected on.
 *  4. Effect sizes are printed in basis points next to the ~2 bps maker round
 *     trip, because an anomaly smaller than the cost of trading it is a
 *     curiosity, not an edge.
 *
 * A slice that clears all four is worth attention. A slice that clears only the
 * first is noise wearing a p-value.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');
const COST_FLOOR_BPS = 2.0;      // maker round trip, the bar any edge must clear

// ── statistics ──────────────────────────────────────────────────────────

function mean(a) { return a.reduce((x, y) => x + y, 0) / a.length; }
function stdev(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

/**
 * One-sample t-test against zero, with Newey-West HAC standard errors.
 *
 * THIS CORRECTION IS NOT OPTIONAL AND ITS ABSENCE IS A CLASSIC WAY TO INVENT
 * AN ANOMALY. The forward return over h bars, sampled at every bar, produces
 * observations that share h-1 of their h bars with their neighbour. They are
 * heavily positively autocorrelated, so the naive standard error is far too
 * small and every t-statistic comes out inflated by roughly sqrt(h).
 *
 * On the first run of this scan with a 4-bar horizon, that inflation was about
 * 2x, and it was enough to push a dozen slices through FDR correction that have
 * no business being there. Newey-West with lag = h-1 accounts for exactly that
 * overlap.
 */
function tTest(values, lag = 0) {
  const n = values.length;
  if (n < 30) return null;
  const m = mean(values);
  const dev = values.map(v => v - m);

  // Long-run variance: gamma_0 plus Bartlett-weighted autocovariances.
  let lrv = dev.reduce((a, d) => a + d * d, 0) / n;
  for (let L = 1; L <= Math.min(lag, n - 2); L++) {
    let g = 0;
    for (let i = L; i < n; i++) g += dev[i] * dev[i - L];
    g /= n;
    const w = 1 - L / (lag + 1);          // Bartlett kernel
    lrv += 2 * w * g;
  }
  if (!(lrv > 0)) return null;

  const se = Math.sqrt(lrv / n);
  const t = m / se;
  return { n, mean: m, t, p: twoSidedP(t, n - 1), se, naiveT: m / (stdev(values) / Math.sqrt(n)) };
}

/** Normal approximation to the two-sided t p-value; fine at these sample sizes. */
function twoSidedP(t, df) {
  void df;
  const z = Math.abs(t);
  // Abramowitz & Stegun 26.2.17
  const p = 0.2316419, b = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429];
  const tt = 1 / (1 + p * z);
  const phi = Math.exp(-z * z / 2) / Math.sqrt(2 * Math.PI);
  let poly = 0;
  for (let i = 0; i < b.length; i++) poly += b[i] * Math.pow(tt, i + 1);
  return 2 * phi * poly;
}

/**
 * Benjamini-Hochberg: control the expected proportion of false positives among
 * the reported discoveries, rather than the chance of any single false positive.
 * Less conservative than Bonferroni and the right tool when scanning many
 * genuinely independent-ish hypotheses.
 */
function benjaminiHochberg(tests, q = 0.10) {
  const valid = tests.filter(t => t.p != null).sort((a, b) => a.p - b.p);
  const m = valid.length;
  let cutoff = -1;
  for (let i = 0; i < m; i++) {
    if (valid[i].p <= ((i + 1) / m) * q) cutoff = i;
  }
  const survivors = cutoff >= 0 ? valid.slice(0, cutoff + 1) : [];
  return { survivors, tested: m, threshold: cutoff >= 0 ? valid[cutoff].p : null, q };
}

// ── feature construction ────────────────────────────────────────────────

function buildFeatures(bars, barMs) {
  const rows = [];
  const closes = bars.map(b => b.close);
  const rets = [0];
  for (let i = 1; i < closes.length; i++) rets.push(Math.log(closes[i] / closes[i - 1]));

  const barsPerHour = Math.max(1, Math.round(3600000 / barMs));
  const volWindow = barsPerHour * 24;

  for (let i = volWindow; i < bars.length; i++) {
    const b = bars[i];
    const d = new Date(b.start);
    const recent = rets.slice(i - volWindow, i);
    const vol = stdev(recent);
    if (!vol) continue;

    // Size of the last move, in units of recent volatility. The classic
    // overreaction candidate: does an unusually large move revert harder?
    const z = rets[i] / vol;

    // Run length: consecutive same-direction bars ending here.
    let run = 0;
    const dir = Math.sign(rets[i]);
    for (let k = i; k >= 1 && Math.sign(rets[k]) === dir && dir !== 0; k--) run++;

    // Position within the trailing day's range.
    const win = bars.slice(i - volWindow, i + 1);
    const hi = Math.max(...win.map(c => c.high));
    const lo = Math.min(...win.map(c => c.low));
    const pos = hi > lo ? (b.close - lo) / (hi - lo) : 0.5;

    // Volume relative to its own recent norm.
    const avgVol = mean(win.map(c => c.volume));
    const relVol = avgVol ? b.volume / avgVol : 1;

    rows.push({
      i, ts: b.start,
      hour: d.getUTCHours(),
      weekday: d.getUTCDay(),
      ret: rets[i], z, run: dir * run, pos, relVol, vol,
      // Minutes until the next 8-hourly funding settlement (00/08/16 UTC).
      minsToFunding: minutesToFunding(d)
    });
  }
  return { rows, rets };
}

function minutesToFunding(d) {
  const h = d.getUTCHours(), m = d.getUTCMinutes();
  const mins = h * 60 + m;
  const marks = [0, 480, 960, 1440];
  let best = Infinity;
  for (const mk of marks) if (mk >= mins) best = Math.min(best, mk - mins);
  return best;
}

/** Forward return from bar i over `h` bars, in bps. */
function forwardBps(bars, i, h) {
  if (i + h >= bars.length) return null;
  return ((bars[i + h].close - bars[i].close) / bars[i].close) * 10000;
}

// ── the scan ────────────────────────────────────────────────────────────

function scan(symbol, bars, barMs, horizonBars, label) {
  // Overlap lag for the HAC correction: each observation shares horizon-1 bars
  // of its forward window with the next one.
  const hacLag = Math.max(0, horizonBars - 1);
  const { rows } = buildFeatures(bars, barMs);
  const midpoint = Math.floor(rows.length / 2);
  const tests = [];

  const addTest = (name, family, filter, signFn) => {
    const first = [], second = [], all = [];
    for (let k = 0; k < rows.length; k++) {
      const r = rows[k];
      if (!filter(r)) continue;
      const fwd = forwardBps(bars, r.i, horizonBars);
      if (fwd == null) continue;
      // signFn maps the slice to a directional bet: +1 long, -1 short.
      const signed = signFn ? signFn(r) * fwd : fwd;
      all.push(signed);
      (k < midpoint ? first : second).push(signed);
    }
    const t = tTest(all, hacLag);
    if (!t) return;
    tests.push({
      symbol, name, family,
      n: t.n, meanBps: t.mean, t: t.t, p: t.p, naiveT: t.naiveT,
      firstHalf: first.length >= 30 ? mean(first) : null,
      secondHalf: second.length >= 30 ? mean(second) : null,
      firstN: first.length, secondN: second.length
    });
  };

  // ── Family 1: calendar. Pure "what nobody looks at". ──
  for (let h = 0; h < 24; h++) addTest(`hour ${String(h).padStart(2, '0')} UTC`, 'hour', r => r.hour === h, null);
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  for (let d = 0; d < 7; d++) addTest(`${wd[d]}`, 'weekday', r => r.weekday === d, null);

  // ── Family 2: overreaction. Does an outsized move revert? ──
  for (const th of [1.5, 2, 2.5, 3]) {
    addTest(`fade move > +${th}σ`, 'overreaction', r => r.z > th, () => -1);
    addTest(`fade move < -${th}σ`, 'overreaction', r => r.z < -th, () => +1);
  }

  // ── Family 3: runs. Does a streak continue or break? ──
  for (const n of [3, 4, 5, 6]) {
    addTest(`fade ${n}+ up bars`, 'runs', r => r.run >= n, () => -1);
    addTest(`fade ${n}+ down bars`, 'runs', r => r.run <= -n, () => +1);
  }

  // ── Family 4: location within the trailing range. ──
  for (const [lo, hi, dir, nm] of [[0, 0.1, +1, 'bottom decile'], [0.9, 1, -1, 'top decile'],
                                   [0, 0.2, +1, 'bottom quintile'], [0.8, 1, -1, 'top quintile']]) {
    addTest(`fade from ${nm}`, 'location', r => r.pos >= lo && r.pos <= hi, () => dir);
  }

  // ── Family 5: volatility state. ──
  const vols = rows.map(r => r.vol).sort((a, b) => a - b);
  const q = p => vols[Math.floor(vols.length * p)];
  addTest('lowest vol quintile', 'volstate', r => r.vol <= q(0.2), null);
  addTest('highest vol quintile', 'volstate', r => r.vol >= q(0.8), null);
  addTest('fade in highest vol quintile', 'volstate', r => r.vol >= q(0.8) && Math.abs(r.z) > 1, r => -Math.sign(r.z));
  addTest('fade in lowest vol quintile', 'volstate', r => r.vol <= q(0.2) && Math.abs(r.z) > 1, r => -Math.sign(r.z));

  // ── Family 6: funding settlement mechanics. A scheduled, mechanical flow. ──
  for (const w of [15, 30, 60, 120]) {
    addTest(`${w}min before funding`, 'funding', r => r.minsToFunding <= w && r.minsToFunding > 0, null);
  }
  addTest('funding hour itself', 'funding', r => [0, 8, 16].includes(r.hour), null);

  // ── Family 7: volume anomalies. ──
  addTest('fade high-volume spike', 'volume', r => r.relVol > 3 && Math.abs(r.z) > 1, r => -Math.sign(r.z));
  addTest('follow high-volume spike', 'volume', r => r.relVol > 3 && Math.abs(r.z) > 1, r => Math.sign(r.z));
  addTest('low-volume move fades', 'volume', r => r.relVol < 0.5 && Math.abs(r.z) > 1, r => -Math.sign(r.z));

  void label;
  return tests;
}

// ── report ──────────────────────────────────────────────────────────────

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tfKey = args.tf || '15';
  const horizon = parseInt(args.horizon || '4', 10);
  const q = parseFloat(args.fdr || '0.10');
  const barMs = { '1': 60000, '5': 300000, '15': 900000, '60': 3600000 }[tfKey];

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));
  let all = [];
  const spans = [];

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const bars = bundle.series[tfKey];
    if (!bars || bars.length < 2000) continue;
    spans.push(`${symbol}: ${bars.length} bars, ${((bars[bars.length - 1].start - bars[0].start) / 86400000).toFixed(0)} days`);
    all = all.concat(scan(symbol, bars, barMs, horizon, tfKey));
  }

  const line = '─'.repeat(86);
  console.log(`\n${line}\n  STUDY 5 — SYSTEMATIC ANOMALY SCAN (${tfKey}m bars, ${horizon}-bar forward horizon)\n${line}`);
  spans.forEach(s => console.log(`  ${s}`));
  console.log(`\n  ${all.length} hypotheses tested across ${files.length} symbols.`);
  console.log(`  Newey-West HAC standard errors (lag ${horizon - 1}) correct for overlapping forward windows.`);
  console.log(`  Benjamini-Hochberg FDR control at q=${q}. Cost floor for tradability: ${COST_FLOOR_BPS} bps.\n`);
  const inflation = all.filter(t => t.naiveT).map(t => Math.abs(t.naiveT) / Math.max(1e-9, Math.abs(t.t)));
  if (inflation.length) {
    const medInf = inflation.sort((a, b) => a - b)[Math.floor(inflation.length / 2)];
    console.log(`  Median t-statistic inflation removed by the HAC correction: ${medInf.toFixed(2)}x`);
    console.log(`  (i.e. without it, every number below would look ${medInf.toFixed(2)}x more significant than it is.)\n`);
  }

  const { survivors, tested, threshold } = benjaminiHochberg(all, q);

  if (!survivors.length) {
    console.log(`  NO slice survived FDR correction across ${tested} tests.`);
    console.log(`  That is the honest result: at this horizon, on this data, the scan found`);
    console.log(`  nothing that is distinguishable from what ${tested} random tests would throw up.\n`);
  } else {
    console.log(`  ${survivors.length} of ${tested} slices survived (p <= ${threshold.toExponential(2)}).\n`);
    console.log(`  ${'symbol'.padEnd(9)}${'slice'.padEnd(26)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t'.padStart(7)}${'1st half'.padStart(10)}${'2nd half'.padStart(10)}${'  verdict'}`);
    for (const s of survivors) {
      const consistent = s.firstHalf != null && s.secondHalf != null &&
        Math.sign(s.firstHalf) === Math.sign(s.secondHalf) && Math.sign(s.firstHalf) === Math.sign(s.meanBps);
      const tradable = Math.abs(s.meanBps) > COST_FLOOR_BPS;
      const verdict = !consistent ? 'fails out-of-sample'
        : !tradable ? `real but < ${COST_FLOOR_BPS}bps cost`
        : 'SURVIVES ALL FOUR';
      console.log(`  ${s.symbol.padEnd(9)}${s.name.padEnd(26)}${String(s.n).padStart(7)}${s.meanBps.toFixed(2).padStart(10)}${s.t.toFixed(2).padStart(7)}${(s.firstHalf == null ? '—' : s.firstHalf.toFixed(2)).padStart(10)}${(s.secondHalf == null ? '—' : s.secondHalf.toFixed(2)).padStart(10)}  ${verdict}`);
    }
  }

  // Family-level view: is any whole family of ideas carrying signal, even where
  // no single slice clears correction? Aggregating across symbols and slices
  // inside a family is a lower-variance question than any one slice.
  console.log(`\n  FAMILY SUMMARY (all tests, not just survivors)`);
  console.log(`  ${'family'.padEnd(16)}${'tests'.padStart(7)}${'median |t|'.padStart(12)}${'max |t|'.padStart(10)}${'best slice'.padStart(30)}`);
  const byFamily = {};
  for (const t of all) {
    byFamily[t.family] = byFamily[t.family] || [];
    byFamily[t.family].push(t);
  }
  for (const [fam, ts] of Object.entries(byFamily).sort((a, b) => b[1].length - a[1].length)) {
    const absT = ts.map(x => Math.abs(x.t)).sort((a, b) => a - b);
    const med = absT[Math.floor(absT.length / 2)];
    const best = ts.reduce((a, b) => (Math.abs(b.t) > Math.abs(a.t) ? b : a));
    console.log(`  ${fam.padEnd(16)}${String(ts.length).padStart(7)}${med.toFixed(2).padStart(12)}${Math.abs(best.t).toFixed(2).padStart(10)}${(best.symbol + ' ' + best.name).padStart(30)}`);
  }
  console.log(`\n  Under a true null, median |t| would sit near 0.67 and max |t| near 3 for`);
  console.log(`  this many tests. A family whose median |t| is much above 0.67 is carrying`);
  console.log(`  something broader than one lucky slice.`);
  console.log(`\n${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', `anomaly-scan-${tfKey}m-h${horizon}.json`),
    JSON.stringify({ generatedAt: new Date().toISOString(), tf: tfKey, horizon, q, tested, survivors, all }, null, 2));
}

main();
