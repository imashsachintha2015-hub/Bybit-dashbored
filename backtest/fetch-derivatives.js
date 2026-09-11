#!/usr/bin/env node
/**
 * Fetches the derivatives context the Positioning Analyst reads: open interest,
 * funding, crowd long/short ratio, and REAL taker buy/sell volume.
 *
 * That last one matters for honesty. Until now the backtest inferred taker
 * aggression from candle shape, because klines carry no trade data. OKX
 * publishes actual taker buy and sell notional per period, so the backtest can
 * use measured aggression instead of a proxy for the one signal that most needs
 * to be real.
 *
 * Resolution is 1H (the API offers only 5m / 1H / 1D, and 5m retains barely two
 * days). 1H is the right home for these anyway — open interest and funding are
 * slow, positional signals, and reading them at 5m is mostly reading noise.
 *
 * Usage: node backtest/fetch-derivatives.js BTC,ETH,SOL
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const OUT_DIR = path.join(__dirname, 'data');

function getJSON(urlPath) {
  return new Promise((resolve, reject) => {
    const req = https.request({ host: 'www.okx.com', path: urlPath, method: 'GET',
      headers: { 'User-Agent': 'MASIS-Backtest/3.1' } }, res => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(new Error(body.slice(0, 150))); } });
    });
    req.on('error', reject);
    req.setTimeout(20000, () => req.destroy(new Error('timeout')));
    req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const coins = (process.argv[2] || 'BTC,ETH,SOL').split(',');
  fs.mkdirSync(OUT_DIR, { recursive: true });

  for (const ccy of coins) {
    const out = { ccy, period: '1H', fetchedAt: new Date().toISOString(), rows: {} };

    // rows keyed by timestamp so the four series align without interpolation.
    const put = (ts, key, val) => {
      out.rows[ts] = out.rows[ts] || { ts: +ts };
      out.rows[ts][key] = val;
    };

    const oi = await getJSON(`/api/v5/rubik/stat/contracts/open-interest-volume?ccy=${ccy}&period=1H`);
    for (const r of (oi.data || [])) { put(r[0], 'oiUsd', +r[1]); put(r[0], 'volUsd', +r[2]); }
    await sleep(200);

    const ls = await getJSON(`/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=${ccy}&period=1H`);
    for (const r of (ls.data || [])) put(r[0], 'longShortRatio', +r[1]);
    await sleep(200);

    const tv = await getJSON(`/api/v5/rubik/stat/taker-volume?ccy=${ccy}&instType=CONTRACTS&period=1H`);
    // OKX returns [ts, sellVol, buyVol] for taker volume.
    for (const r of (tv.data || [])) { put(r[0], 'takerSell', +r[1]); put(r[0], 'takerBuy', +r[2]); }
    await sleep(200);

    const fr = await getJSON(`/api/v5/public/funding-rate-history?instId=${ccy}-USDT-SWAP&limit=100`);
    for (const r of (fr.data || [])) put(r.fundingTime, 'fundingRate', +r.fundingRate);

    const series = Object.values(out.rows).sort((a, b) => a.ts - b.ts);
    // Funding settles 3x daily; carry the last known value forward so every
    // hourly row has one rather than a hole.
    let lastFunding = null;
    for (const row of series) {
      if (row.fundingRate != null) lastFunding = row.fundingRate;
      else if (lastFunding != null) row.fundingRate = lastFunding;
    }

    const file = path.join(OUT_DIR, `${ccy}-derivs.json`);
    fs.writeFileSync(file, JSON.stringify({ ccy: out.ccy, period: out.period, fetchedAt: out.fetchedAt, series }));
    const withAll = series.filter(r => r.oiUsd && r.takerBuy && r.longShortRatio).length;
    console.log(`  ${ccy}: ${series.length} hourly rows (${withAll} complete), ` +
      `${new Date(series[0].ts).toISOString().slice(0, 16)} → ${new Date(series[series.length - 1].ts).toISOString().slice(0, 16)}`);
  }
}

main().catch(e => { console.error('Fetch failed:', e.message); process.exit(1); });
