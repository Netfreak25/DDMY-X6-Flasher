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

async function pageFixture(bootMapping = profile.boot_write_mapping_verified) {
  const context = await browser.newContext({viewport: {width: 1280, height: 1120}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], apiCalls = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/tools/x6_profile.json', route => route.fulfill({
    json: {...profile, boot_write_mapping_verified: bootMapping},
  }));
  let mode = 'normal';
  // Every API URL is fulfilled or aborted here; no status/ADFU request reaches Python.
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    apiCalls.push(path);
    if (path === '/api/pair') return route.fulfill({json: {origin: new URL(route.request().url()).origin, token: 'mock-session'}});
    if (path === '/api/jobs/acquire') return route.fulfill({json: {job: 'test-job'}});
    if (path.startsWith('/api/jobs/')) return route.fulfill({json: {ok: true}});
    if (path === '/api/profile') return route.fulfill({json: {...profile, boot_write_mapping_verified: bootMapping}});
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
      async bootstrap(){}
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
    // Exercise the second-click path after an ADFU switch outlasts activation.
    Object.defineProperty(navigator, 'userActivation', {value: {isActive: false}});
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

for (const helperAvailable of [false, true]) test(`direct Pages click with helper ${helperAvailable ? 'running opens USB chooser' : 'absent explains pairing failure'}`, async () => {
  const context = await browser.newContext({viewport: {width: 1280, height: 1100}});
  const page = await context.newPage(), unexpected = [], errors = [], helperRequests = [];
  const base = 'https://netfreak25.github.io/DDMY-X6-Flasher/';
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = route.request().url();
    if (new URL(url).origin === 'http://127.0.0.1:8765') {
      const path = new URL(url).pathname;
      helperRequests.push(path);
      if (!helperAvailable) return route.abort('connectionrefused');
      if (path === '/api/pair') return route.fulfill({json: {origin: 'http://127.0.0.1:8765', token: 'direct-session'}});
      if (path === '/api/jobs/acquire') return route.fulfill({json: {job: 'test-job'}});
      if (path.startsWith('/api/jobs/')) return route.fulfill({json: {ok: true}});
      if (path === '/api/status') return route.fulfill({json: {mode: 'adfu'}});
      unexpected.push(url);
      return route.abort();
    }
    if (!url.startsWith(base)) {unexpected.push(url); return route.abort();}
    const relative = url.slice(base.length) || 'index.html';
    if (relative.includes('..') || relative.includes('/api/')) {unexpected.push(url); return route.abort();}
    const contentType = relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' :
      relative.endsWith('.json') ? 'application/json' : relative.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({contentType, body: await readFile(relative)});
  });
  await page.addInitScript(() => {
    window.__usbCalls = [];
    Object.defineProperty(navigator, 'usb', {value: {
      getDevices: async () => {window.__usbCalls.push({method: 'getDevices'}); return [];},
      requestDevice: async options => {
        window.__usbCalls.push({method: 'requestDevice', active: navigator.userActivation.isActive, options});
        throw new Error('Test chooser opened; no physical device accessed.');
      },
      addEventListener: () => {},
    }});
  });
  await page.goto(base);
  await page.waitForFunction(() => !document.querySelector('#connect').disabled);
  assert.equal(await page.locator('#setup-note').isVisible(), true);
  assert.deepEqual(helperRequests, []);
  assert.deepEqual(await page.evaluate(() => window.__usbCalls), []);
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Vorgang gestoppt');
  if (helperAvailable) {
    assert.deepEqual(helperRequests, ['/api/pair', '/api/jobs/acquire', '/api/status', '/api/jobs/release']);
    assert.deepEqual(await page.evaluate(() => window.__usbCalls), [
      {method: 'getDevices'},
      {method: 'requestDevice', active: true, options: {filters: [{vendorId: 0x10d6, productId: 0x10d6}]}},
    ]);
    assert.equal(await page.locator('#setup-note').isVisible(), false);
  } else {
    assert.deepEqual(helperRequests, ['/api/pair']);
    assert.deepEqual(await page.evaluate(() => window.__usbCalls), []);
    assert.match(await page.locator('#activity-message').textContent(), /Windows-App konnte nicht gekoppelt/);
  }
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

test('native ownership blocks Pages before device discovery', async () => {
  const {page, context, errors, apiCalls} = await pageFixture();
  await page.route('**/api/jobs/acquire', route => route.fulfill({status: 409, json: {error: 'Windows-App verwendet den X6.'}}));
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Vorgang gestoppt');
  assert.match(await page.locator('#activity-message').textContent(), /Windows-App verwendet/);
  assert.equal(apiCalls.includes('/api/status'), false);
  assert.deepEqual(errors, []);
  await context.close();
});

test('disconnect closes WebUSB even when the helper has lost its lease', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page);
  await page.route('**/api/jobs/check', route => route.fulfill({status: 409, json: {error: 'Sitzung verloren'}}));
  await page.route('**/api/jobs/release', route => route.fulfill({status: 409, json: {error: 'Sitzung verloren'}}));
  await page.locator('#connect').click();
  await page.waitForFunction(() => document.querySelector('#device-badge').textContent === 'Getrennt');
  assert.equal(await page.locator('#dump').isDisabled(), true);
  assert.deepEqual(errors, []);
  await context.close();
});

test('Chrome pairs an HTTPS Pages tab with the real loopback HTTP endpoint and CORS', async () => {
  // Separate ephemeral helper, empty runtime, and hard blocks on all device APIs.
  const helper = spawn('python', ['-u', '-c', `
import sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, 'tools')
import x6_web as web
import x6_toolkit as toolkit
with tempfile.TemporaryDirectory() as runtime, \\
     patch.object(toolkit, 'probe_device', side_effect=AssertionError('No device access')), \\
     patch.object(toolkit, 'enter_adfu', side_effect=AssertionError('No device access')):
    with web.WebServer(0, toolkit.Profile.load(), Path(runtime), online=True) as server:
        print(server.origin, flush=True)
        server.serve_forever()
`], {stdio: ['ignore', 'pipe', 'pipe']});
  const context = await browser.newContext();
  try {
    const helperOrigin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Isolated helper did not start')), 10000);
      helper.stdout.once('data', chunk => {clearTimeout(timer); resolve(chunk.toString().trim());});
      helper.once('error', error => {clearTimeout(timer); reject(error);});
    });
    const base = 'https://netfreak25.github.io/DDMY-X6-Flasher/';
    const page = await context.newPage(), unexpected = [], diagnostics = [];
    // Current Chrome separates loopback-network from local-network. The
    // installed Playwright version still maps only the obsolete combined name.
    const cdp = await context.newCDPSession(page);
    const {targetInfo} = await cdp.send('Target.getTargetInfo');
    await cdp.send('Browser.setPermission', {permission: {name: 'loopback-network'}, setting: 'granted',
      origin: new URL(base).origin, browserContextId: targetInfo.browserContextId});
    page.on('console', message => {if (message.type() === 'error') diagnostics.push(message.text());});
    page.on('requestfailed', request => diagnostics.push(`${request.url()}: ${request.failure()?.errorText}`));
    await page.route('**/*', async route => {
      const url = route.request().url();
      if (url === `${helperOrigin}/api/pair`) return route.continue();
      if (url === `${base}lib/bridge.js`) return route.fulfill({contentType: 'text/javascript', body: await readFile('lib/bridge.js')});
      if (url === base) return route.fulfill({contentType: 'text/html', body: `<button id="pair">Verbinden</button><script type="module">
        import {pairHelper} from './lib/bridge.js';
        document.querySelector('#pair').onclick = async () => {
          try { const session = await pairHelper(${JSON.stringify(helperOrigin)}, 10000);
            window.pairResult = {origin: session.origin, tokenLength: session.token.length};
          } catch (error) { window.pairResult = {error: error.message}; }
        };
      </script>`});
      unexpected.push(url);
      return route.abort();
    });
    await page.goto(base);
    await page.locator('#pair').click();
    await page.waitForFunction(() => window.pairResult);
    assert.deepEqual(await page.evaluate(() => window.pairResult), {origin: helperOrigin, tokenLength: 43}, diagnostics.join('\n'));
    assert.deepEqual(unexpected, []);
  } finally {
    await context.close();
    if (helper.exitCode === null) {const stopped = once(helper, 'exit'); helper.kill(); await stopped;}
  }
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

test('incomplete dump remains downloadable and an unverified mapping requires the emergency checkbox', async () => {
  const {page, context, errors} = await pageFixture(false);
  await connect(page);
  await page.evaluate(() => {window.__badBoot = true;});
  await page.locator('#dump').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Unvollständige Sicherung gespeichert.');
  const backupMessage = await page.locator('#activity').textContent();
  assert.match(backupMessage, /Firmware: gültig/);
  assert.match(backupMessage, /Bootkopie 1: bad brec checksum/);
  assert.match(backupMessage, /Bootkopie 2: gültig/);
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

test('verified boot mapping permits normal restore and requires complete readback', async () => {
  const {page, context, errors} = await pageFixture();
  await connect(page);
  await page.locator('#backup-file').setInputFiles('private_dumps/web-tests/synthetic.x6backup.zip');
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Sicherung ist vollständig.');
  await page.evaluate(() => {window.__calls = [];});
  await page.locator('#restore').click();
  await page.locator('#confirmation').waitFor({state: 'visible'});
  assert.equal(await page.locator('#emergency-option').isHidden(), true);
  assert.equal(await page.locator('#confirm-go').isEnabled(), true);
  await page.locator('#confirm-go').click();
  await page.waitForFunction(() => document.querySelector('#activity-heading').textContent === 'Vollständig zurückgeschrieben und verifiziert.');
  assert.deepEqual(await page.evaluate(() => window.__calls), ['driver', 'mbrec', 'brec', 'lfi', 'final', 'read', 'boot0', 'boot1', 'reset']);
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
