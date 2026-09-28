#!/usr/bin/env node
/**
 * Fetches the data the earlier studies were blind to.
 *
 *   liquidations — real liquidation prints (price, size, side, time). Study 6
 *                  inferred cascades from bar shape; this is the event itself.
 *   index        — spot index candles. Perp minus index is the BASIS, and the
 *                  funding mechanism exerts mechanical pressure to close it.
 *   daily        — multi-year daily bars, so a seasonality claim has the years
 *                  behind it that 418 days cannot provide.
 *   spot         — the same asset on other venues, for cross-exchange lead-lag.
 *
 * Usage:
 *   node research/fetch-extra.js liquidations BTC,ETH,SOL
 *   node research/fetch-extra.js index BTC-USDT,ETH-USDT,SOL-USDT
 *   node research/fetch-extra.js daily BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP
 *   node research/fetch-extra.js spot BTC
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, 'data');
const sleep = ms => new Promise(r => setTimeout(r, ms));

function get(host, urlPath) {
  return new Promise((resolve, reject) => {
    const req = https.request({ host, path: urlPath, method: 'GET',
      headers: { 'User-Agent': 'MASIS-Research/1.0' } }, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(new Error(body.slice(0, 140))); } });
    });
    req.on('error', reject);
    req.setTimeout(25000, () => req.destroy(new Error('timeout')));
    req.end();
  });
}

async function retry(fn, n = 4) {
  let last;
  for (let i = 0; i < n; i++) {
    try { return await fn(); } catch (e) { last = e; await sleep(1000 * (i + 1)); }
  }
  throw last;
}

// ── Liquidations ────────────────────────────────────────────────────────
async function fetchLiquidations(ccy) {
  const seen = new Set();
  const out = [];
  let before = '';
  for (let page = 0; page < 400; page++) {
    const p = `/api/v5/public/liquidation-orders?instType=SWAP&state=filled&uly=${ccy}-USDT` +
              `&limit=100${before ? `&before=${before}` : ''}`;
    const res = await retry(() => get('www.okx.com', p));
    const blocks = res.data || [];
    const details = blocks.flatMap(b => b.details || []);
    if (!details.length) break;

    let added = 0;
    let oldest = Infinity;
    for (const d of details) {
      const key = `${d.ts}-${d.bkPx}-${d.sz}-${d.side}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ ts: +d.ts, px: +d.bkPx, sz: +d.sz, side: d.side, posSide: d.posSide });
      added++;
      oldest = Math.min(oldest, +d.ts);
    }
    process.stdout.write(`\r  ${ccy} liquidations: ${out.length}`);
    if (!added) break;
    // Page backwards in time.
    before = String(oldest);
    await sleep(160);
  }
  process.stdout.write('\n');
  return out.sort((a, b) => a.ts - b.ts);
}

// ── OKX candle-style endpoints ──────────────────────────────────────────
async function fetchOkxCandles(endpoint, instId, bar, depth) {
  const out = [];
  let after = '';
  for (let page = 0; page < depth / 100 + 5; page++) {
    const p = `/api/v5/market/${endpoint}?instId=${instId}&bar=${bar}&limit=100${after ? `&after=${after}` : ''}`;
    const res = await retry(() => get('www.okx.com', p));
    const rows = res.data || [];
    if (!rows.length) break;
    const batch = rows.map(r => ({
      start: +r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4],
      volume: r[5] != null ? +r[5] : 0
    })).sort((a, b) => a.start - b.start);
    out.unshift(...batch);
    after = String(batch[0].start);
    process.stdout.write(`\r  ${instId} ${bar}: ${out.length}/${depth}`);
    if (out.length >= depth) break;
    await sleep(150);
  }
  process.stdout.write('\n');
  const seen = new Set();
  return out.filter(c => (seen.has(c.start) ? false : (seen.add(c.start), true))).sort((a, b) => a.start - b.start);
}

// ── Coinbase spot, for cross-venue comparison ───────────────────────────
async function fetchCoinbase(product, granularity, depth) {
  const out = [];
  let end = Math.floor(Date.now() / 1000);
  for (let page = 0; page < depth / 300 + 5; page++) {
    const start = end - granularity * 300;
    const p = `/products/${product}/candles?granularity=${granularity}&start=${new Date(start * 1000).toISOString()}&end=${new Date(end * 1000).toISOString()}`;
    let rows;
    try { rows = await retry(() => get('api.exchange.coinbase.com', p)); }
    catch (e) { break; }
    if (!Array.isArray(rows) || !rows.length) break;
    // Coinbase: [time, low, high, open, close, volume]
    const batch = rows.map(r => ({ start: r[0] * 1000, low: r[1], high: r[2], open: r[3], close: r[4], volume: r[5] }))
      .sort((a, b) => a.start - b.start);
    out.unshift(...batch);
    end = start;
    process.stdout.write(`\r  coinbase ${product} ${granularity}s: ${out.length}/${depth}`);
    if (out.length >= depth) break;
    await sleep(250);
  }
  process.stdout.write('\n');
  const seen = new Set();
  return out.filter(c => (seen.has(c.start) ? false : (seen.add(c.start), true))).sort((a, b) => a.start - b.start);
}

async function main() {
  const what = process.argv[2];
  const arg = process.argv[3] || '';
  fs.mkdirSync(OUT, { recursive: true });

  if (what === 'liquidations') {
    for (const ccy of arg.split(',')) {
      const rows = await fetchLiquidations(ccy);
      fs.writeFileSync(path.join(OUT, `${ccy}-liquidations.json`), JSON.stringify({ ccy, fetchedAt: new Date().toISOString(), rows }));
      if (rows.length) {
        const days = (rows[rows.length - 1].ts - rows[0].ts) / 86400000;
        console.log(`  saved ${rows.length} ${ccy} liquidations spanning ${days.toFixed(2)} days`);
      }
    }
  } else if (what === 'index') {
    for (const inst of arg.split(',')) {
      const rows = await fetchOkxCandles('history-index-candles', inst, '15m', 40000);
      fs.writeFileSync(path.join(OUT, `${inst}-index.json`), JSON.stringify({ inst, bar: '15m', rows }));
      console.log(`  saved ${rows.length} index bars for ${inst}`);
    }
  } else if (what === 'daily') {
    for (const inst of arg.split(',')) {
      const rows = await fetchOkxCandles('history-candles', inst, '1D', 3000);
      fs.writeFileSync(path.join(OUT, `${inst}-daily.json`), JSON.stringify({ inst, bar: '1D', rows }));
      const days = rows.length;
      console.log(`  saved ${days} daily bars for ${inst}, from ${new Date(rows[0].start).toISOString().slice(0, 10)}`);
    }
  } else if (what === 'spot') {
    for (const ccy of arg.split(',')) {
      const rows = await fetchCoinbase(`${ccy}-USD`, 60, 12000);
      fs.writeFileSync(path.join(OUT, `${ccy}-coinbase-1m.json`), JSON.stringify({ ccy, venue: 'coinbase', bar: '1m', rows }));
      console.log(`  saved ${rows.length} Coinbase 1m bars for ${ccy}`);
    }
  } else {
    console.error('usage: node research/fetch-extra.js <liquidations|index|daily|spot> <args>');
    process.exit(1);
  }
}

main().catch(e => { console.error('\nfailed:', e.message); process.exit(1); });
