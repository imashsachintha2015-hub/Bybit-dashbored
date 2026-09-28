#!/usr/bin/env node
/**
 * Order book / liquidation collector.
 *
 * This is the one blind spot the studies could not close. Exchanges serve the
 * order book LIVE only — there is no historical endpoint — and OKX's
 * liquidation feed returns roughly the last 100 events (about three hours),
 * with no working backward pagination. So neither can be tested retrospectively
 * the way klines can.
 *
 * The only way to get that data is to start recording and wait. This does that:
 * it samples top-of-book depth and liquidation prints on an interval and
 * appends them to newline-delimited JSON, so a study can be run against them
 * after it has been running for a few weeks.
 *
 * Run it under a process supervisor:
 *   node research/collect-orderbook.js --symbols BTC-USDT-SWAP,DOGE-USDT-SWAP --interval 5
 *
 * What becomes testable once several weeks exist, and cannot be tested now:
 *   - depth imbalance as a short-horizon predictor
 *   - queue dynamics and genuine (rather than inferred) spoofing
 *   - iceberg detection from repeated fills at a static displayed size
 *   - real liquidation cascades, as events rather than as a bar-shape proxy
 *
 * Sampling on an interval is itself a compromise: it sees the book at instants,
 * not the full event stream. A websocket recorder would capture every update
 * and is the better instrument if this proves worth pursuing.
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, 'data', 'live');

function get(host, p) {
  return new Promise((resolve, reject) => {
    const req = https.request({ host, path: p, method: 'GET', headers: { 'User-Agent': 'MASIS-Collector/1.0' } }, res => {
      let b = '';
      res.on('data', d => b += d);
      res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    });
    req.on('error', reject);
    req.setTimeout(15000, () => req.destroy(new Error('timeout')));
    req.end();
  });
}

function appendLine(file, obj) {
  fs.appendFileSync(file, JSON.stringify(obj) + '\n');
}

async function sampleBook(instId) {
  const res = await get('www.okx.com', `/api/v5/market/books?instId=${instId}&sz=25`);
  const d = (res.data || [])[0];
  if (!d || !d.bids || !d.asks) return null;
  const bids = d.bids.map(r => [parseFloat(r[0]), parseFloat(r[1])]);
  const asks = d.asks.map(r => [parseFloat(r[0]), parseFloat(r[1])]);
  const bestBid = bids[0][0], bestAsk = asks[0][0];
  const mid = (bestBid + bestAsk) / 2;
  const depth = n => ({
    bid: bids.slice(0, n).reduce((a, r) => a + r[1], 0),
    ask: asks.slice(0, n).reduce((a, r) => a + r[1], 0)
  });
  const d5 = depth(5), d25 = depth(25);
  return {
    ts: Date.now(), instId, bestBid, bestAsk, mid,
    spreadBps: ((bestAsk - bestBid) / mid) * 10000,
    // Imbalance is the quantity most likely to carry short-horizon signal, and
    // it is exactly what bar data cannot reconstruct.
    imb5: d5.bid + d5.ask > 0 ? d5.bid / (d5.bid + d5.ask) : 0.5,
    imb25: d25.bid + d25.ask > 0 ? d25.bid / (d25.bid + d25.ask) : 0.5,
    topBidSz: bids[0][1], topAskSz: asks[0][1],
    depth5Bid: d5.bid, depth5Ask: d5.ask
  };
}

async function sampleLiquidations(ccy, seen) {
  const res = await get('www.okx.com',
    `/api/v5/public/liquidation-orders?instType=SWAP&state=filled&uly=${ccy}-USDT&limit=100`);
  const details = (res.data || []).flatMap(b => b.details || []);
  const fresh = [];
  for (const d of details) {
    const key = `${d.ts}-${d.bkPx}-${d.sz}-${d.side}`;
    if (seen.has(key)) continue;
    seen.add(key);
    fresh.push({ ts: +d.ts, ccy, px: +d.bkPx, sz: +d.sz, side: d.side, posSide: d.posSide });
  }
  // Bound the dedupe set so a long-running collector does not grow without limit.
  if (seen.size > 20000) for (const k of [...seen].slice(0, 10000)) seen.delete(k);
  return fresh;
}

async function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const symbols = (args.symbols || 'BTC-USDT-SWAP,SOL-USDT-SWAP,DOGE-USDT-SWAP').split(',');
  const intervalSec = parseInt(args.interval || '5', 10);
  const ccys = [...new Set(symbols.map(s => s.split('-')[0]))];

  fs.mkdirSync(OUT, { recursive: true });
  const day = () => new Date().toISOString().slice(0, 10);
  const seen = new Set();

  console.log(`Collecting into ${OUT}`);
  console.log(`  book:         ${symbols.join(', ')} every ${intervalSec}s`);
  console.log(`  liquidations: ${ccys.join(', ')} every ${Math.max(30, intervalSec * 6)}s`);
  console.log(`Leave running. A few weeks makes the order-book questions testable;`);
  console.log(`anything less is not enough to distinguish signal from noise.\n`);

  let lastLiq = 0;
  let books = 0, liqs = 0;

  for (;;) {
    const start = Date.now();
    for (const s of symbols) {
      try {
        const row = await sampleBook(s);
        if (row) { appendLine(path.join(OUT, `book-${day()}.ndjson`), row); books++; }
      } catch (e) { /* transient; the next tick retries */ }
    }
    if (Date.now() - lastLiq > Math.max(30000, intervalSec * 6000)) {
      lastLiq = Date.now();
      for (const c of ccys) {
        try {
          const fresh = await sampleLiquidations(c, seen);
          for (const f of fresh) appendLine(path.join(OUT, `liquidations-${day()}.ndjson`), f);
          liqs += fresh.length;
        } catch (e) { /* transient */ }
      }
    }
    process.stdout.write(`\r  book samples: ${books}   liquidation prints: ${liqs}   `);
    const elapsed = Date.now() - start;
    await new Promise(r => setTimeout(r, Math.max(500, intervalSec * 1000 - elapsed)));
  }
}

main().catch(e => { console.error('collector failed:', e.message); process.exit(1); });
