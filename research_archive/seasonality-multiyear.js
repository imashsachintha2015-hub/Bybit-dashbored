#!/usr/bin/env node
/**
 * ATTACK 2 — Calendar effects, with years behind them.
 *
 * The 15-minute anomaly scan tested hour-of-day and day-of-week on 418 days and
 * found nothing survived correction. But 418 days is barely more than one
 * calendar year, which is far too little to say anything about a MONTHLY or
 * turn-of-month effect, and thin even for day-of-week.
 *
 * This re-runs the calendar question on multi-year DAILY bars, where each
 * observation is genuinely independent (no overlapping forward windows at a
 * 1-day horizon, so the HAC correction has nothing to correct) and the sample
 * covers several distinct market regimes rather than one.
 *
 * Calendar effects are the canonical example of an anomaly that looks
 * overwhelming in-sample and vanishes out of it, which is exactly why they are
 * worth testing properly rather than dismissing or believing.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, 'data');
const COST_BPS = 2.0;

const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function main() {
  const files = fs.readdirSync(DATA).filter(f => f.endsWith('-daily.json'));
  const line = '─'.repeat(88);
  console.log(`\n${line}\n  ATTACK 2 — CALENDAR EFFECTS ON MULTI-YEAR DAILY BARS\n${line}`);
  if (!files.length) {
    console.error('  No daily data. Run: node research/fetch-extra.js daily BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP');
    process.exit(1);
  }

  const allTests = [];

  for (const f of files) {
    const d = JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf8'));
    const bars = d.rows;
    if (!bars || bars.length < 400) continue;
    const inst = d.inst;

    const rets = [];
    for (let i = 1; i < bars.length; i++) {
      const r = ((bars[i].close - bars[i - 1].close) / bars[i - 1].close) * 10000;
      rets.push({ ts: bars[i].start, bps: r, d: new Date(bars[i].start) });
    }
    const years = ((bars[bars.length - 1].start - bars[0].start) / 86400000 / 365).toFixed(2);
    const mid = Math.floor(rets.length / 2);

    console.log(`\n  ${inst} — ${rets.length} daily returns, ${years} years (from ${new Date(bars[0].start).toISOString().slice(0, 10)})`);
    console.log(`    unconditional mean: ${S.mean(rets.map(r => r.bps)).toFixed(1)} bps/day\n`);

    // TWO CORRECTIONS, both of which this study got wrong on its first run.
    //
    // 1. TEST AGAINST THE UNCONDITIONAL MEAN, NOT ZERO. Crypto rose over this
    //    period (+19 bps/day for ETH). Any subset of a rising series has a
    //    positive mean, so testing "is July's mean above zero" mostly measures
    //    that the asset went up. The series is demeaned first so the question
    //    becomes "is July different from an ordinary day", which is the
    //    question anyone actually cares about.
    //
    // 2. THE INDEPENDENT UNIT FOR A MONTH EFFECT IS A YEAR, NOT A DAY. Listing
    //    n=217 for "July" is misleading: those 217 days are 7 Julys, and days
    //    within one July share a regime almost entirely. Monthly and
    //    turn-of-month tests are therefore run on per-period aggregates, so
    //    the sample size reported is the number of genuinely independent
    //    observations. It collapses from 217 to 7, which is the honest figure
    //    and changes the conclusion completely.
    const grand = S.mean(rets.map(r => r.bps));

    const add = (name, family, filter, aggregateBy) => {
      const sel = rets.filter(filter);
      if (sel.length < 20) return;

      let vals, firstVals, secondVals, unit;
      if (aggregateBy) {
        // Group into independent periods (e.g. one value per calendar July).
        const groups = new Map();
        rets.forEach((r, i) => {
          if (!filter(r)) return;
          const k = aggregateBy(r);
          if (!groups.has(k)) groups.set(k, { vals: [], idx: i });
          groups.get(k).vals.push(r.bps - grand);
        });
        const entries = [...groups.entries()].sort((a, b) => a[1].idx - b[1].idx);
        if (entries.length < 4) return;
        vals = entries.map(e => S.mean(e[1].vals));
        firstVals = entries.filter(e => e[1].idx < mid).map(e => S.mean(e[1].vals));
        secondVals = entries.filter(e => e[1].idx >= mid).map(e => S.mean(e[1].vals));
        unit = `${entries.length} periods`;
      } else {
        vals = sel.map(r => r.bps - grand);
        const idxs = rets.map((r, i) => ({ r, i })).filter(x => filter(x.r));
        firstVals = idxs.filter(x => x.i < mid).map(x => x.r.bps - grand);
        secondVals = idxs.filter(x => x.i >= mid).map(x => x.r.bps - grand);
        unit = `${vals.length} days`;
      }

      // Daily bars at a 1-day horizon do not overlap, so lag 0 is correct here.
      const t = S.hacT(vals, 0);
      if (!t) return;
      allTests.push({ inst, name, family, unit, ...t, first: S.mean(firstVals), second: S.mean(secondVals) });
    };

    const yearOf = r => r.d.getUTCFullYear();
    const yearMonthOf = r => `${r.d.getUTCFullYear()}-${r.d.getUTCMonth()}`;

    // Weekdays recur ~52x a year, so days are close enough to independent.
    for (let i = 0; i < 7; i++) add(WD[i], 'weekday', r => r.d.getUTCDay() === i, null);
    // Months recur once a year: the independent unit is the year.
    for (let i = 0; i < 12; i++) add(MO[i], 'month', r => r.d.getUTCMonth() === i, yearOf);
    // Turn-of-month recurs monthly: the independent unit is the year-month.
    add('turn of month (last 2 + first 3)', 'turnofmonth', r => {
      const day = r.d.getUTCDate();
      const last = new Date(Date.UTC(r.d.getUTCFullYear(), r.d.getUTCMonth() + 1, 0)).getUTCDate();
      return day <= 3 || day >= last - 1;
    }, yearMonthOf);
    add('mid-month (10th-20th)', 'turnofmonth', r => r.d.getUTCDate() >= 10 && r.d.getUTCDate() <= 20, yearMonthOf);
    add('weekend (Sat+Sun)', 'weekend', r => [0, 6].includes(r.d.getUTCDay()), null);
    add('weekday (Mon-Fri)', 'weekend', r => ![0, 6].includes(r.d.getUTCDay()), null);

    const mine = allTests.filter(t => t.inst === inst);
    console.log(`    (all figures are EXCESS over the ${grand.toFixed(1)} bps/day unconditional mean)`);
    console.log(`    ${'slice'.padEnd(34)}${'independent n'.padStart(15)}${'excess bps'.padStart(12)}${'t'.padStart(8)}${'1st'.padStart(10)}${'2nd'.padStart(10)}`);
    for (const t of mine.sort((a, b) => Math.abs(b.t) - Math.abs(a.t)).slice(0, 8)) {
      console.log(`    ${t.name.padEnd(34)}${t.unit.padStart(15)}${t.mean.toFixed(1).padStart(12)}${t.t.toFixed(2).padStart(8)}${t.first.toFixed(1).padStart(10)}${t.second.toFixed(1).padStart(10)}`);
    }
  }

  const { survivors, tested, threshold } = S.fdr(allTests, 0.10);
  console.log(`\n${line}`);
  console.log(`  ${tested} calendar hypotheses tested. FDR q=0.10 -> ${survivors.length} survive` +
              (threshold ? ` (p <= ${threshold.toExponential(2)})` : ''));
  if (survivors.length) {
    console.log(`\n  ${'instrument'.padEnd(18)}${'slice'.padEnd(34)}${'mean bps'.padStart(10)}${'t'.padStart(8)}${'verdict'.padStart(22)}`);
    for (const s of survivors) {
      const v = S.verdict(s, s.first, s.second, COST_BPS);
      console.log(`  ${s.inst.padEnd(18)}${s.name.padEnd(34)}${s.mean.toFixed(1).padStart(10)}${s.t.toFixed(2).padStart(8)}${v.padStart(22)}  [${s.unit}]`);
    }
  } else {
    console.log(`\n  Nothing survived. Calendar effects in crypto are, on this data, indistinguishable`);
    console.log(`  from what ${tested} random tests produce by chance.`);
  }

  const byFam = {};
  for (const t of allTests) (byFam[t.family] = byFam[t.family] || []).push(Math.abs(t.t));
  console.log(`\n  ${'family'.padEnd(16)}${'tests'.padStart(7)}${'median |t|'.padStart(12)}${'max |t|'.padStart(10)}   (null: median ~0.67)`);
  for (const [fam, ts] of Object.entries(byFam)) {
    const s = ts.slice().sort((a, b) => a - b);
    console.log(`  ${fam.padEnd(16)}${String(ts.length).padStart(7)}${s[Math.floor(s.length / 2)].toFixed(2).padStart(12)}${Math.max(...ts).toFixed(2).padStart(10)}`);
  }
  console.log(`\n${line}\n`);
}

main();
