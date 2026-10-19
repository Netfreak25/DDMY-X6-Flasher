import { test } from 'node:test';
import assert from 'node:assert/strict';
import { X6Operations, incompleteDumpMessage } from '../lib/workflows.js';
import { validateBundle, sha256 } from '../lib/formats.js';
import { packBackup, unpackBackup, importFolder, encodeManifest } from '../lib/archive.js';

import { profile, files, payload } from './fixtures.js';

function fixture() {
  const calls = [];
  const driver = {payload, flashDriver: async () => calls.push('driver'),
    readLfi: async () => {calls.push('read'); return files['lfi.bin'];},
    readLfiHeader: async () => {calls.push('header'); return files['lfi.bin'].slice(0, profile.sector_size);},
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

test('optional backup skips saving and full preread but retains header and full readback checks', async () => {
  const {operations, calls} = fixture();
  await operations.flash(files['lfi.bin'], async () => {throw Error('must not save');}, {backup: false});
  assert.deepEqual(calls, ['header', 'driver', 'lfi', 'final', 'read', 'reset']);
  for (const short of [true, false]) {
    const f = fixture();
    f.driver.readLfiHeader = async () => {
      const data = files['lfi.bin'].slice(0, profile.sector_size - Number(short));
      if (!short) data[profile.protocol.preserve_header_ranges[0][0]] ^= 1;
      return data;
    };
    await assert.rejects(f.operations.flash(files['lfi.bin'], null, {backup: false}), /Header/);
    assert.deepEqual(f.calls, []);
  }
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

test('optional backup skips saving and full preread but rejects short or foreign device headers', async () => {
  const {operations, calls} = fixture();
  await operations.flash(files['lfi.bin'], () => assert.fail('Backup disabled'), {backup: false});
  assert.deepEqual(calls, ['header', 'driver', 'lfi', 'final', 'read', 'reset']);
  for (const short of [true, false]) {
    const f = fixture();
    f.driver.readLfiHeader = async () => {
      const data = files['lfi.bin'].slice(0, profile.sector_size - Number(short));
      if (!short) data[profile.protocol.preserve_header_ranges[0][0]] ^= 1;
      return data;
    };
    await assert.rejects(f.operations.flash(files['lfi.bin'], null, {backup: false}), /Header/);
    assert.deepEqual(f.calls, []);
  }
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
  operations.profile = {...profile, boot_write_mapping_verified: false};
  await assert.rejects(operations.restore(files), /Notfallmodus/);
  assert.deepEqual(calls, []);
  driver.writeArea = async name => {calls.push(name); throw new Error('unconfirmed');};
  await assert.rejects(operations.restore(files, true), /unconfirmed/);
  assert.deepEqual(calls, ['driver', 'mbrec']);
});

test('invalid finalized BREC stops full restore before MBREC even with matching archive hashes', async () => {
  const {operations, calls} = fixture(), corrupt = {...files}, manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  for (const name of ['brec0.bin', 'brec1.bin']) {
    corrupt[name] = files[name].slice(); corrupt[name][0x200] ^= 1;
    manifest.files[name].sha256 = await sha256(corrupt[name]);
  }
  corrupt['manifest.json'] = encodeManifest(manifest);
  await assert.rejects(operations.restore(corrupt), /Prüfsumme/);
  assert.deepEqual(calls, []);
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
  const result = await f.operations.restore(files);
  assert.equal(result.bootVerified, true);
  assert.deepEqual(f.calls, ['driver', 'mbrec', 'brec', 'lfi', 'final', 'read', 'boot0', 'boot1', 'reset']);
  f = fixture();
  f.driver.readBoot = async () => {throw new Error('timeout');};
  assert.equal((await f.operations.restore(files, true)).bootVerified, false);
  assert.ok(!f.calls.includes('reset'));
  f = fixture();
  f.driver.readBoot = async () => {throw new Error('timeout');};
  await assert.rejects(f.operations.restore(files), /Boot-Rücklesung fehlgeschlagen/);
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

test('intentional partial backups read each selected boot pair once and export only requested components', async () => {
  const names = ['mbrec.bin', 'mbrec_copy.bin', 'brec0.bin', 'brec1.bin', 'lfi.bin'];
  for (const selected of [['mbrec.bin'], ['brec1.bin'], ['lfi.bin'], ['mbrec.bin', 'brec0.bin'], ['mbrec_copy.bin', 'brec1.bin'], ['mbrec.bin', 'brec1.bin', 'lfi.bin']]) {
    const f = fixture(), result = await f.operations.dump(selected);
    assert.equal(result.manifest.status, 'partial');
    assert.deepEqual(Object.keys(result.files).sort(), [...selected].sort());
    assert.equal(f.calls.filter(value => value === 'read').length, Number(selected.includes('lfi.bin')));
    assert.equal(f.calls.filter(value => value === 'boot0').length, Number(selected.some(name => ['mbrec.bin', 'brec0.bin'].includes(name))));
    assert.equal(f.calls.filter(value => value === 'boot1').length, Number(selected.some(name => ['mbrec_copy.bin', 'brec1.bin'].includes(name))));
    const packaged = unpackBackup(packBackup(result.files, result.manifest), profile);
    await validateBundle(packaged, profile, {allowPartial: true});
    await assert.rejects(validateBundle(packaged, profile), /unvollständig/);
    assert.deepEqual(result.manifest.requested_components, names.filter(name => selected.includes(name)));
  }
});

test('full restore backup is optional; cancelled save or failed reads stop before every write', async () => {
  const f = fixture();
  await f.operations.restore(files, false, {backup: true, saveBefore: async before => {
    await validateBundle({...before.files, 'manifest.json': encodeManifest(before.manifest)}, profile);
    f.calls.push('saved'); return true;
  }});
  assert.deepEqual(f.calls, ['read', 'boot0', 'boot1', 'saved', 'driver', 'mbrec', 'brec', 'lfi', 'final', 'read', 'boot0', 'boot1', 'reset']);
  for (const failed of ['save', 'read']) {
    const attempt = fixture();
    if (failed === 'read') attempt.driver.readLfi = async () => {throw Error('read failed');};
    await assert.rejects(attempt.operations.restore(files, false, {backup: true, saveBefore: async () => false}), /gespeichert|read failed/);
    assert.ok(!attempt.calls.some(value => ['driver', 'mbrec', 'brec', 'lfi', 'final', 'reset'].includes(value)));
  }
});

test('partial archive imports by content while incomplete archives remain rejected', async () => {
  const {selectImages} = await import('../lib/images.js');
  const f = fixture(), backup = await f.operations.dump(['mbrec_copy.bin', 'brec1.bin']);
  const packed = packBackup(backup.files, backup.manifest);
  const source = {name: 'parts.zip', size: packed.length, arrayBuffer: async () => packed.buffer};
  const selected = await selectImages([source], profile);
  assert.deepEqual(Object.keys(selected.components), ['mbrec', 'brec']);
  const damaged = packBackup(backup.files, {...backup.manifest, status: 'incomplete'});
  await assert.rejects(selectImages([{...source, size: damaged.length, arrayBuffer: async () => damaged.buffer}], profile), /unvollständig/);
});
