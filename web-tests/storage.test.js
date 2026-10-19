import test from 'node:test';
import assert from 'node:assert/strict';
import {STORAGE, inspectStorage, removeDictionaries, repack, addBackground, deleteBackground} from '../lib/storage.js';
import {validateLfi, view, wordSum} from '../lib/formats.js';
import {storageFixture, pixels} from './storage-fixture.js';

const {image, profile} = storageFixture(), freed = removeDictionaries(image, profile);
const resources = data => new Map([...validateLfi(data, profile)].map(([name, entry]) => [name, data.subarray(entry.offset, entry.offset + entry.size)]));

test('explicit dictionary removal preserves other files, including zero-filled executable allocation', () => {
  const before = resources(image), after = resources(freed), report = inspectStorage(freed, profile);
  assert.equal(inspectStorage(image, profile).free_bytes, 0);
  assert.deepEqual([report.free_bytes, report.capacity, report.count], [308736, 2, 11]);
  assert.equal(after.get('USER4.AP').length, 512);
  assert.ok(STORAGE.dictionaries.every(name => !after.has(name)));
  for (const [name, raw] of before) if (after.has(name)) assert.deepEqual(after.get(name), raw);
  assert.deepEqual(removeDictionaries(freed, profile), freed);
  assert.deepEqual(image, storageFixture().image);
});

test('add/delete compacts only extras, preserves standard pixel bytes, and returns every byte on roundtrip', () => {
  const first = addBackground(freed, pixels(4660), profile), both = addBackground(first, pixels(48879), profile);
  const report = inspectStorage(both, profile);
  assert.equal(report.count, 13); assert.equal(report.capacity, 0);
  assert.throws(() => addBackground(both, pixels(1), profile), /Kein Platz/);
  const removed = deleteBackground(both, 11, profile), extra = inspectStorage(removed, profile).backgrounds.at(-1);
  assert.deepEqual(removed.subarray(extra.absolute_offset, extra.absolute_offset + extra.size), pixels(48879));
  assert.deepEqual(deleteBackground(removed, 11, profile), freed);
  assert.deepEqual(deleteBackground(first, 11, profile), freed);
});

test('black and all standard positions are protected and invalid edits are atomic', () => {
  for (const index of [-1, 0, 1, 10, 11, 255, 11.5]) assert.throws(() => deleteBackground(freed, index, profile), /Standardbilder/);
  assert.throws(() => addBackground(freed, new Uint8Array(2), profile), /RGB565/);
  assert.throws(() => repack(freed, {'USER2.AP': new Uint8Array(freed.length)}, [], profile), /Nicht genügend/);
  assert.deepEqual(freed, removeDictionaries(image, profile));
});

test('missing and corrupt catalogs do not trigger an implicit code patch or allocation repair', () => {
  const common = resources(freed).get('COMMON.STY').slice(); common.fill(0, 64, 1024);
  const legacy = repack(freed, {'COMMON.STY': common}, [], profile);
  assert.equal(inspectStorage(legacy, profile).supported, false);
  assert.throws(() => addBackground(legacy, pixels(1), profile), /Kein unterstützter/);
  const mutations = [raw => view(raw).setUint32(84, 0, true), raw => view(raw).setUint32(72, raw.length - 512, true),
    raw => raw[1000] = 1, raw => {view(raw).setUint32(1024, raw.length, true); view(raw).setUint32(1028, 16, true);},
    raw => view(raw).setUint16(70, 237, true)];
  for (const mutate of mutations) {
    const bad = resources(freed).get('COMMON.STY').slice(); mutate(bad);
    assert.throws(() => inspectStorage(repack(freed, {'COMMON.STY': bad}, [], profile), profile));
  }
  const config = resources(freed).get('CONFIG.BIN').slice(); view(config).setUint16(128, 12, true);
  assert.throws(() => inspectStorage(repack(freed, {'CONFIG.BIN': config}, [], profile), profile), /Bildanzahl/);
});

test('named reserve with nonzero data is never silently overwritten', () => {
  const bad = freed.slice(), entry = validateLfi(bad, profile).get('X6FREE.BIN'), header = view(bad);
  bad[entry.offset] = 1; header.setUint32(entry.directoryOffset + 28, 1, true);
  header.setUint32(16, wordSum(bad.subarray(512, 8192), 4), true); header.setUint16(510, wordSum(bad.subarray(0, 510), 2), true);
  assert.throws(() => inspectStorage(bad, profile), /enthält Daten/);
});

test('exhausted reserve disappears instead of becoming an invalid zero-length entry', () => {
  const old = resources(freed).get('USER2.AP'), larger = new Uint8Array(old.length + 308736); larger.set(old);
  const full = repack(freed, {'USER2.AP': larger}, [], profile);
  assert.equal(validateLfi(full, profile).has('X6FREE.BIN'), false);
  assert.equal(inspectStorage(full, profile).free_bytes, 0);
});
