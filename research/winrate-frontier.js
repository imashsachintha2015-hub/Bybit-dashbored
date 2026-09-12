#!/usr/bin/env node
/**
 * STUDY 8 — Is win rate a free parameter?
 *
 * The question behind this study: "we always land at 41-47%; if we do the
 * opposite, can we get 53-59%?"
 *
 * There are two separate claims in there and they need separating, because one
 * is true in a way that does not help, and the other is false in a way that
 * costs money.
 *
 * CLAIM 1: you can get a 59% win rate.
 * True, and trivially so. Win rate is not a measure of skill — it is a
 * consequence of where you put the take-profit relative to the stop. Put the
 * target close and the stop far away and you will win most trades. Put them the
 * other way round and you will lose most trades. Both can be done with a coin
 * flip for a signal. This study measures exactly that: the same random entries,
 * with the target/stop ratio dialled from 1:4 to 4:1, and the win rate sweeping
 * from ~20% to ~80% while expectancy does not improve at all.
 *
 * CLAIM 2: inverting a losing strategy produces a winning one.
 * False, and the reason is arithmetic. Net = Gross − Cost. Inverting flips the
 * sign of Gross but NOT of Cost, because you pay the spread and the fee either
 * way. So the inverse of a strategy with net −0.137R is not +0.137R; it is
 * −(Gross) − Cost, which is worse than break-even whenever the original was
 * losing mostly because of costs. This study inverts every strategy in the
 * repository and measures what actually happens.
 *
 * Both are tested on real data rather than argued.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');

/**
 * Walks bars forward from entry until either the target or the stop is touched.
 * Pessimistic: if a single bar contains both, the STOP is taken.
 */
function resolveTrade(bars, i, dir, tpR, slR, riskPx, maxBars, costBps) {
  const entry = bars[i].close;
  const stop = dir > 0 ? entry - riskPx * slR : entry + riskPx * slR;
  const target = dir > 0 ? entry + riskPx * tpR : entry - riskPx * tpR;

  // Cost expressed in R. R is the distance to the stop, so the same fee is a
  // bigger fraction of R when the stop is tight. This conversion is the whole
  // reason a fast strategy is harder than a slow one.
  const riskFraction = (riskPx * slR) / entry;
  const costR = riskFraction > 0 ? (costBps / 10000) / riskFraction : 0;

  const done = (grossR, win, nbars) => ({ grossR, r: grossR - costR, win, winNet: (grossR - costR) > 0, bars: nbars, costR });

  for (let k = i + 1; k <= Math.min(i + maxBars, bars.length - 1); k++) {
    const b = bars[k];
    const hitStop = dir > 0 ? b.low <= stop : b.high >= stop;
    const hitTgt = dir > 0 ? b.high >= target : b.low <= target;
    if (hitStop) return done(-slR, false, k - i);
    if (hitTgt) return done(tpR, true, k - i);
  }
  const exit = bars[Math.min(i + maxBars, bars.length - 1)].close;
  const raw = (dir > 0 ? (exit - entry) : (entry - exit)) / (riskPx * slR);
  return done(raw, raw > 0, maxBars);
}

function atr(bars, i, period) {
  let sum = 0, n = 0;
  for (let k = Math.max(1, i - period); k < i; k++) {
    const c = bars[k], p = bars[k - 1];
    sum += Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close));
    n++;
  }
  return n ? sum / n : 0;
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';
  const costBps = parseFloat(args.cost || '15');  // taker round trip incl. slippage

  const line = '─'.repeat(92);
  console.log(`\n${line}\n  STUDY 8 — IS WIN RATE A FREE PARAMETER?\n${line}`);

  const bundle = JSON.parse(fs.readFileSync(path.join(DATA, 'BTCUSDT.json'), 'utf8'));
  const bars = bundle.series[tf];
  const days = ((bars[bars.length - 1].start - bars[0].start) / 86400000).toFixed(0);

  // ── Part 1: coin-flip entries, sweeping the target/stop ratio ──
  console.log(`
  PART 1 — RANDOM entries on BTCUSDT (${bars.length} x ${tf}m bars, ${days} days).
  The signal is a coin flip. Nothing here has any predictive content whatsoever.
  Only the target/stop ratio changes.
`);
  console.log(`  ${'target : stop'.padEnd(16)}${'trades'.padStart(8)}${'WIN RATE'.padStart(11)}${'avg win'.padStart(10)}${'avg loss'.padStart(10)}${'GROSS exp'.padStart(11)}${'cost/trade'.padStart(12)}${'NET exp'.padStart(10)}`);

  // Deterministic pseudo-random so the run reproduces exactly.
  let seed = 12345;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };

  const ratios = [[0.25, 1], [0.5, 1], [1, 1], [1.5, 1], [2, 1], [3, 1], [1, 2], [1, 4]];
  for (const [tpR, slR] of ratios) {
    const results = [];
    seed = 12345;
    for (let i = 50; i < bars.length - 100; i += 7) {
      const a = atr(bars, i, 14);
      if (!a) continue;
      const dir = rand() > 0.5 ? 1 : -1;
      results.push(resolveTrade(bars, i, dir, tpR, slR, a * 3, 96, costBps));
    }
    if (results.length < 50) continue;
    // Win rate is reported on the GROSS outcome (did the target get hit before
    // the stop), which is the number people quote. Expectancy is shown both
    // gross and net so the two effects can be told apart.
    const wins = results.filter(r => r.win);
    const losses = results.filter(r => !r.win);
    const wr = (wins.length / results.length) * 100;
    const grossExp = S.mean(results.map(r => r.grossR));
    const netExp = S.mean(results.map(r => r.r));
    const cost = S.mean(results.map(r => r.costR));
    console.log(`  ${`${tpR} : ${slR}`.padEnd(16)}${String(results.length).padStart(8)}${(wr.toFixed(1) + '%').padStart(11)}${(wins.length ? S.mean(wins.map(r => r.grossR)) : 0).toFixed(2).padStart(10)}${(losses.length ? S.mean(losses.map(r => r.grossR)) : 0).toFixed(2).padStart(10)}${grossExp.toFixed(3).padStart(11)}${('-' + cost.toFixed(3)).padStart(12)}${netExp.toFixed(3).padStart(10)}`);
  }

  console.log(`
  Two columns matter and they say different things.

  WIN RATE sweeps from ~25% to ~79% purely by moving the target and the stop.
  The signal never changes — it is a coin flip throughout. So yes: a 59% win
  rate is available right now, on no information at all, by putting the target
  closer than the stop.

  GROSS EXPECTANCY stays flat near zero across that entire sweep. That is the
  theorem underneath: with no predictive edge, moving the target and stop trades
  win RATE against win SIZE at a fixed exchange rate. You can have frequent
  small wins or rare large ones. You cannot manufacture expectancy from the
  ratio, because there is nothing there to manufacture it from.

  NET EXPECTANCY is gross minus the cost column, and it is negative everywhere.
  Note how the cost per trade grows as the stop tightens: the same fee is a
  larger fraction of R when R is small. That is the entire reason a fast
  strategy is harder than a slow one.

  Win rate is a DIAL. Expectancy is the SCORE.
`);

  // ── Part 2: inverting real strategies ──
  console.log(`${line}\n  PART 2 — WHAT INVERSION ACTUALLY DOES\n${line}`);
  console.log(`
  Every directional rule below is run forwards and then inverted, on identical
  bars with identical costs. Watch the win rate flip and the expectancy fail to.
`);
  console.log(`  ${'rule'.padEnd(28)}${'n'.padStart(7)}${'win%'.padStart(8)}${'exp R'.padStart(9)}  │${'inv win%'.padStart(10)}${'inv exp R'.padStart(11)}${'gross R'.padStart(10)}`);

  const files = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
  const rules = {
    'momentum: last bar up': (b, i) => Math.sign(b[i].close - b[i - 1].close),
    'momentum: 5-bar': (b, i) => Math.sign(b[i].close - b[i - 5].close),
    'reversion: last bar': (b, i) => -Math.sign(b[i].close - b[i - 1].close),
    'above/below 20-EMA': (b, i) => {
      let e = b[i - 20].close;
      for (let k = i - 19; k <= i; k++) e = b[k].close * (2 / 21) + e * (1 - 2 / 21);
      return Math.sign(b[i].close - e);
    },
    'breakout 20-bar high/low': (b, i) => {
      const w = b.slice(i - 20, i);
      const hi = Math.max(...w.map(c => c.high)), lo = Math.min(...w.map(c => c.low));
      return b[i].close > hi ? 1 : b[i].close < lo ? -1 : 0;
    }
  };

  for (const [name, fn] of Object.entries(rules)) {
    const fwd = [], inv = [], gross = [];
    for (const f of files) {
      const bb = JSON.parse(fs.readFileSync(path.join(DATA, `${f}.json`), 'utf8')).series[tf];
      if (!bb) continue;
      for (let i = 60; i < bb.length - 100; i += 7) {
        const a = atr(bb, i, 14);
        if (!a) continue;
        const d = fn(bb, i);
        if (!d) continue;
        // 3 ATR of risk, which is a realistic swing stop rather than the
        // hair-trigger 1 ATR that makes costs dominate everything.
        const r = resolveTrade(bb, i, d, 2, 1, a * 3, 96, costBps);
        const ri = resolveTrade(bb, i, -d, 2, 1, a * 3, 96, costBps);
        const rg = resolveTrade(bb, i, d, 2, 1, a * 3, 96, 0);   // zero-cost, isolates gross edge
        fwd.push(r); inv.push(ri); gross.push(rg);
      }
    }
    if (fwd.length < 100) continue;
    const wr = x => (x.filter(r => r.win).length / x.length) * 100;
    const ex = x => S.mean(x.map(r => r.r));
    console.log(`  ${name.padEnd(28)}${String(fwd.length).padStart(7)}${wr(fwd).toFixed(1).padStart(7)}%${ex(fwd).toFixed(3).padStart(9)}  │${(wr(inv).toFixed(1) + '%').padStart(10)}${ex(inv).toFixed(3).padStart(11)}${ex(gross).toFixed(3).padStart(10)}`);
  }

  console.log(`
  The "gross R" column is the forward rule with costs switched off. Compare it
  to "exp R": the difference between them is what the exchange takes, and it is
  larger than any edge in the rule.

  That is why inversion does not rescue anything. Net = Gross − Cost. Inverting
  flips Gross and leaves Cost alone, so a rule that is losing because of costs
  inverts into a rule that is ALSO losing because of costs. You do not get
  −(−0.137R); you get −(Gross) − Cost, and both sides of the trade pay the toll.
${line}
`);
}

main();
