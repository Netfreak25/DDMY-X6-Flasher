import { test } from 'node:test';
import assert from 'node:assert/strict';
import { X6Driver, bootLayout, brecLength, prepareBrecTransfer } from '../lib/driver.js';
import { sha256, view, wordSum } from '../lib/formats.js';
import { profile, files, payload } from './fixtures.js';

test('BREC writer prepares one transfer copy; original backup survives unchanged', async () => {
  const original = files['brec0.bin'].slice(), saved = original.slice(), calls = [];
  const driver = new X6Driver({writeFlash: async (sector, data) => calls.push({sector, data})}, profile, payload);
  driver.mode = 'flash';
  await driver.writeArea('brec', original);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].sector, Number(profile.protocol.brec_sector));
  const transfer = calls[0].data, adjusted = transfer.slice();
  const headerSum = wordSum(adjusted.subarray(4, 512), 2);
  view(adjusted).setUint16(2, headerSum, true);
  view(adjusted).setUint16(0, (view(adjusted).getUint16(0, true) + 2 * headerSum) & 0xffff, true);
  assert.deepEqual(adjusted, original);
  assert.deepEqual(original, saved);
  assert.deepEqual(transfer.subarray(2), original.subarray(2));
  assert.notDeepEqual(transfer, original);
  await assert.rejects(driver.writeArea('brec', transfer), /checksum/);
  const corrupt = original.slice(); corrupt[0x210] ^= 1;
  await assert.rejects(driver.writeArea('brec', corrupt), /checksum/);
  assert.equal(calls.length, 1);
  await driver.writeArea('mbrec', files['mbrec.bin']);
  assert.equal(calls[1].data, files['mbrec.bin']);
});

test('header-only read loads the checked flash driver before reading one sector', async () => {
  const calls = [], expected = files['lfi.bin'].subarray(0, profile.sector_size);
  const driver = new X6Driver({readLfiHeader: async () => {calls.push('header'); return expected;}}, profile, payload);
  driver.flashDriver = async () => calls.push('driver');
  assert.deepEqual(await driver.readLfiHeader(), expected);
  assert.deepEqual(calls, ['driver', 'header']);
  driver.usb.readLfiHeader = async () => expected.subarray(0, expected.length - 1);
  await assert.rejects(driver.readLfiHeader(), /Header.*nicht vollständig/);
});

test('BREC preparation rejects a wrong driver or invalid finalized header', async () => {
  const original = files['brec0.bin'].slice(), code = (await payload('fwsc.bin')).slice();
  code[0x4c2] ^= 1;
  await assert.rejects(prepareBrecTransfer(original, code, profile), /FWSC-Hash/);
  const changed = {...profile, runtime_sha256: {...profile.runtime_sha256, 'fwsc.bin': await sha256(code)}};
  await assert.rejects(prepareBrecTransfer(original, code, changed), /Prüfsummenroutine/);
  original[2] ^= 1;
  view(original).setUint16(0, (wordSum(original.subarray(2, 65536), 2) + 0x1234) & 0xffff, true);
  await assert.rejects(prepareBrecTransfer(original, await payload('fwsc.bin'), profile), /Headerprüfsumme/);
});

test('synthetic BREC checksum and size, MBREC copy addressing match native algorithm', async () => {
  const mbrec = files['mbrec.bin'].slice();
  const brec = files['brec0.bin'].slice();
  assert.equal(brecLength(brec.subarray(0, 65536), profile.brec_size), brec.length);
  assert.notEqual(bootLayout(mbrec, 0).row, bootLayout(mbrec, 1).row);
  const badHeader = brec.slice();
  view(badHeader).setUint16(0, view(brec).getUint16(0, true) ^ 0xffff, true);
  assert.throws(() => brecLength(badHeader.subarray(0, 65536), profile.brec_size), /checksum/);
  brec[50] ^= 1;
  assert.throws(() => brecLength(brec, profile.brec_size), /checksum/);
});

test('FWSC always loads before overlapping NANDINFO and rejects other NAND IDs', async () => {
  const calls = [], scan = new Uint8Array(0x9c), status = new Uint8Array(92);
  scan.set([0x48, 0x57]); scan.set([0x2c, 0x68, 4, 0x4a, 0xa9], 16);
  status.set(new TextEncoder().encode('NAFWSC'), 6);
  const usb = {ensureAlive() {}, writeRam: async (address, data) => calls.push([address, data]),
    execute: async address => address === 0x11e000 ? scan : status};
  const driver = new X6Driver(usb, profile, payload);
  driver.bootstrapped = true;
  await driver.flashDriver();
  assert.deepEqual(calls.map(call => call[0]), [0x11e000, 0x11e000, 0x125400]);
  assert.deepEqual(calls[1][1], await payload('fwsc.bin'));
  driver.mode = null; calls.length = 0; scan[16] = 0;
  await assert.rejects(driver.flashDriver(), /NAND-Kennung/);
  assert.equal(calls.length, 1);
});

test('RAM execution uses explicit entry address and verifies loaded ADFUS bytes', async () => {
  const calls = [], adfus = await payload('adfus.bin');
  let reads = 0;
  const usb = {info: async () => new Uint8Array([0, 67, 65, 68, 70, 85, 68, 48, 81]),
    writeRam: async address => calls.push(['write', address]), switchTo: async address => calls.push(['switch', address]),
    readRam: async () => reads++ ? adfus.subarray(0, 512) : new Uint8Array(512)};
  await new X6Driver(usb, profile, payload).bootstrap();
  assert.equal(usb.ramBlock, 512);
  assert.deepEqual(calls, [['write', 0x118000], ['switch', 0x118000]]);
  usb.readRam = async () => new Uint8Array(512);
  await assert.rejects(new X6Driver(usb, profile, payload).bootstrap(), /RAM-Prüfung/);
});

test('already loaded ADFUS is reused without the unsupported ROM INFO command', async () => {
  const adfus = await payload('adfus.bin');
  const usb = {readRam: async () => adfus.subarray(0, 512),
    info: async () => { throw new Error('ROM INFO must not be sent'); }};
  const driver = new X6Driver(usb, profile, payload);
  await driver.bootstrap();
  assert.equal(driver.bootstrapped, true);
});

test('boot reader returns exactly the actual indexed copy, including partial data on checksum failure', async () => {
  const mbrec = files['mbrec.bin'].slice();
  const brec = files['brec1.bin'].slice();
  const layout = bootLayout(mbrec, 1);
  let row = layout.row;
  const usb = {alive: true, ensureAlive() {}, execute: async () => {},
    writeRam: async (address, data) => { if (address === 0x100920) row = view(data).getUint32(0, true); },
    readRam: async (address, length) => {
      if (address === 0x11ffe8) return new Uint8Array([0x78, 0x56, 0x34, 0x12]);
      if (length === 1024) return mbrec;
      const offset = (row - layout.row) * layout.page;
      return brec.subarray(offset, offset + length);
    }};
  const driver = new X6Driver(usb, profile, payload);
  driver.bootstrapped = true;
  assert.deepEqual((await driver.readBoot(1))['brec1.bin'], brec);
  brec[30] ^= 1;
  await assert.rejects(driver.readBoot(1), error => {
    assert.deepEqual(error.partial['mbrec_copy.bin'], mbrec);
    assert.equal(error.partial['brec1.bin'].length, 65536);
    return /checksum/.test(error.message);
  });
});
