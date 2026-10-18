/** Real browser, mocked bridge and USB modules: these tests never contact hardware. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { Buffer } from 'node:buffer';
import { once } from 'node:events';
import { chromium } from 'playwright';
import { packBackup } from '../../lib/archive.js';
import { profile, files as original } from '../fixtures.js';

let server, browser, origin;
const zip = packBackup(original, JSON.parse(new TextDecoder().decode(original['manifest.json'])));

before(async () => {
  // Static test server only: the hardware helper is never started here.
  server = spawn('python', ['-u', '-m', 'http.server', '0', '--bind', '127.0.0.1'], {stdio: ['ignore', 'pipe', 'pipe']});
  origin = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Preview server did not start')), 10000);
    server.stdout.on('data', chunk => {
      output += chunk;
      const match = /port (\d+)/.exec(output);
      if (match) {clearTimeout(timer); resolve(`http://127.0.0.1:${match[1]}`);}
    });
    server.once('error', error => {clearTimeout(timer); reject(error);});
  });
  browser = await chromium.launch({channel: 'chrome', headless: true});
  await mkdir('private_dumps/web-tests', {recursive: true});
  await writeFile('private_dumps/web-tests/synthetic.x6backup.zip', zip);
  await writeFile('private_dumps/web-tests/synthetic.bin', original['lfi.bin']);
});

after(async () => {
  await browser?.close();
  if (server && server.exitCode === null) {const stopped = once(server, 'exit'); server.kill(); await stopped;}
});

async function pageFixture() {
  const context = await browser.newContext({viewport: {width: 1280, height: 1120}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], apiCalls = [];
  page.on('pageerror', error => errors.push(error.message));
  let mode = 'normal';
  // Every API URL is fulfilled or aborted here; no status/ADFU request reaches Python.
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    apiCalls.push(path);
    if (path === '/api/profile') return route.fulfill({json: profile});
    if (path === '/api/status') return route.fulfill({json: {mode, serial: 'X6TEST000001'}});
    if (path === '/api/enter-adfu') {mode = 'adfu'; return route.fulfill({json: {mode}});}
    if (path === '/api/test-normal') {mode = 'normal'; return route.fulfill({json: {mode}});}
    errors.push(`Unexpected API request blocked: ${path}`);
    return route.abort();
  });
  await page.route('**/test-file/*', route => {
    const name = new URL(route.request().url()).pathname.split('/').at(-1);
    return route.fulfill({body: Buffer.from(original[name])});
  });
  await page.route('**/lib/transport.js', route => route.fulfill({contentType: 'text/javascript', body: `
    export const usbId=()=>({vendorId:0x10d6,productId:0x10d6});
    export const delay=ms=>new Promise(resolve=>setTimeout(resolve,Math.min(ms,1)));
    export class ActionsUSB{constructor(device){this.device=device;this.alive=false;}async open(){this.alive=true;}async info(){return new Uint8Array([0,67,65,68,70,85,68,48,81]);}async close(){this.alive=false;}}
  `}));
  await page.route('**/lib/driver.js', route => route.fulfill({contentType: 'text/javascript', body: `
    const file=async name=>new Uint8Array(await (await fetch('/test-file/'+name)).arrayBuffer());
    export class X6Driver{
      constructor(usb){this.usb=usb;}
      async flashDriver(){window.__calls.push('driver');}
      async readLfi(progress){window.__calls.push('read');await new Promise(r=>setTimeout(r,150));let data=await file('lfi.bin');progress?.(data.length,data.length);if(window.__mismatch)data[0]^=1;return data;}
      async readBoot(index){window.__calls.push('boot'+index);if(window.__badBoot&&index===0)throw Error('bad brec checksum');return index===0?{'mbrec.bin':await file('mbrec.bin'),'brec0.bin':await file('brec0.bin')}:{'mbrec_copy.bin':await file('mbrec_copy.bin'),'brec1.bin':await file('brec1.bin')};}
      async writeArea(name){window.__calls.push(name);if(window.__writeFailure)throw Error('USB-Befehl nicht bestätigt.');}
      async finalize(){window.__calls.push('final');}
      async reset(){window.__calls.push('reset');this.usb.alive=false;await fetch('/api/test-normal');}
    }
  `}));
  await page.addInitScript(() => {
    window.__calls = [];
    window.__saved = [];
    Object.defineProperty(navigator, 'usb', {value: {
      getDevices: async () => [], requestDevice: async () => ({vendorId: 0x10d6, productId: 0x10d6}),
      addEventListener: () => {},
    }});
    window.showSaveFilePicker = async () => {
      let data;
      return {createWritable: async () => ({write: async value => {data = value; window.__calls.push('save');}, close: async () => {window.__saved.push(data.length);}, abort: async () => {}}),
        getFile: async () => new Blob([data])};
    };
  });
  await page.goto(`${origin}/#session=mock-session`);
  await page.waitForFunction(() => !document.querySelector('#connect').disabled);
  return {page, context, errors, apiCalls};
}

async function connect(page) {
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#connect').textContent === 'USB-Zugriff erlauben');
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#device-badge').textContent === 'Verbunden');
}

async function selectFirmware(page) {
  await page.locator('#firmware-file').setInputFiles('private_dumps/web-tests/synthetic.bin');
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Firmware ist gültig.');
}

test('initial page is quiet, responsive and has no automatic hardware discovery', async () => {
  const {page, context, errors, apiCalls} = await pageFixture();
  assert.deepEqual(apiCalls, []);
  assert.equal(await page.locator('#flash').isDisabled(), true);
  assert.equal(await page.locator('#dump').isDisabled(), true);
  await page.screenshot({path: 'private_dumps/web-tests/desktop.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.screenshot({path: 'private_dumps/web-tests/mobile.png', fullPage: true});
  assert.deepEqual(errors, []);
  await context.close();
});

test('public Pages subpath opens without a helper and never sends a device request', async () => {
  const context = await browser.newContext({viewport: {width: 1280, height: 1100}});
  const page = await context.newPage(), unexpected = [], errors = [];
  const base = 'https://netfreak25.github.io/DDMY-X6-Flasher/';
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (!url.startsWith(base)) {unexpected.push(url); return route.abort();}
    const relative = url.slice(base.length) || 'index.html';
    if (relative.includes('..') || relative.includes('/api/')) {unexpected.push(url); return route.abort();}
    const contentType = relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' :
      relative.endsWith('.json') ? 'application/json' : relative.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({contentType, body: await readFile(relative)});
  });
  await page.goto(base);
  await page.waitForFunction(() => !document.querySelector('#connect').disabled);
  assert.equal(await page.locator('#setup-note').isVisible(), true);
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Vorgang gestoppt');
  assert.match(await page.locator('#activity-message').textContent(), /Windows-Helfer/);
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  await page.screenshot({path: 'private_dumps/web-tests/pages.png', fullPage: true});
  await context.close();
});

test('connect, valid file selection, comparison and disabled concurrent actions', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page);
  await selectFirmware(page);
  await page.locator('#verify').click();
  assert.equal(await page.locator('#flash').isDisabled(), true);
  assert.equal(await page.locator('#connect').isDisabled(), true);
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Firmware ist identisch.');
  await page.evaluate(() => {window.__mismatch = true;});
  await page.locator('#verify').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Firmware weicht ab.');
  assert.match(await page.locator('#activity-message').textContent(), /1 Bytes/);
  assert.deepEqual(errors, []);
  await context.close();
});

test('flash uses confirmation, saves before writing and shows verified restart', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page); await selectFirmware(page);
  await page.evaluate(() => {window.__calls = [];});
  await page.locator('#flash').click();
  await page.locator('#confirm-go').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'LFI geschrieben und verifiziert.');
  assert.deepEqual(await page.evaluate(() => window.__calls), ['read', 'save', 'driver', 'lfi', 'final', 'read', 'reset']);
  assert.equal(await page.locator('#device-badge').textContent(), 'Normal gestartet');
  assert.deepEqual(errors, []);
  await context.close();
});

test('incomplete dump remains downloadable and full restore requires its explicit emergency checkbox', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page);
  await page.evaluate(() => {window.__badBoot = true;});
  await page.locator('#dump').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Unvollständige Sicherung gespeichert.');
  assert.ok((await page.evaluate(() => window.__saved))[0] > profile.lfi_size);
  await page.locator('#backup-file').setInputFiles('private_dumps/web-tests/synthetic.x6backup.zip');
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Sicherung ist vollständig.');
  await page.locator('#restore').click();
  await page.locator('#confirmation').waitFor({state: 'visible'});
  assert.equal(await page.locator('#confirm-go').isDisabled(), true);
  await page.locator('#emergency-check').check();
  await page.locator('#confirm-go').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Firmware verifiziert, Boot-Rücklesung offen.');
  assert.ok(!(await page.evaluate(() => window.__calls)).includes('reset'));
  assert.deepEqual(errors, []);
  await context.close();
});

test('an uncertain write displays the failure with no finalization or reset', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page); await selectFirmware(page);
  await page.evaluate(() => {window.__writeFailure = true; window.__calls = [];});
  await page.locator('#flash').click(); await page.locator('#confirm-go').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Vorgang gestoppt');
  assert.deepEqual(await page.evaluate(() => window.__calls), ['read', 'save', 'driver', 'lfi']);
  assert.deepEqual(errors, []);
  await context.close();
});
