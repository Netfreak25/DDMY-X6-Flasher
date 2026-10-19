import {test} from 'node:test';
import assert from 'node:assert/strict';
import {files, profile, payload} from './fixtures.js';
import {AREA_FILES, selectImages, identify} from '../lib/images.js';
import {packBackup} from '../lib/archive.js';
import {prepareBrecTransfer} from '../lib/driver.js';
import {view, validateLfi, wordSum, compareBytes} from '../lib/formats.js';
import {X6Operations} from '../lib/workflows.js';

const source = (data, name = 'unrelated-name.bin') => ({name, size: data.length, arrayBuffer: async () => data.slice().buffer});
const inputs = Object.fromEntries(Object.entries(AREA_FILES).map(([area, names]) => {
  const data = files[names[0]].slice();
  data[area === 'mbrec' ? 0x100 : area === 'brec' ? 0x11000 : data.length - 1] ^= 1;
  if (area === 'lfi') {
    const entry = [...validateLfi(files[names[0]], profile).values()].at(-1), header = view(data);
    header.setUint32(entry.directoryOffset + 28, wordSum(data.subarray(entry.offset, entry.offset + entry.size), 4), true);
    header.setUint32(16, wordSum(data.subarray(512, 8192), 4), true);
    header.setUint16(510, wordSum(data.subarray(0, 510), 2), true);
  }
  return [area, data];
}));

function fixture() {
  const calls = [], current = {...files};
  const driver = {payload, flashDriver: async () => calls.push('driver'),
    readLfi: async () => {calls.push('read-lfi'); return current['lfi.bin'];},
    readLfiHeader: async () => {calls.push('read-header'); return current['lfi.bin'].subarray(0, profile.sector_size);},
    readBoot: async index => {
      calls.push(`read-boot${index}`);
      const names = index ? ['mbrec_copy.bin', 'brec1.bin'] : ['mbrec.bin', 'brec0.bin'];
      return Object.fromEntries(names.map(name => [name, current[name]]));
    },
    writeArea: async (area, data) => {
      calls.push(`write-${area}`);
      if (area === 'brec') {
        data = await prepareBrecTransfer(data, await payload('fwsc.bin'), profile);
        view(data).setUint16(0, (view(data).getUint16(0, true) + 2 * view(data).getUint16(2, true)) & 0xffff, true);
      }
      for (const name of AREA_FILES[area]) current[name] = data;
    }, finalize: async () => calls.push('final')};
  const operations = new X6Operations(driver, profile, {restart: async () => {calls.push('reset'); return true;}});
  return {calls, current, driver, operations};
}

test('content detection accepts all seven combinations and writes only those areas in order', async () => {
  for (let mask = 1; mask < 8; mask++) {
    const f = fixture(), areas = Object.keys(AREA_FILES).filter((_, index) => mask & (1 << index));
    const selected = await selectImages(areas.toReversed().map(area => source(inputs[area])), profile);
    const result = await f.operations.flashSelection(selected, async before => {
      f.calls.push('saved');
      if (areas.some(area => area !== 'lfi')) {
        assert.equal(before.manifest.status, 'complete');
        for (const names of Object.values(AREA_FILES)) for (const name of names) assert.deepEqual(before.files[name], files[name]);
      } else assert.deepEqual(before, files['lfi.bin']);
      return true;
    });
    assert.equal(result.normalMode, true);
    assert.deepEqual(f.calls.filter(call => call.startsWith('write-')), areas.map(area => `write-${area}`));
    assert.equal(f.calls[f.calls.indexOf(`write-${areas[0]}`) - 1], 'driver');
    assert.equal(f.calls.filter(call => call === 'final').length, 1);
    assert.equal(f.calls.at(-1), 'reset');
    for (const [area, names] of Object.entries(AREA_FILES)) for (const name of names) {
      assert.deepEqual(f.current[name], areas.includes(area) ? inputs[area] : files[name]);
    }
  }
});

test('optional backup skips full preread and saving for all seven combinations, preserving verification', async () => {
  for (let mask = 1; mask < 8; mask++) {
    const f = fixture(), areas = Object.keys(AREA_FILES).filter((_, index) => mask & (1 << index));
    const selected = await selectImages(areas.map(area => source(inputs[area])), profile);
    const result = await f.operations.flashSelection(selected, () => assert.fail('Backup was disabled'), {backup: false});
    assert.equal(result.normalMode, true);
    assert.deepEqual(f.calls.filter(call => call.startsWith('write-')), areas.map(area => `write-${area}`));
    const beforeWrite = f.calls.slice(0, f.calls.indexOf(`write-${areas[0]}`));
    assert.ok(!beforeWrite.includes('read-lfi'));
    assert.equal(f.calls.filter(call => call === 'read-header').length, Number(areas.includes('lfi')));
    assert.equal(f.calls.filter(call => call === 'read-lfi').length, Number(areas.includes('lfi')));
    assert.equal(f.calls.at(-1), 'reset');
    for (const [area, names] of Object.entries(AREA_FILES)) for (const name of names) {
      assert.deepEqual(f.current[name], areas.includes(area) ? inputs[area] : files[name]);
    }
  }
});

test('identical duplicate copies and complete ZIPs merge; conflicting copies and damaged files stop offline', async () => {
  const selected = await selectImages([source(files['brec0.bin'], 'lfi.bin'), source(files['brec1.bin'])], profile);
  assert.deepEqual(Object.keys(selected.components), ['brec']);
  await assert.rejects(selectImages([source(inputs.brec), source(files['brec0.bin'])], profile), /Widersprüchliche BREC/);
  const manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  const zipped = await selectImages([source(packBackup(files, manifest), 'arbitrary.bin')], profile);
  assert.deepEqual(Object.keys(zipped.components), ['mbrec', 'brec', 'lfi']);
  for (const [area, offset] of [['mbrec', 0x0e], ['brec', 0x200], ['lfi', 0x2000]]) {
    const data = inputs[area].slice(); data[offset] ^= 1;
    assert.throws(() => identify(data, profile));
  }
  const prepared = await prepareBrecTransfer(files['brec0.bin'], await payload('fwsc.bin'), profile);
  assert.throws(() => identify(prepared, profile), /Prüfsumme/);
});

test('optional backup skips full pre-read and save for all seven selections while retaining verification', async () => {
  for (let mask = 1; mask < 8; mask++) {
    const f = fixture(), areas = Object.keys(AREA_FILES).filter((_, index) => mask & (1 << index)), events = [];
    f.operations.log = event => events.push(event);
    const selected = await selectImages(areas.map(area => source(inputs[area])), profile);
    const result = await f.operations.flashSelection(selected, () => assert.fail('Backup disabled'), {backup: false});
    assert.equal(result.normalMode, true);
    const writeIndex = f.calls.findIndex(call => call.startsWith('write-'));
    assert.ok(!f.calls.slice(0, writeIndex).includes('read-lfi'));
    assert.equal(f.calls.filter(call => call === 'read-lfi').length, Number(areas.includes('lfi')));
    assert.equal(f.calls.filter(call => call === 'read-header').length, Number(areas.includes('lfi')));
    assert.equal(events.filter(event => event.event === 'backup_skipped').length, 1);
    assert.equal(f.calls.at(-1), 'reset');
  }
});

test('without backup, header mismatch or uncertain write still stops without retry or restart', async () => {
  const selected = await selectImages([source(inputs.lfi)], profile);
  let f = fixture();
  f.driver.readLfiHeader = async () => {const header = files['lfi.bin'].slice(0, profile.sector_size); header[40] ^= 1; return header;};
  await assert.rejects(f.operations.flashSelection(selected, null, {backup: false}), /Gerätespezifischer/);
  assert.deepEqual(f.calls, []);
  f = fixture();
  f.driver.writeArea = async area => {f.calls.push(`write-${area}`); throw new Error('unconfirmed');};
  await assert.rejects(f.operations.flashSelection(selected, null, {backup: false}), /unconfirmed/);
  assert.deepEqual(f.calls, ['read-header', 'driver', 'write-lfi']);
});

test('without backup, all boot records and a complete LFI header are checked before writing', async () => {
  const selected = await selectImages(Object.values(inputs).map(data => source(data)), profile);
  for (const name of ['mbrec.bin', 'mbrec_copy.bin', 'brec0.bin', 'brec1.bin']) {
    const f = fixture();
    f.current[name] = f.current[name].slice(0, -1);
    await assert.rejects(f.operations.flashSelection(selected, null, {backup: false}), /Falsche Größe/);
    assert.ok(!f.calls.some(call => call.startsWith('write-')));
  }
  const f = fixture();
  f.driver.readLfiHeader = async () => files['lfi.bin'].slice(0, profile.sector_size - 1);
  await assert.rejects(f.operations.flashSelection(selected, null, {backup: false}), /Header.*nicht vollständig/);
  assert.deepEqual(f.calls, []);
});

test('uncertain MBREC, BREC or LFI acknowledgement stops without finalization, retry or reset', async () => {
  const selected = await selectImages(Object.values(inputs).map(data => source(data)), profile);
  for (const area of Object.keys(AREA_FILES)) {
    const f = fixture(), write = f.driver.writeArea;
    f.driver.writeArea = async (...args) => {await write(...args); if (args[0] === area) throw new Error('unconfirmed');};
    await assert.rejects(f.operations.flashSelection(selected, async () => true), /unconfirmed/);
    assert.equal(f.calls.at(-1), `write-${area}`);
    assert.equal(f.calls.filter(call => call === `write-${area}`).length, 1);
  }
});

test('save failure and altered selection stop before any flash command', async () => {
  const selected = await selectImages([source(inputs.brec)], profile), f = fixture();
  await assert.rejects(f.operations.flashSelection(selected, async () => false), /gespeichert/);
  assert.deepEqual(f.calls, ['read-lfi', 'read-boot0', 'read-boot1']);
  f.calls.length = 0;
  selected.components.brec[0x11001] ^= 1;
  await assert.rejects(f.operations.flashSelection(selected, async () => true), /erneut auswählen/);
  assert.deepEqual(f.calls, []);
});

test('partial BREC write checks both copies and preserves unselected MBREC', async () => {
  const selected = await selectImages([source(inputs.brec)], profile);
  for (const name of ['mbrec_copy.bin', 'brec1.bin']) {
    const f = fixture();
    f.driver.finalize = async () => {f.calls.push('final'); f.current[name] = f.current[name].slice(); f.current[name][0x100] ^= 1;};
    await assert.rejects(f.operations.flashSelection(selected, async () => true), /Rückleseprüfung/);
    assert.ok(!f.calls.includes('reset'));
  }
});

test('component comparison counts both boot copies and never writes', async () => {
  const selected = await selectImages(Object.values(inputs).map(data => source(data)), profile), f = fixture();
  const result = await f.operations.verifySelection(selected);
  assert.equal(result.identical, false);
  const expected = Object.entries(AREA_FILES).reduce((sum, [area, names]) => sum + names.reduce((count, name) => count + compareBytes(inputs[area], files[name]).different, 0), 0);
  assert.equal(result.different, expected);
  assert.deepEqual(f.calls, ['read-lfi', 'read-boot0', 'read-boot1']);
});
