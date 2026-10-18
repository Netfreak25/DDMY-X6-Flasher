import { test } from 'node:test';
import assert from 'node:assert/strict';
import { X6Operations, incompleteDumpMessage } from '../lib/workflows.js';
import { validateBundle } from '../lib/formats.js';
import { packBackup, unpackBackup, importFolder } from '../lib/archive.js';

import { profile, files } from './fixtures.js';

function fixture() {
  const calls = [];
  const driver = {flashDriver: async () => calls.push('driver'),
    readLfi: async () => {calls.push('read'); return files['lfi.bin'];},
    writeArea: async name => calls.push(name), finalize: async () => calls.push('final'),
    readBoot: async index => {calls.push(`boot${index}`); return index === 0 ?
      {'mbrec.bin': files['mbrec.bin'], 'brec0.bin': files['brec0.bin']} :
      {'mbrec_copy.bin': files['mbrec_copy.bin'], 'brec1.bin': files['brec1.bin']};}};
  const operations = new X6Operations(driver, profile, {restart: async () => {calls.push('reset'); return true;}});
  return {driver, operations, calls};
}

test('flash saves first, writes once, finalizes, verifies the whole LFI and then resets', async () => {
  const {operations, calls} = fixture();
  const result = await operations.flash(files['lfi.bin'], async () => {calls.push('saved'); return true;});
  assert.equal(result.lfiVerified, true);
  assert.deepEqual(calls, ['read', 'saved', 'driver', 'lfi', 'final', 'read', 'reset']);
});

test('cancelled save and uncertain write cannot cause any subsequent write/finalize/reset', async () => {
  let f = fixture();
  await assert.rejects(f.operations.flash(files['lfi.bin'], async () => false), /gespeichert/);
  assert.deepEqual(f.calls, ['read']);
  f = fixture();
  f.driver.writeArea = async name => {f.calls.push(name); throw new Error('unconfirmed');};
  await assert.rejects(f.operations.flash(files['lfi.bin'], async () => true), /unconfirmed/);
  assert.deepEqual(f.calls, ['read', 'driver', 'lfi']);
});

test('incomplete boot read retains the good second copy and produces an incomplete portable ZIP', async () => {
  const {driver, operations, calls} = fixture();
  const original = driver.readBoot;
  driver.readBoot = async index => {if (index === 0) throw new Error('bad brec checksum'); return original(index);};
  const result = await operations.dump();
  assert.equal(result.manifest.status, 'incomplete');
  assert.ok(result.files['brec1.bin']);
  assert.equal(result.files['brec0.bin'], undefined);
  const message = incompleteDumpMessage(result.manifest);
  assert.match(message, /Firmware: gültig/);
  assert.match(message, /Bootkopie 1: bad brec checksum/);
  assert.match(message, /Bootkopie 2: gültig/);
  assert.match(message, /nicht für Full Restore/);
  assert.ok(!calls.includes('reset'));
  const restored = unpackBackup(packBackup(result.files, result.manifest), profile);
  await assert.rejects(validateBundle(restored, profile), /unvollständig/);
  assert.deepEqual(restored['brec1.bin'], files['brec1.bin']);
});

test('backup-wide validation errors and missing steps remain visible in the result', () => {
  assert.match(incompleteDumpMessage({steps: {}, error: 'BREC-Kopien unterscheiden sich'}), /BREC-Kopien unterscheiden sich/);
  assert.match(incompleteDumpMessage({}), /Firmware: nicht vollständig gelesen/);
});

test('normal restore gate and uncertain MBREC write stop before BREC/LFI', async () => {
  const {driver, operations, calls} = fixture();
  await assert.rejects(operations.restore(files), /Notfallmodus/);
  assert.deepEqual(calls, []);
  driver.writeArea = async name => {calls.push(name); throw new Error('unconfirmed');};
  await assert.rejects(operations.restore(files, true), /unconfirmed/);
  assert.deepEqual(calls, ['driver', 'mbrec']);
});

test('interrupted firmware dump saves the confirmed prefix as incomplete without invented bytes', async () => {
  const {driver, operations} = fixture();
  const prefix = files['lfi.bin'].slice(0, profile.transfer_size);
  driver.readLfi = async () => {throw Object.assign(new Error('disconnected'), {partialLfi: prefix});};
  driver.readBoot = async () => {throw new Error('session closed');};
  const result = await operations.dump();
  assert.equal(result.manifest.status, 'incomplete');
  assert.equal(result.manifest.steps.lfi.status, 'failed');
  const restored = unpackBackup(packBackup(result.files, result.manifest), profile);
  assert.deepEqual(restored['lfi.bin'], prefix);
  assert.equal(result.manifest.files['lfi.bin'].size, prefix.length);
  await assert.rejects(validateBundle(restored, profile), /unvollständig/);
});

test('full restore only reports boot verification after both copies match; unreadable boot does not reset', async () => {
  let f = fixture();
  const result = await f.operations.restore(files, true);
  assert.equal(result.bootVerified, true);
  assert.deepEqual(f.calls, ['driver', 'mbrec', 'brec', 'lfi', 'final', 'read', 'boot0', 'boot1', 'reset']);
  f = fixture();
  f.driver.readBoot = async () => {throw new Error('timeout');};
  assert.equal((await f.operations.restore(files, true)).bootVerified, false);
  assert.ok(!f.calls.includes('reset'));
});

test('byte mismatch after write and concurrent actions cannot report success', async () => {
  const {driver, operations, calls} = fixture();
  let reads = 0;
  driver.readLfi = async () => { const value = files['lfi.bin'].slice(); if (reads++) value[value.length - 1] ^= 1; return value; };
  await assert.rejects(operations.flash(files['lfi.bin'], async () => true), /Rückleseprüfung/);
  assert.ok(!calls.includes('reset'));
  operations.busy = true;
  await assert.rejects(operations.verify(files['lfi.bin']), /anderer Vorgang/);
});

test('complete archives and legacy folder imports validate with the unchanged manifest', async () => {
  const manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  const unpacked = unpackBackup(packBackup(files, manifest), profile);
  assert.equal((await validateBundle(unpacked, profile)).status, 'complete');
  const folder = await importFolder(Object.entries(files).map(([name, data]) =>
    ({name, size: data.length, arrayBuffer: async () => data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)})), profile);
  assert.equal((await validateBundle(folder, profile)).status, 'complete');
});
