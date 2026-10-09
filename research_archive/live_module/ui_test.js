// Browser check of the Kalman Trend mode in index.html against ui_mock_server.py (port 8070).
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const OUT = process.argv[2] || '.';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function errorsOf(page, url, waitMs) {
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await sleep(waitMs);
  return errs;
}

(async () => {
  const browser = await chromium.launch();
  const report = {};

  // 0) baseline: page errors of the original page (to tell new errors from old ones)
  {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' })).newPage();
    const errs = await errorsOf(page, 'http://localhost:8070/index_orig.html', 4000);
    report.baseline_pageerrors = errs.filter(e => e.startsWith('pageerror'));
    await page.context().close();
  }

  // 1) desktop: mode menu -> Kalman, overlay on the Bybit canvas, panel
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' });
  const page = await ctx.newPage();
  const errs = await errorsOf(page, 'http://localhost:8070/', 4000);
  await page.locator('.dash-champ-toggle-btn:visible').first().click();
  await sleep(300);
  report.menu_items = await page.locator('#modeMenu button').allInnerTexts();
  await page.screenshot({ path: OUT + '/ui_menu.png', clip: { x: 900, y: 0, width: 540, height: 260 } });
  await page.locator('#modeMenu button', { hasText: 'Kalman Trend' }).click();
  await sleep(2500);
  report.toggle_label = (await page.locator('.dash-champ-toggle-btn:visible').first().innerText()).trim();
  report.posts = await (await page.request.get('http://localhost:8070/api/_posts')).json();
  report.strip = (await page.locator('#stripName').innerText()).trim();
  report.kal_indicator_on = await page.evaluate(() => !!(window.indicatorState && window.indicatorState.kal));
  // 4H timeframe on the canvas chart
  await page.locator('.ctf[data-tf="240"]').first().click();
  await sleep(2500);
  report.tickKal = (await page.locator('#tickKal').innerText()).trim();
  report.chart_engine_btn = (await page.locator('#chartEngineBtn').innerText()).trim();
  await page.locator('#a1').screenshot({ path: OUT + '/ui_chart_4h.png' });
  // 1H timeframe as well
  await page.locator('.ctf[data-tf="60"]').first().click();
  await sleep(2500);
  report.tickKal_1h = (await page.locator('#tickKal').innerText()).trim();
  await page.locator('#a1').screenshot({ path: OUT + '/ui_chart_1h.png' });
  // panel
  await page.evaluate(() => window.switchView('kalman', 4));
  await sleep(1500);
  report.badge = (await page.locator('#ktBadge').innerText()).trim();
  report.tiles = await page.locator('#ktTiles .kt-tile').count();
  report.radar_rows = await page.locator('#ktRadar tr').count();
  report.pos_rows = await page.locator('#ktPos tr').count();
  report.trade_rows = await page.locator('#ktTrades tr').count();
  report.mode_btn = (await page.locator('#ktModeBtn').innerText()).trim();
  await page.locator('#a5').screenshot({ path: OUT + '/ui_panel.png' });
  // settings: open, change risk, save
  await page.evaluate(() => window.ktToggleSettings());
  await sleep(300);
  report.live_checkbox_disabled = await page.locator('#ktLive').isDisabled();
  await page.fill('#ktRisk', '0.4');
  await page.evaluate(() => window.ktSaveSettings());
  await sleep(1000);
  const posts2 = await (await page.request.get('http://localhost:8070/api/_posts')).json();
  report.settings_post = posts2.filter(p => p.path === '/api/kalman/settings').map(p => p.body);
  // switch back to Championship through the menu
  await page.locator('.dash-champ-toggle-btn:visible').first().click();
  await sleep(300);
  await page.locator('#modeMenu button', { hasText: 'Championship' }).click();
  await sleep(1200);
  report.toggle_label_after = (await page.locator('.dash-champ-toggle-btn:visible').first().innerText()).trim();
  report.new_pageerrors = errs.filter(e => e.startsWith('pageerror') && !report.baseline_pageerrors.includes(e));
  report.kalman_console_errors = errs.filter(e => /kalman|kt[A-Z_]|Kalman/.test(e));
  await ctx.close();

  // 2) mobile: Kalman tab
  const m = await (await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'en-US' })).newPage();
  const merrs = await errorsOf(m, 'http://localhost:8070/', 4000);
  await m.locator('#bsnKalman').click();
  await sleep(1500);
  report.mobile_view_active = await m.evaluate(() => document.getElementById('view-kalman').classList.contains('active'));
  await m.screenshot({ path: OUT + '/ui_mobile.png', fullPage: false });
  report.mobile_new_pageerrors = merrs.filter(e => e.startsWith('pageerror') && !report.baseline_pageerrors.includes(e));
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
})().catch(e => { console.error('TEST FAILED', e); process.exit(1); });
