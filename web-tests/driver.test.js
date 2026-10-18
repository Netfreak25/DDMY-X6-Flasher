import { test } from 'node:test';
import assert from 'node:assert/strict';
import { X6Driver, bootLayout, brecLength } from '../lib/driver.js';
import { view } from '../lib/formats.js';
import { profile, files, payload } from './fixtures.js';

test('synthetic BREC checksum and size, MBREC copy addressing match native algorithm', async () => {
  const mbrec = files['mbrec.bin'].slice();
  const brec = files['brec0.bin'].slice();
  assert.equal(brecLength(brec.subarray(0, 65536), profile.brec_size), brec.length);
  assert.notEqual(bootLayout(mbrec, 0).row, bootLayout(mbrec, 1).row);
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
  const usb = {info: async () => new Uint8Array([0, 67, 65, 68, 70, 85, 68, 48, 81]),
    writeRam: async address => calls.push(['write', address]), switchTo: async address => calls.push(['switch', address]),
    readRam: async () => adfus.subarray(0, 512)};
  await new X6Driver(usb, profile, payload).bootstrap();
  assert.equal(usb.ramBlock, 512);
  assert.deepEqual(calls, [['write', 0x118000], ['switch', 0x118000]]);
  usb.readRam = async () => new Uint8Array(512);
  await assert.rejects(new X6Driver(usb, profile, payload).bootstrap(), /RAM-Prüfung/);
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
