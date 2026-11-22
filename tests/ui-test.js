// UI test for the price check badges, sort and image layout.
// Run: node tests/ui-test.js   (serves a fixture on a random port; no network needed except fonts)
const path = require('path'), fs = require('fs'), http = require('http'), os = require('os');
const { chromium } = require(process.env.PW || '/usr/local/lib/pnpm/5/.pnpm/playwright-core@1.59.1/node_modules/playwright-core');
const root = path.resolve(__dirname, '..');
let fails = 0, passes = 0;
const ok = (c, m) => { if (c) passes++; else { fails++; console.error('FAIL:', m); } };

const pc = (o) => Object.assign({ status: 'unknown', store_low_30d: null, pre_hike_price: null, pre_hike_month: null,
  real_discount_pct: null, history_url: null }, o);
const fixture = { generated_at: '2026-11-22T14:00:00+01:00', count: 6, deals: [
  { name: 'Amber <b>mus</b>', shop: 'POWER', price_sek: 339, was_price_sek: 999, url: 'https://example.com/a', category: 'computers', image: null,
    pricecheck: pc({ status: 'raised_before_sale', store_low_30d: 999, pre_hike_price: 349, pre_hike_month: 'oktober', real_discount_pct: 2.9,
      history_url: 'https://www.prisjakt.nu/produkt.php?p=4925912#statistics' }) },
  { name: 'Röd TV', shop: 'Webhallen', price_sek: 5000, was_price_sek: 9000, url: 'https://example.com/b', category: 'electronics', image: null,
    pricecheck: pc({ status: 'inflated', store_low_30d: 6000, real_discount_pct: 16.7, history_url: 'https://www.prisjakt.nu/produkt.php?p=1#statistics' }) },
  { name: 'OK kaffe', shop: 'POWER', price_sek: 500, was_price_sek: 1000, url: 'https://example.com/c', category: 'household', image: null,
    pricecheck: pc({ status: 'ok', store_low_30d: 1000, real_discount_pct: 50, history_url: 'https://www.prisjakt.nu/produkt.php?p=2#statistics' }) },
  { name: 'Okänd', shop: 'Kjell & Company', price_sek: 100, was_price_sek: 400, url: 'https://example.com/d', category: 'household', image: null,
    pricecheck: pc({ status: 'unknown', history_url: 'javascript:alert(1)' }) },
  { name: 'Utan check', shop: 'NetOnNet', price_sek: 90, was_price_sek: 100, url: 'https://example.com/e', category: 'household', image: null },
  { name: 'Dyrare', shop: 'POWER', price_sek: 1999, was_price_sek: 5399, url: 'https://example.com/f', category: 'household', image: null,
    pricecheck: pc({ status: 'raised_before_sale', store_low_30d: 5399, pre_hike_price: 1799, pre_hike_month: 'augusti', real_discount_pct: -11.1 }) },
]};

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sd-test-'));
fs.copyFileSync(path.join(root, 'index.html'), path.join(dir, 'index.html'));
fs.writeFileSync(path.join(dir, 'deals.json'), JSON.stringify(fixture));
const srv = http.createServer((q, r) => {
  const f = path.join(dir, decodeURIComponent(q.url.split('?')[0]).replace(/^\/$/, '/index.html'));
  if (!f.startsWith(dir) || !fs.existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { 'content-type': f.endsWith('.json') ? 'application/json' : 'text/html; charset=utf-8' }); r.end(fs.readFileSync(f));
}).listen(0, async () => {
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox'] });
  try {
    const ctx = await b.newContext({ viewport: { width: 390, height: 844 } });
    const p = await ctx.newPage();
    const errors = []; p.on('pageerror', e => errors.push(e.message));
    await p.goto(base); await p.waitForSelector('.card');
    const cards = p.locator('.card');
    ok(await cards.count() === 6, 'six cards');
    const t0 = await cards.nth(0).innerText();
    ok(t0.includes('HÖJT FÖRE REAN') || t0.includes('Höjt före rean'), 'amber badge text');
    ok(t0.includes('Var 349\u00a0kr i oktober · verklig rabatt ca 3\u00a0%'), 'amber line: ' + t0);
    ok(await cards.nth(0).locator('h2 b').count() === 0 && t0.includes('<b>mus</b>'), 'name escaped');
    const t1 = await cards.nth(1).innerText();
    ok(/uppblåst ordinariepris/i.test(t1) && t1.includes('Lägst 6\u00a0000\u00a0kr'), 'red badge');
    ok(await cards.nth(2).locator('.pc-box').count() === 0 && await cards.nth(2).locator('.pc-link').count() === 1, 'ok: only history link');
    ok((await cards.nth(3).innerText()).includes('Ej verifierad'), 'unknown subtle');
    ok(await cards.nth(3).locator('.pc-link').count() === 0, 'unsafe history url dropped');
    ok(await cards.nth(4).locator('.pc').count() === 0, 'no pricecheck -> nothing');
    ok((await cards.nth(5).innerText()).includes('dyrare än före prishöjningen'), 'negative real discount wording');
    // history link opens Prisjakt only, not the dealer page
    const popups = []; ctx.on('page', pg => popups.push(pg));
    const first = ctx.waitForEvent('page', { timeout: 8000 }).catch(() => null);
    await cards.nth(0).locator('.pc-link').click();
    await first; await p.waitForTimeout(1200);
    const urls = popups.map(pg => pg.url());
    ok(popups.length === 1, 'exactly one tab opened, got ' + JSON.stringify(urls));
    ok(urls.every(u => !u.includes('example.com')), 'dealer page not opened');
    for (const pg of popups) await pg.close();
    // sort
    await p.getByRole('button', { name: 'Verklig rabatt' }).click();
    const order = await p.locator('.card h2').allInnerTexts();
    ok(order[0] === 'Okänd' && order[1] === 'OK kaffe', 'real sort, unknown falls back to claimed 75%: ' + order);
    ok(order.indexOf('Amber <b>mus</b>') > order.indexOf('Utan check') - 1 && order[order.length - 1] === 'Dyrare', 'amber/negative sorted low: ' + order);
    await p.getByRole('button', { name: 'Störst rabatt' }).click();
    ok((await p.locator('.card h2').allInnerTexts())[0] === 'Okänd', 'claimed sort');
    await p.getByRole('button', { name: 'Standard' }).click();
    ok((await p.locator('.card h2').first().innerText()) === 'Amber <b>mus</b>', 'default order');
    // image layout: contained, fixed frame height
    const css = await p.evaluate(() => { const i = document.createElement('img'); i.className = 'card-image'; document.body.appendChild(i);
      const s = getComputedStyle(i).objectFit; i.remove(); return s; });
    ok(css === 'contain', 'images use object-fit: contain');
    const hs = await p.locator('.card-frame').evaluateAll(els => els.map(e => e.getBoundingClientRect().height));
    ok(new Set(hs.map(Math.round)).size === 1, 'even frame heights ' + hs);
    ok(errors.length === 0, 'no page errors: ' + errors.join('; '));
  } finally { await b.close(); srv.close(); }
  console.log(`${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
});
