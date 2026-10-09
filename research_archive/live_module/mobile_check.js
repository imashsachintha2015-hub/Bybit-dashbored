const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const W = +(process.argv[2] || 390), H = +(process.argv[3] || 844), OUT = process.argv[4] || '.';
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, locale: 'en-US', timezoneId: 'Asia/Colombo' });
  const page = await ctx.newPage();
  const errs = []; page.on('pageerror', e => errs.push(e.message));
  await page.goto('http://localhost:8070/', { waitUntil: 'domcontentloaded' });
  await sleep(3500);
  const cdp = await ctx.newCDPSession(page);
  const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
  const drag = async (x, y0, y1) => { await touch('touchStart', x, y0); for (let i = 1; i <= 14; i++) { await touch('touchMove', x, y0 + (y1 - y0) * i / 14); await sleep(16); } await touch('touchEnd', x, y1); await sleep(600); };
  const R = {};
  await page.evaluate(() => { window.dayKey = (ms) => { const d = new Date(ms), p = n => (n < 10 ? '0' : '') + n; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); }; });
  await page.locator('#bsnKalman').click(); await sleep(1800);
  R.header = await page.evaluate(() => ['ktBadge', 'ktModeBtn'].map(id => { const e = document.getElementById(id), r = e && e.getBoundingClientRect(); return [id, !!r && r.width > 0 && r.height > 0]; }).concat([['settingsBtn', [...document.querySelectorAll('#a5 .hbtn')].some(b => /Settings/.test(b.textContent) && b.getBoundingClientRect().width > 0)]]));
  R.hscroll = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth, document.querySelector('.scr').scrollWidth, document.querySelector('.scr').clientWidth]);
  // swipe starting on a table: the page must move, not a nested box
  const scr = () => page.evaluate(() => Math.round(document.querySelector('.scr').scrollTop));
  const a = await scr(); await drag(Math.round(W / 2), Math.round(H * 0.8), Math.round(H * 0.2)); const b = await scr();
  await page.evaluate(() => { const s = document.querySelector('.scr'); s.style.scrollBehavior = 'auto'; s.scrollTop = 1e6; }); await sleep(300);
  R.scroll = { start: a, afterSwipe: b, bottom: await scr(), scrollH: await page.evaluate(() => document.querySelector('.scr').scrollHeight), clientH: await page.evaluate(() => document.querySelector('.scr').clientHeight) };
  R.lastVisible = await page.evaluate(() => { const e = document.getElementById('ktEvents'), s = document.querySelector('.scr').getBoundingClientRect(), r = e.getBoundingClientRect(); return r.bottom <= s.bottom + 1; });
  await page.screenshot({ path: `${OUT}/kal_bottom_${W}.png` });
  await page.evaluate(() => { document.querySelector('.scr').scrollTop = 0; }); await sleep(300);
  await page.screenshot({ path: `${OUT}/kal_top_${W}.png` });
  // day filter: pick the day of the first listed trade
  const day = await page.evaluate(() => { const t = ktState.hist.trades[0]; return dayKey(t.exit_t); });
  R.allTrades = await page.locator('#ktTrades tr[data-sym]').count();
  await page.evaluate((d) => window.ktDaySet(d), day); await sleep(300);
  R.day = day; R.dayTrades = await page.locator('#ktTrades tr[data-sym]').count();
  R.daySummary = (await page.locator('#ktDaySum').innerText()).trim();
  R.expectDayTrades = await page.evaluate((d) => ktState.hist.trades.filter(t => dayKey(t.exit_t) === d).length, day);
  await page.evaluate(() => window.ktDaySet(dayKey(Date.now()))); await sleep(300);
  R.todayTrades = await page.locator('#ktTrades tr[data-sym]').count(); R.todayCap = (await page.locator('#ktTrCap').innerText()).trim();
  await page.evaluate(() => window.ktDaySet('')); await sleep(300);
  // row click -> chart
  const sym = await page.locator('#ktTrades tr[data-sym]').first().getAttribute('data-sym');
  await page.evaluate(() => document.querySelector('.scr').scrollTop = 0);
  await page.locator('#ktTrades tr[data-sym]').first().scrollIntoViewIfNeeded(); await page.locator('#ktTrades tr[data-sym]').first().tap(); await sleep(3000);
  R.rowClick = { sym, chartActive: await page.evaluate(() => document.getElementById('view-chart').classList.contains('active')), S: await page.evaluate(() => (window.S || document.querySelector('.bybit-sym-row') && '')), kalOn: await page.evaluate(() => !!window.indicatorState.kal), tickKal: (await page.locator('#tickKal').innerText()).trim(), engine: (await page.locator('#chartEngineBtn').innerText()).trim() };
  R.title = await page.evaluate(() => (document.querySelector('.bybit-topbar') || document.body).innerText.split('\n').slice(0, 6).join(' | '));
  await page.screenshot({ path: `${OUT}/kal_rowclick_${W}.png` });
  R.errors = errs;
  console.log(JSON.stringify(R, null, 1));
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
