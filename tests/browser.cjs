// npm install playwright in your development environment before running.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  if (!process.env.SMOKE_PASSWORD) throw new Error('Set SMOKE_PASSWORD for the test admin');
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {}),
  });
  const context = await browser.newContext({
    // The HTTPS transport/CA is separately verified by docker_smoke.py.
    ignoreHTTPSErrors: true,
    httpCredentials: {username: 'admin', password: process.env.SMOKE_PASSWORD},
    viewport: {width: 1440, height: 1000},
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`https://localhost:${process.env.MANAGEMENT_PORT || 18443}`);
    await page.getByText('Worker подключён', {exact: false}).waitFor();
    await page.getByRole('button', {name: '＋ Новое правило'}).click();
    await page.getByLabel('Название', {exact: true}).fill('Browser smoke');
    await page.getByLabel('Входящий порт').fill('18082');
    await page.getByLabel('IPv4 назначения').fill('192.0.2.10');
    await page.getByLabel('Порт назначения', {exact: true}).fill('80');
    await page.getByRole('button', {name: 'Сохранить правило'}).click();
    await page.getByRole('dialog').waitFor({state: 'hidden'});
    let row = page.getByRole('row').filter({hasText: 'Browser smoke'});
    await row.getByRole('button', {name: 'Выключить Browser smoke'}).click();
    await row.getByRole('button', {name: 'Включить Browser smoke'}).waitFor();
    await row.getByRole('button', {name: 'Изменить правило', exact: true}).click();
    await page.getByLabel('Название', {exact: true}).fill('Browser edited');
    await page.getByRole('button', {name: 'Сохранить правило'}).click();
    await page.getByRole('dialog').waitFor({state: 'hidden'});
    row = page.getByRole('row').filter({hasText: 'Browser edited'});
    await row.waitFor();
    await page.reload();
    await row.waitFor();
    await page.setViewportSize({width: 390, height: 844});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.setViewportSize({width: 1440, height: 1000});
    if (process.env.SMOKE_SCREENSHOT) await page.screenshot({path: process.env.SMOKE_SCREENSHOT, fullPage: true});
    page.once('dialog', dialog => dialog.accept());
    await row.getByRole('button', {name: 'Удалить правило'}).click();
    await row.waitFor({state: 'hidden'});
    assert.deepEqual(errors, []);
    console.log('PASS: browser create, disable, edit, reload persistence, delete, mobile layout, no JS errors');
  } finally {
    // Clean up even when a UI assertion fails.
    const response = await context.request.get(`https://localhost:${process.env.MANAGEMENT_PORT || 18443}/api/rules`);
    if (response.ok()) {
      for (const r of (await response.json()).rules.filter(r => ['Browser smoke', 'Browser edited'].includes(r.name))) {
        await context.request.delete(`https://localhost:${process.env.MANAGEMENT_PORT || 18443}/api/rules/${r.id}`, {headers: {'X-Gokaskad': '1'}});
      }
    }
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
