const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await chromium.launch();
  const sizes = [[360, 740], [390, 844], [430, 932], [844, 390]];
  for (const [W, H] of sizes) {
    const ctx = await browser.newContext({ viewport: { width: W, height: H }, isMobile: true, hasTouch: true, deviceScaleFactor: 1, locale: 'en-US' });
    const page = await ctx.newPage(); const errs = []; page.on('pageerror', e => errs.push(e.message));
    await page.goto('http://localhost:8070/', { waitUntil: 'domcontentloaded' }); await sleep(3000);
    const cdp = await ctx.newCDPSession(page);
    const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
    const tabs = await page.evaluate(() => [...document.querySelectorAll('.bsn-item')].map(b => b.textContent.trim()));
    console.log(`\n== ${W}x${H} tabs: ${tabs.join(', ')}`);
    for (const t of tabs) {
      await page.evaluate((t) => [...document.querySelectorAll('.bsn-item')].forEach(b => { if (b.textContent.trim() === t) b.click(); }), t);
      await sleep(1200);
      const r = await page.evaluate(() => {
        const vw = innerWidth, scr = document.querySelector('.scr');
        const view = document.querySelector('.app-view.active');
        const wide = [];
        if (view) view.querySelectorAll('*').forEach(e => {
          const b = e.getBoundingClientRect(); if (b.width === 0 || b.right <= vw + 1) return;
          for (let p = e.parentElement; p && p !== view.parentElement; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return; }
          wide.push(`${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}.${String(e.className).split(' ')[0]} right=${Math.round(b.right)}`);
        });
        return { docW: document.documentElement.scrollWidth, vw, scrollH: scr.scrollHeight, clientH: scr.clientHeight, canScroll: scr.scrollHeight > scr.clientHeight + 2, view: view && view.id, wide: wide.slice(0, 5), nWide: wide.length };
      });
      let moved = null;
      if (r.canScroll) {
        const before = await page.evaluate(() => document.querySelector('.scr').scrollTop);
        const x = Math.round(W / 2), y0 = Math.round(H * 0.8), y1 = Math.round(H * 0.35);
        await touch('touchStart', x, y0); for (let i = 1; i <= 12; i++) { await touch('touchMove', x, y0 + (y1 - y0) * i / 12); await sleep(16); } await touch('touchEnd', x, y1); await sleep(500);
        moved = (await page.evaluate(() => document.querySelector('.scr').scrollTop)) - before;
      }
      console.log(`${t.padEnd(10)} view=${r.view} hOverflow=${r.docW > r.vw ? 'YES(' + r.docW + ')' : 'no'} scrollH=${r.scrollH}/${r.clientH} swipeMoved=${moved} wideEls=${r.nWide} ${r.nWide ? JSON.stringify(r.wide) : ''}`);
    }
    if (errs.length) console.log('page errors:', errs.slice(0, 3));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
