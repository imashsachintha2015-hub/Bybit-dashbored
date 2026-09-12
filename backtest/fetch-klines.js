#!/usr/bin/env node
/**
 * Pulls real perpetual-futures klines and caches them for the backtest harness.
 *
 * Usage:
 *   node backtest/fetch-klines.js BTCUSDT,ETHUSDT,SOLUSDT 1,5,15 3000
 *
 * Source selection: Bybit is tried first, since that is the venue the live
 * system trades on. Bybit's CDN geo-blocks some regions (including the one this
 * was built in), so OKX USDT-margined perpetuals are used as the fallback —
 * same instrument type, same 24/7 tape, and highly correlated pricing. The
 * source actually used is recorded in the cache file and printed in the
 * backtest report, because a result is only interpretable if you know which
 * venue's tape produced it.
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const OUT_DIR = path.join(__dirname, 'data');

function getJSON(host, urlPath) {
  return new Promise((resolve, reject) => {
    const req = https.request({ host, path: urlPath, method: 'GET', headers: { 'User-Agent': 'MASIS-Backtest/3.0' } }, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(new Error(`Non-JSON response from ${host}: ${body.slice(0, 160)}`)); }
      });
    });
    req.on('error', reject);
    req.setTimeout(20000, () => req.destroy(new Error('request timeout')));
    req.end();
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ─── Bybit V5 (preferred) ───
async function fetchBybit(symbol, interval, depth) {
  const out = [];
  let end = Date.now();
  while (out.length < depth) {
    const limit = Math.min(1000, depth - out.length);
    const res = await getJSON('api.bybit.com',
      `/v5/market/kline?category=linear&symbol=${symbol}&interval=${interval}&limit=${limit}&end=${end}`);
    if (res.retCode !== 0 || !res.result || !res.result.list || !res.result.list.length) {
      if (!out.length) throw new Error(res.retMsg || 'empty result');
      break;
    }
    const batch = res.result.list.map(r => ({
      start: +r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4], volume: +r[5], confirm: true
    })).sort((a, b) => a.start - b.start);
    out.unshift(...batch);
    end = batch[0].start - 1;
    process.stdout.write(`\r  [bybit] ${symbol} ${interval}m: ${out.length}/${depth}`);
    await sleep(120);
  }
  return out;
}

// ─── OKX perpetual swaps (fallback) ───
const OKX_BAR = { '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m', '60': '1H', '240': '4H' };
function okxInst(symbol) {
  const m = symbol.match(/^(.*)(USDT)$/);
  return m ? `${m[1]}-USDT-SWAP` : symbol;
}

async function fetchOkx(symbol, interval, depth) {
  const bar = OKX_BAR[interval];
  if (!bar) throw new Error(`OKX has no bar size mapped for interval ${interval}`);
  const inst = okxInst(symbol);
  const out = [];
  let after = '';
  while (out.length < depth) {
    const res = await getJSON('www.okx.com',
      `/api/v5/market/history-candles?instId=${inst}&bar=${bar}&limit=100${after ? `&after=${after}` : ''}`);
    if (res.code !== '0' || !res.data || !res.data.length) {
      if (!out.length) throw new Error(res.msg || 'empty result');
      break;
    }
    // OKX rows: [ts, o, h, l, c, vol(contracts), volCcy, volCcyQuote, confirm]
    const batch = res.data
      .filter(r => r[8] === '1') // confirmed bars only
      .map(r => ({ start: +r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4], volume: +r[6], confirm: true }))
      .sort((a, b) => a.start - b.start);
    if (!batch.length) break;
    out.unshift(...batch);
    after = String(batch[0].start);
    process.stdout.write(`\r  [okx] ${symbol} ${interval}m: ${out.length}/${depth}`);
    await sleep(140);
  }
  return out;
}

async function fetchSeries(symbol, interval, depth) {
  try {
    const rows = await fetchBybit(symbol, interval, depth);
    return { source: 'bybit', rows };
  } catch (e) {
    process.stdout.write(`\r  [bybit] ${symbol} ${interval}m unavailable (${e.message.slice(0, 60)}) — falling back to OKX\n`);
    const rows = await fetchOkx(symbol, interval, depth);
    return { source: 'okx', rows };
  }
}

function dedupe(rows) {
  const seen = new Set();
  return rows.filter(c => (seen.has(c.start) ? false : (seen.add(c.start), true))).sort((a, b) => a.start - b.start);
}

async function main() {
  const symbols = (process.argv[2] || 'BTCUSDT,ETHUSDT,SOLUSDT').split(',');
  const intervals = (process.argv[3] || '1,5,15').split(',');
  const depth = parseInt(process.argv[4] || '3000', 10);

  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const symbol of symbols) {
    // MERGE with whatever is already cached rather than replacing it. Fetching
    // one interval used to wipe the others out of the file, which silently
    // broke every consumer that needed a timeframe this run did not request.
    const file0 = path.join(OUT_DIR, `${symbol}.json`);
    let existing = { series: {} };
    if (fs.existsSync(file0)) {
      try { existing = JSON.parse(fs.readFileSync(file0, 'utf8')); } catch (e) { existing = { series: {} }; }
    }
    const bundle = { symbol, source: null, fetchedAt: new Date().toISOString(), series: existing.series || {} };
    for (const iv of intervals) {
      const { source, rows } = await fetchSeries(symbol, iv, depth);
      bundle.source = source;
      // MERGE with whatever was already cached for THIS SAME interval, too --
      // not just across intervals (above). A shallower re-fetch (smaller
      // --depth than a previous run) used to silently replace a deep cached
      // history with a shorter one, since dedupe(rows) here ignored
      // existing.series[iv] entirely. Now the union of old+new rows is kept,
      // so history only ever grows.
      const priorRows = (existing.series && existing.series[iv]) || [];
      bundle.series[iv] = dedupe([...priorRows, ...rows]);
      process.stdout.write('\n');
    }
    const file = path.join(OUT_DIR, `${symbol}.json`);
    fs.writeFileSync(file, JSON.stringify(bundle));
    const s = bundle.series[intervals[0]];
    console.log(`  saved ${symbol} from ${bundle.source}: ${s.length} x ${intervals[0]}m bars, ${new Date(s[0].start).toISOString()} → ${new Date(s[s.length - 1].start).toISOString()}`);
  }
}

main().catch(e => { console.error('\nFetch failed:', e.message); process.exit(1); });
