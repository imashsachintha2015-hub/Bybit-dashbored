#!/usr/bin/env node
/**
 * STUDY 4 — Funding harvest: getting paid without predicting.
 *
 * Every study so far has looked for an edge in guessing where price goes. This
 * one looks somewhere structurally different, and it is the closest thing to
 * the "loophole" in the question.
 *
 * A perpetual future has no expiry, so nothing forces it to converge to spot.
 * Exchanges maintain the peg with a FUNDING PAYMENT: when the perp trades above
 * spot, longs pay shorts; when below, shorts pay longs. Typically every 8 hours.
 *
 * That creates a position with no directional exposure at all:
 *
 *     long 1 BTC spot  +  short 1 BTC perp   =   delta neutral
 *
 * Price can do anything; the legs cancel. What does not cancel is the funding
 * payment, which the short perp leg collects whenever funding is positive. You
 * are not predicting anything. You are being paid to take the other side of
 * leveraged longs who want exposure more than you do.
 *
 * This is a real, well-known trade — not a discovery. It is included because it
 * is the only structure measured here where the payoff does not depend on being
 * right about direction, and because the data to price it honestly is available.
 *
 * What is NOT modelled, and matters:
 *   - Spot leg cost. No spot series was fetched, so entry/exit fees on the spot
 *     side and any spot/perp basis at entry are excluded. Both reduce returns.
 *   - Liquidation risk on the short perp leg if it is not fully collateralised.
 *   - Exchange/counterparty risk, which is not a rounding error in crypto.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');

function stats(arr) {
  if (!arr.length) return null;
  const s = arr.slice().sort((a, b) => a - b);
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  return {
    n: arr.length, mean,
    median: s[Math.floor(s.length / 2)],
    p10: s[Math.floor(s.length * 0.1)],
    p90: s[Math.floor(s.length * 0.9)],
    min: s[0], max: s[s.length - 1],
    pctPositive: arr.filter(v => v > 0).length / arr.length
  };
}

/**
 * Funding is quoted per settlement (8h) and my hourly series carries the last
 * settled value forward. Settlements are therefore de-duplicated before any
 * sum, otherwise the same payment is counted eight times.
 */
function settlements(series) {
  const out = [];
  let last = null;
  for (const row of series) {
    if (row.fundingRate == null) continue;
    if (last === null || row.fundingRate !== last) {
      out.push({ ts: row.ts, rate: row.fundingRate });
      last = row.fundingRate;
    }
  }
  return out;
}

function main() {
  const line = '─'.repeat(78);
  console.log(`\n${line}\n  STUDY 4 — FUNDING HARVEST (delta-neutral, no prediction)\n${line}`);

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('-derivs.json'));
  if (!files.length) {
    console.error('  No derivatives data. Run: node backtest/fetch-derivatives.js BTC,ETH,SOL');
    process.exit(1);
  }

  const summary = [];

  for (const f of files) {
    const d = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const ccy = d.ccy;
    const settles = settlements(d.series);
    if (settles.length < 5) continue;

    const rates = settles.map(s => s.rate);
    const st = stats(rates);
    const days = (d.series[d.series.length - 1].ts - d.series[0].ts) / 86400000;

    // Always-on harvest: short perp for the whole window, collect every settlement.
    const totalAlways = rates.reduce((a, b) => a + b, 0);
    const annualisedAlways = (totalAlways / days) * 365 * 100;

    // Selective harvest: hold only while funding is positive. Realistic, since
    // the rate for the coming period is known in advance on most venues.
    const positives = rates.filter(r => r > 0);
    const totalSelective = positives.reduce((a, b) => a + b, 0);
    const annualisedSelective = (totalSelective / days) * 365 * 100;

    // Round-trip cost of the perp leg, assuming taker in and out.
    const perpRoundTripPct = 0.055 * 2;
    const cyclesIfAlwaysOn = 1;
    const cyclesIfSelective = countFlips(rates);

    console.log(`\n  ${ccy} — ${settles.length} settlements over ${days.toFixed(1)} days\n`);
    console.log(`    funding per settlement (8h):`);
    console.log(`      mean ${(st.mean * 100).toFixed(5)}%   median ${(st.median * 100).toFixed(5)}%`);
    console.log(`      p10  ${(st.p10 * 100).toFixed(5)}%   p90    ${(st.p90 * 100).toFixed(5)}%`);
    console.log(`      range ${(st.min * 100).toFixed(5)}% .. ${(st.max * 100).toFixed(5)}%`);
    console.log(`      positive ${(st.pctPositive * 100).toFixed(0)}% of the time (longs paying shorts)`);
    console.log(``);
    console.log(`    always-short-perp:   ${(totalAlways * 100).toFixed(4)}% collected  ->  ${annualisedAlways.toFixed(1)}% annualised, gross`);
    console.log(`    only-when-positive:  ${(totalSelective * 100).toFixed(4)}% collected  ->  ${annualisedSelective.toFixed(1)}% annualised, gross`);
    console.log(`    perp-leg round trips needed: ${cyclesIfAlwaysOn} (always-on) vs ${cyclesIfSelective} (selective)`);
    console.log(`    cost per round trip on the perp leg: ${perpRoundTripPct.toFixed(3)}%`);
    const netAlways = annualisedAlways - (perpRoundTripPct * cyclesIfAlwaysOn / days) * 365;
    const netSelective = annualisedSelective - (perpRoundTripPct * cyclesIfSelective / days) * 365;
    console.log(`    NET of perp fees:    always-on ${netAlways.toFixed(1)}%/yr   selective ${netSelective.toFixed(1)}%/yr`);
    console.log(`    (spot-leg fees and basis excluded — both reduce these further)`);

    summary.push({ ccy, annualisedAlways, netAlways, netSelective, pctPositive: st.pctPositive, days });
  }

  function countFlips(rates) {
    let flips = 0, holding = false;
    for (const r of rates) {
      const want = r > 0;
      if (want !== holding) { if (want) flips++; holding = want; }
    }
    return Math.max(1, flips);
  }

  console.log(`\n${line}\n  WHAT THIS IS AND IS NOT\n${line}`);
  console.log(`
  What it is: a genuine, structural, non-directional yield. You are paid for
  supplying something the market wants (the short side of leveraged demand)
  rather than for guessing correctly. Nothing in it requires a prophet, and the
  measured numbers above are what the market actually paid over the window.

  What it is not: free money, or large. The headline rate is a single-digit to
  low-double-digit annual percentage in a calm market. Every study in this
  directory that searched for a directional edge found something smaller than
  its own transaction costs; this is the first one that did not, and it is still
  a yield, not a windfall.

  The honest risk list, none of which is priced above:
    - Funding goes negative in sustained selloffs, exactly when you would most
      want to be flat. The selective variant handles this but pays round-trip
      fees each time it re-enters.
    - The short perp leg can be liquidated if it is not fully collateralised.
      Leverage applied to this trade converts a modest yield into a blow-up risk.
    - Both legs sit on an exchange. Crypto exchange failure is not theoretical,
      and this trade holds the position through whatever happens.
    - The spot leg's fees and the entry basis are excluded here and are real.

  It is included because the question asked for a way to earn that does not
  depend on predicting. This is what that actually looks like when measured:
  modest, structural, and boring.
`);
  console.log(line + '\n');
}

main();
