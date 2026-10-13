// Browser test of the whole demo against REAL regions (joining step J7). Not part of `npm test`: it needs the stack running.
//
//   node tests/browser/integration.cjs keys     API-key mode: spam one shopper, limit a region, change power, add Bengaluru
//   node tests/browser/integration.cjs auth     sign-in mode: the restricted item, a crash without signing in again, the
//                                               person-keyed rate limit, a denied write, the four-row Tamper
//
// One-off setup:  npm install --no-save puppeteer-core     (and Chrome or Edge; set CHROME to its path if it is not found)
// Prerequisites (see README, "Run the browser test"): the regions and the chaos agent running (REGIONS=mum,pun,del,blr) on a
// CLEAN data folder, and the console started with VITE_SHOPPER_KEYS (keys mode) or VITE_AUTH_ENABLED=1 (auth mode).
// Auth mode also needs the claim codes: save the block that `python scripts/true_stack.py up --provision` prints in
// CLAIM_CODES_FILE (default .local/codes.txt). Each code works once, so a second auth run needs a new provision.
//
// Every assertion is a real browser action followed by a real answer from a region; nothing is simulated.
const fs = require('node:fs')
const path = require('node:path')
const puppeteer = require('puppeteer-core')

const MODE = process.argv[2] || 'keys'
const BASE = process.env.CONSOLE_URL || 'http://127.0.0.1:5175'
const PORTS = { mum: 8001, pun: 8002, del: 8003, blr: 8004 }
const NAME = { mum: 'Mumbai', pun: 'Pune', del: 'Delhi', blr: 'Bengaluru' }
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find((p) => fs.existsSync(p))

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
const check = (name, ok, detail = '') => {
  results.push({ name, ok: Boolean(ok), detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`)
}
async function until(fn, ms = 20000, every = 300) {
  const end = Date.now() + ms
  let v
  while (Date.now() < end) { try { v = await fn() } catch { v = undefined } if (v) return v; await sleep(every) }
  return v
}
const status = async (r) => { try { return await (await fetch(`http://127.0.0.1:${PORTS[r]}/v1/status`)).json() } catch { return null } }
const eventTotal = async () => { let n = 0; for (const r of ['mum', 'pun', 'del']) n = Math.max(n, (await status(r))?.event_count ?? 0); return n }
const admin = async (r, body) => (await fetch(`http://127.0.0.1:${PORTS[r]}/admin/config`, {
  method: 'POST', headers: { 'content-type': 'application/json', 'X-API-Key': 'dev-admin' }, body: JSON.stringify(body) })).json()

async function newPage(browser) {
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 1100 })
  page.seen = []
  page.on('pageerror', (e) => console.log('  [page error]', e.message))
  page.on('response', (res) => {
    const u = res.url(), m = res.request().method()
    if (m !== 'GET' && /\/api\//.test(u)) page.seen.push({ m, url: u, status: res.status() })
  })
  return page
}
const text = async (page) => { await page.bringToFront(); return page.evaluate(() => document.body.innerText) }
async function click(page, selector, label, nth = 0) {
  await page.bringToFront()   // a background window is throttled by the browser; act in the one being driven
  const h = await page.evaluateHandle((selector, label, nth) => {
    const all = [...document.querySelectorAll(selector)].filter((e) => {
      const t = (e.getAttribute('aria-label') || e.textContent || '').trim()
      return t === label || t.startsWith(label)
    })
    return all[nth]
  }, selector, label, nth)
  const el = h.asElement()
  if (!el) throw new Error(`no ${selector} "${label}"`)
  await el.click()
}
const btn = (page, label, nth) => click(page, 'button,[role=tab],summary', label, nth)
async function hold(page, label, ms = 1500) {
  const h = await page.evaluateHandle((label) => [...document.querySelectorAll('button')].find((e) => e.getAttribute('aria-label') === label), label)
  const el = h.asElement()
  if (!el) throw new Error(`no hold button ${label}`)
  if (await el.evaluate((e) => e.disabled)) throw new Error(`hold button ${label} is disabled`)
  await el.evaluate((e) => e.scrollIntoView({ block: 'center' })); await sleep(300)
  const box = await el.boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down(); await sleep(ms); await page.mouse.up()
}
const meter = (page, who) => page.evaluate((who) => {
  const m = document.querySelector(`[role=meter][aria-label="${who}: requests left"]`)
  return m ? Number(m.getAttribute('aria-valuenow')) : null
}, who)
const errorsShown = async (page) => {
  await btn(page, 'Evidence'); await sleep(600)
  const m = (await text(page)).match(/(\d+)\s*\n\s*Errors shown to shoppers/)
  await btn(page, 'Overview'); await sleep(300)
  return m ? Number(m[1]) : null
}
const openNetwork = async (page) => {
  if (!(await text(page)).includes('Limit each shopper')) { try { await btn(page, 'More controls') } catch { /* already open */ } await sleep(400); await btn(page, 'Network'); await sleep(600) }
}
const pickShopper = async (page, who) => {
  await page.bringToFront()
  const h = await page.evaluateHandle((who) => [...document.querySelectorAll('[role=tab]')].find((e) => e.textContent.includes(who)), who)
  const el = h.asElement(); if (!el) throw new Error(`no shopper tab ${who}`)
  await el.click(); await sleep(300)
}
const add = async (page, item) => { await btn(page, `Add ${item}`); await sleep(1200) }
const inRow = async (page, rowText, label) => { await page.bringToFront(); return inRowH(page, rowText, label) }
const inRowH = (page, rowText, label) => page.evaluateHandle((rowText, label) => {
  const row = [...document.querySelectorAll('.limit-row')].find((r) => r.innerText.includes(rowText))
  return row && [...row.querySelectorAll('button')].find((b) => b.textContent.trim() === label)
}, rowText, label).then((h) => h.asElement())
const route = (page) => page.evaluate(() => document.querySelector('.cx-route')?.innerText.replace(/\s+/g, ' ').trim() || '')

// ---------------------------------------------------------------------------------------------------------------
async function keysMode(browser) {
  const page = await newPage(browser)
  await page.goto(BASE + '/demo', { waitUntil: 'domcontentloaded' })
  const up = await until(async () => { const t = await text(page); return t.includes('CONVERGED') && /3 of 3 regions reachable/.test(t) }, 30000)
  check('console loads against three real regions, CONVERGED', up)

  const e0 = await eventTotal()
  await pickShopper(page, 'Alice'); await add(page, 'Filter coffee 500 g')
  check('an add-to-cart is a real write on a region', await until(async () => (await eventTotal()) > e0, 8000))

  // --- spam one shopper ---------------------------------------------------------------------------------------
  await openNetwork(page)
  const five = await inRow(page, 'Limit each shopper', '5'); await five.click(); await sleep(1500)
  await pickShopper(page, 'Bob'); await btn(page, 'Spam as Bob')
  await until(async () => !(await text(page)).includes('is spamming'), 40000, 500)
  const bob = await meter(page, 'Bob'), alice = await meter(page, 'Alice')
  check('Spam as Bob empties Bob\'s own meter only', bob === 0 && alice > 0, `Bob ${bob}, Alice ${alice} left`)
  const refused = page.seen.filter((r) => r.status === 429).length
  check('the region refused Bob with 429 (a calm notice, not an error)', refused > 0, `${refused} refusals`)
  check('errors shown to shoppers stays 0 after spam', (await errorsShown(page)) === 0)
  await openNetwork(page)
  await (await inRow(page, 'Limit each shopper', 'Off')).click(); await sleep(1000)

  // --- limit a region: a failover, not an error -------------------------------------------------------------------
  await openNetwork(page)
  await (await inRow(page, 'Limit a region', '1')).click().catch(() => {})
  const before = await eventTotal()
  const toggleMumbai = async () => {
    const h = await page.evaluateHandle(() => [...document.querySelectorAll('.limit-row--region')].find((r) => r.innerText.includes('Mumbai')).querySelector('label.switch'))
    await h.asElement().click(); await sleep(1500)
  }
  await toggleMumbai()
  check('Mumbai reports its limit in /v1/status', (await status('mum'))?.rate?.region_limit_per_s > 0)
  await pickShopper(page, 'Alice')
  await btn(page, 'Spam as Alice')            // 25 quick adds: Alice's home is Mumbai, which is over its limit
  await btn(page, 'Overview')
  const routed = await until(async () => /write limit|Pune|Delhi/.test(await route(page)), 12000, 150)
  check('the route line moves away from the limited region and says why', routed, await route(page))
  await openNetwork(page)
  await until(async () => !(await text(page)).includes('is spamming'), 40000, 500)
  await btn(page, 'Evidence'); await sleep(900)
  const present = (await text(page)).match(/(\d+) \/ (\d+)\s*\n\s*Present on survivors/)
  check('every acknowledged write is present on the survivors (the console\'s own counter)', present && present[1] === present[2] && Number(present[1]) > 5, present ? `${present[1]} / ${present[2]}` : 'no counter')
  await btn(page, 'Overview'); await sleep(300)
  check('errors shown to shoppers stays 0 while Mumbai is limited', (await errorsShown(page)) === 0)
  const caughtUp = await until(async () => { const c = await Promise.all(['mum', 'pun', 'del'].map(status)); return c.every(Boolean) && new Set(c.map((x) => x.event_digest)).size === 1 }, 30000, 500)
  check('replication into the limited region still works: Mumbai holds what the others wrote', caughtUp && (await status('mum')).rate.region_limit_per_s > 0)
  await openNetwork(page)
  await toggleMumbai()
  check('lifting the limit: Mumbai reports no limit', (await status('mum'))?.rate?.region_limit_per_s === 0)

  // --- power ------------------------------------------------------------------------------------------------------
  await btn(page, 'Overview'); await sleep(500)
  await btn(page, 'High', 0); await sleep(1500)
  check('the High dial changes status.load.batch on the real region', (await status('mum'))?.load?.batch === 32, `batch ${(await status('mum'))?.load?.batch}`)
  await btn(page, 'Medium', 0); await sleep(1000)

  // --- add Bengaluru --------------------------------------------------------------------------------------------------
  await btn(page, 'Add Bengaluru')
  const joined = await until(async () => {
    const s = await Promise.all(['mum', 'pun', 'del', 'blr'].map(status))
    return s.every(Boolean) && new Set(s.map((x) => x.event_digest)).size === 1
  }, 60000, 1000)
  check('Bengaluru joins empty and reaches the same digest as the others', joined)
  await btn(page, 'Overview')
  const four = await until(async () => /CONVERGED/.test(await text(page)) && /4 of 4 regions reachable/.test(await text(page)), 60000)
  check('the console shows four regions CONVERGED', four, four ? '' : (await text(page)).match(/\d of \d regions reachable|CONVERGED|SYNCING|UNKNOWN|DIVERGED/g)?.join(' '))
}

// ---------------------------------------------------------------------------------------------------------------
async function authMode(browser) {
  const codesFile = process.env.CLAIM_CODES_FILE || path.join(__dirname, '..', '..', '.local', 'codes.txt')
  const codes = fs.existsSync(codesFile) ? fs.readFileSync(codesFile, 'utf8') : ''
  const ctx = browser.defaultBrowserContext()   // one wallet (IndexedDB) shared by both windows, like two tabs of one browser
  const page = await newPage(browser)
  await page.goto(BASE + '/demo', { waitUntil: 'domcontentloaded' })
  const idHost = await until(async () => page.$('[data-testid=identity-host]'), 30000)
  check('the identity section appears because the backend is in sign-in mode', Boolean(idHost))
  if (!idHost) return
  const start = (await status('mum'))?.event_count ?? 0

  // claim codes -> wallet -> sign in as everyone
  if (!(await text(page)).includes('credentials in this wallet') || /No credentials/.test(await text(page))) {
    if (!codes.trim()) { check('claim codes are available (.local/codes.txt)', false, 'run `python scripts/true_stack.py up --provision` and save its block'); return }
    const area = await page.$('textarea[aria-label="Claim codes"]')
    await area.type(codes)
    await btn(page, 'Add to wallet')
  }
  check('claim codes become credentials in the wallet', await until(async () => /7 of 7 credentials in this wallet/.test(await text(page)), 30000))
  await btn(page, 'Sign in everyone')
  const signedIn = await until(async () => (await text(page)).includes('signed in as Alice'), 60000)
  if (!signedIn) { const t = await text(page); const i = t.indexOf('Identity of'); console.log('  [identity panel]', JSON.stringify(t.slice(i, i + 700))) }
  check('every member signs in with a real SD-JWT presentation', signedIn)
  await sleep(2500)

  // --- a restricted item without the claim: denied, nothing stored --------------------------------------------------
  await pickShopper(page, 'Alice')
  await add(page, 'Filter coffee 500 g')
  const base = await eventTotal()
  const seen0 = page.seen.length
  await btn(page, 'Add Craft beer sampler'); await sleep(1800)
  const denied = page.seen.slice(seen0).find((r) => r.status === 403)
  check('without the claim the region answers 403 claim_required', Boolean(denied), denied ? denied.url.split('/api/')[1] : 'no 403 seen')
  check('the "prove you are over 18" prompt is shown', /adults only|over 18/i.test(await text(page)))
  check('nothing was stored for the denied item', (await eventTotal()) === base, `${base} -> ${await eventTotal()}`)

  // --- prove the claim: the panel lists only that claim; the add succeeds -----------------------------------------------
  await btn(page, 'Sign in again'); await sleep(4000)
  const panel = await text(page)
  const received = panel.split(/what the shop received/i)[1] || ''
  check('the panel\'s "what the shop received" lists only the proven claim', /Over 18/.test(received.split('Every sign-in')[0]) && !/Loyalty member/.test(received.split('Every sign-in')[0]))
  await btn(page, 'Add Craft beer sampler'); await sleep(2000)
  check('after proving it the restricted add is stored', (await eventTotal()) === base + 1, `${base} -> ${await eventTotal()}`)

  // --- the limit follows the person (a second window, the same credential) ------------------------------------------------
  const window2 = await newPage(browser)
  await window2.goto(BASE + '/demo', { waitUntil: 'domcontentloaded' })
  await until(async () => window2.$('[data-testid=identity-host]'), 30000)
  await pickShopper(window2, 'Bob')
  await window2.select('select[aria-label="Whose identity"]', 'Bob')
  await btn(window2, 'Sign in with wallet')
  check('Bob signs in again from a second window (same credential, new session)', await until(async () => (await text(window2)).includes('signed in as Bob'), 40000))
  await openNetwork(page)
  await (await inRow(page, 'Limit each shopper', '5')).click(); await sleep(2000)   // changed through the Admin session, not an API key
  await pickShopper(page, 'Bob'); await btn(page, 'Spam as Bob')           // window 1 spends Bob's whole allowance...
  await sleep(1200)
  const s3 = window2.seen.length
  for (let i = 0; i < 3; i++) { await btn(window2, 'Add Backpack'); await sleep(300) }   // ...while window 2 (same credential) adds
  await sleep(1500)
  await until(async () => !(await text(page)).includes('is spamming'), 60000, 500)
  const refusedBob = page.seen.filter((r) => r.status === 429).length
  check('Bob is refused once his allowance is spent (window 1)', refusedBob > 0, `${refusedBob} refusals`)
  const again = window2.seen.slice(s3).filter((r) => r.status === 429).length
  check('window 2, same credential, is refused too: the limit follows the person, not the device', again > 0, `${again} refusals in window 2`)
  await window2.close()
  await openNetwork(page)
  await (await inRow(page, 'Limit each shopper', 'Off')).click(); await sleep(1500)
  await btn(page, 'Overview'); await sleep(300)

  // --- a denied write in the browser: signed out means 401 and nothing stored --------------------------------------------
  await pickShopper(page, 'Esha')
  await btn(page, 'Sign out'); await sleep(500)
  const b2 = await eventTotal(), s2 = page.seen.length
  await add(page, 'Wireless earbuds')
  const unauth = page.seen.slice(s2).find((r) => r.status === 401)
  check('a signed-out shopper\'s add is refused by the region with 401', Boolean(unauth))
  check('...and nothing is stored (not just a disabled button)', (await eventTotal()) === b2)
  await btn(page, 'Sign in with wallet'); await sleep(3500)

  // --- crash the serving region: the next add works with no new sign-in ------------------------------------------------------
  await pickShopper(page, 'Alice')
  const loginsBefore = page.seen.filter((r) => /\/v1\/auth\/session/.test(r.url)).length
  await btn(page, 'Overview')
  await hold(page, 'Hold to crash Mumbai')
  check('Mumbai is down', await until(async () => !(await status('mum')), 15000))
  const eb = await eventTotal(); await add(page, 'Wireless earbuds')
  check('the next add succeeds on another region', await until(async () => (await eventTotal()) > eb, 15000), await route(page))
  check('...with no new sign-in', page.seen.filter((r) => /\/v1\/auth\/session/.test(r.url)).length === loginsBefore && /signed in as Alice/.test(await text(page)))
  check('errors shown to shoppers stays 0 across the crash', (await errorsShown(page)) === 0)

  // --- Tamper: four rows, all rejected -------------------------------------------------------------------------------------------
  await pickShopper(page, 'Esha')
  await add(page, 'Desk lamp')
  await openNetwork(page)
  await click(page, 'button', 'Pune').catch(() => {})
  const sh = (await status('pun')).state_hash, q0 = (await status('pun')).quarantined
  await btn(page, 'Tamper')
  await until(async () => /revoked/.test(await text(page)) || false, 3000)
  await btn(page, 'Security'); await sleep(500)
  const rows = await until(async () => { const t = await text(page); return /revoked_credential/.test(t) ? t : null }, 60000, 1000)
  check('Tamper shows a fourth row: the credential that wrote the event was revoked', Boolean(rows))
  const rejected = await page.evaluate(() => [...document.querySelectorAll('.tamper li')].filter((l) => l.classList.contains('is-ok')).length)
  check('all four rows are rejected', rejected >= 4, `${rejected} rejected`)
  const after = await status('pun')
  check('the state hash did not change and the event is in quarantine', after.state_hash === sh && after.quarantined > q0, `quarantined ${q0} -> ${after.quarantined}`)
  void ctx; void start
}

;(async () => {
  if (!CHROME) { console.error('No Chrome/Edge found: set CHROME=<path>'); process.exit(2) }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox'] })
  try { await (MODE === 'auth' ? authMode(browser) : keysMode(browser)) } catch (e) { check('the run completed without an unexpected error', false, e.message) } finally { await browser.close() }
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length} of ${results.length} checks passed`)
  process.exit(failed.length ? 1 : 0)
})()
