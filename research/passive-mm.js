#!/usr/bin/env node
/**
 * STUDY 3 — Passive liquidity provision, simulated honestly.
 *
 * Study 2's first-pass market-making simulation produced suspiciously good
 * numbers at wide quotes (+8.5 bps per fill). That result was an artefact of
 * three things it did not model, all of which flatter the maker:
 *
 *   1. NO EXIT. It measured the mark-to-market a few bars after a fill and
 *      counted it as the outcome. Real inventory has to be closed, and closing
 *      costs another fee and another spread.
 *   2. NO QUEUE. It assumed that if a bar's low touched the bid, the bid filled.
 *      In reality you are behind everyone who quoted that price earlier; price
 *      touching your level fills the front of the queue, not you.
 *   3. NO INVENTORY LIMIT. It could accumulate unbounded one-sided exposure,
 *      which is precisely the risk that ends market makers.
 *
 * This version models all three. It is deliberately pessimistic wherever the
 * data cannot settle the question, because a passive strategy that only works
 * under optimistic fill assumptions does not work.
 *
 * The underlying reason this is worth testing at all: Study 1 measured genuine
 * mean reversion (variance ratio 0.78-0.87 at 32 bars, direction accuracy
 * 52-54% for a reversion predictor at n=12,098). A resting bid below the market
 * is a mean-reversion bet that ALSO collects the spread instead of paying it.
 * That combination is the only structure found so far where the cost term works
 * in the trader's favour.
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');

/**
 * @param bars            1-minute bars
 * @param cfg.halfSpread  quote distance from reference, in bps
 * @param cfg.makerFee    maker fee, % per side (negative = rebate)
 * @param cfg.takerFee    taker fee, % per side (for forced exits)
 * @param cfg.queueBps    price must trade THROUGH the quote by this much to fill
 * @param cfg.maxHold     bars before unwinding inventory at market
 * @param cfg.maxInv      maximum units of one-sided inventory
 */
function simulate(bars, cfg) {
  const { halfSpread, makerFee, takerFee, queueBps, maxHold, maxInv } = cfg;
  let inventory = 0;          // units, + = long
  let cashBps = 0;            // realised P&L in bps of one unit notional
  const openLots = [];        // { side, price, bar }
  let makerFills = 0, takerExits = 0, timeouts = 0;
  let maxInvSeen = 0;

  for (let i = 1; i < bars.length; i++) {
    const ref = bars[i - 1].close;
    const bar = bars[i];
    const bid = ref * (1 - halfSpread / 10000);
    const ask = ref * (1 + halfSpread / 10000);

    // Queue proxy: require the bar to trade meaningfully THROUGH the quote.
    // Touching the level is not a fill when you are behind the queue.
    const bidFilled = bar.low <= bid * (1 - queueBps / 10000);
    const askFilled = bar.high >= ask * (1 + queueBps / 10000);

    // ── Passive exits first: an existing lot closed by the opposite quote is
    //    the profitable path, and it pays the maker fee rather than the taker.
    for (let k = openLots.length - 1; k >= 0; k--) {
      const lot = openLots[k];
      if (lot.side === 'LONG' && askFilled) {
        const pnl = ((ask - lot.price) / lot.price) * 10000;
        cashBps += pnl - (makerFee * 100);
        openLots.splice(k, 1);
        inventory -= 1;
        makerFills++;
      } else if (lot.side === 'SHORT' && bidFilled) {
        const pnl = ((lot.price - bid) / lot.price) * 10000;
        cashBps += pnl - (makerFee * 100);
        openLots.splice(k, 1);
        inventory += 1;
        makerFills++;
      }
    }

    // ── New passive entries, subject to the inventory cap ──
    if (bidFilled && inventory < maxInv) {
      openLots.push({ side: 'LONG', price: bid, bar: i });
      inventory += 1;
      cashBps -= makerFee * 100;
      makerFills++;
    }
    if (askFilled && inventory > -maxInv) {
      openLots.push({ side: 'SHORT', price: ask, bar: i });
      inventory -= 1;
      cashBps -= makerFee * 100;
      makerFills++;
    }
    maxInvSeen = Math.max(maxInvSeen, Math.abs(inventory));

    // ── Forced unwind: inventory held too long goes out at market, paying the
    //    taker fee and the spread. This is the cost of being wrong and having
    //    to admit it, and it is what a mark-to-market simulation hides.
    for (let k = openLots.length - 1; k >= 0; k--) {
      const lot = openLots[k];
      if (i - lot.bar < maxHold) continue;
      const exit = bar.close;
      const pnl = lot.side === 'LONG'
        ? ((exit - lot.price) / lot.price) * 10000
        : ((lot.price - exit) / lot.price) * 10000;
      cashBps += pnl - takerFee * 100;
      openLots.splice(k, 1);
      inventory += lot.side === 'LONG' ? -1 : 1;
      takerExits++; timeouts++;
    }
  }

  // Close whatever is left at the final price.
  const last = bars[bars.length - 1].close;
  for (const lot of openLots) {
    const pnl = lot.side === 'LONG'
      ? ((last - lot.price) / lot.price) * 10000
      : ((lot.price - last) / lot.price) * 10000;
    cashBps += pnl - takerFee * 100;
  }

  const days = (bars[bars.length - 1].start - bars[0].start) / 86400000;
  return {
    netBps: +cashBps.toFixed(1),
    makerFills, takerExits, timeouts, maxInvSeen,
    perFillBps: makerFills ? +(cashBps / makerFills).toFixed(4) : 0,
    days: +days.toFixed(1),
    // Return on the capital that has to sit behind maxInv units of inventory.
    returnOnInvPct: +((cashBps / 10000) / Math.max(1, maxInv) * 100).toFixed(2),
    annualisedPct: days > 0 ? +(((cashBps / 10000) / Math.max(1, maxInv)) * (365 / days) * 100).toFixed(1) : null
  };
}

function main() {
  const line = '─'.repeat(78);
  console.log(`\n${line}\n  STUDY 3 — PASSIVE LIQUIDITY PROVISION, MODELLED HONESTLY\n${line}`);
  console.log(`
  Models what Study 2's first pass omitted: inventory must be closed, queue
  position means price has to trade THROUGH your quote to fill you, and
  one-sided exposure is capped. Forced unwinds pay the taker fee.
`);

  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));

  const scenarios = [
    { label: 'retail maker 0.020%',  makerFee: 0.0200, takerFee: 0.0550 },
    { label: 'VIP-3 maker 0.010%',   makerFee: 0.0100, takerFee: 0.0320 },
    { label: 'rebate tier -0.005%',  makerFee: -0.0050, takerFee: 0.0300 }
  ];

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const bars = bundle.series['1'];
    if (!bars || bars.length < 2000) continue;

    console.log(`\n  ${symbol} — ${bars.length} x 1m bars\n`);
    console.log(`    ${'fee tier'.padEnd(22)}${'quote'.padStart(8)}${'fills'.padStart(8)}${'timeouts'.padStart(10)}${'net bps'.padStart(10)}${'ann. %'.padStart(10)}`);

    for (const sc of scenarios) {
      for (const halfSpread of [5, 10, 20]) {
        const r = simulate(bars, {
          halfSpread, makerFee: sc.makerFee, takerFee: sc.takerFee,
          queueBps: 1,        // must trade 1bp through the quote to fill
          maxHold: 60,        // unwind after an hour
          maxInv: 5
        });
        console.log(`    ${sc.label.padEnd(22)}${(halfSpread + 'bp').padStart(8)}${String(r.makerFills).padStart(8)}${String(r.timeouts).padStart(10)}${String(r.netBps).padStart(10)}${String(r.annualisedPct).padStart(10)}`);
      }
    }
  }

  console.log(`
${line}
  SENSITIVITY — the assumption that decides everything
${line}

  The queue penalty is the parameter this entire result rests on, and it is the
  one the data cannot pin down: bars do not record where in the queue an order
  sat. Below is the same strategy across queue assumptions, so the fragility is
  visible rather than buried in a default.
`);

  const btc = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'BTCUSDT.json'), 'utf8')).series['1'];
  console.log(`    ${'queue penalty'.padEnd(20)}${'fills'.padStart(9)}${'net bps'.padStart(11)}${'annualised %'.padStart(15)}`);
  for (const q of [0, 0.5, 1, 2, 5]) {
    const r = simulate(btc, { halfSpread: 10, makerFee: 0.0100, takerFee: 0.0320, queueBps: q, maxHold: 60, maxInv: 5 });
    console.log(`    ${(q + ' bps through').padEnd(20)}${String(r.makerFills).padStart(9)}${String(r.netBps).padStart(11)}${String(r.annualisedPct).padStart(15)}`);
  }

  console.log(`
${line}
`);
}

main();
