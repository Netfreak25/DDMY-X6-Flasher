import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {inspectStorage, addBackground, deleteBackground, repack} from '../lib/storage.js';
import {locatePictures} from '../lib/artwork.js';
import {validateLfi, view} from '../lib/formats.js';
import {skinFixture, pixels} from './storage-fixture.js';

const {image, profile} = skinFixture();
const common = data => {
  const entry = validateLfi(data, profile).get('COMMON.STY');
  return data.subarray(entry.offset, entry.offset + entry.size);
};

test('512 role aliases preserve exact pixels through wallpaper edits; 513 is refused', () => {
  const fixture = skinFixture(512), report = inspectStorage(fixture.image, fixture.profile);
  assert.equal(report.skin.role_count, 512);
  assert.deepEqual(report.skin.skin_ids, [309]);
  const edited = addBackground(fixture.image, pixels(0x5476), fixture.profile);
  assert.deepEqual(deleteBackground(edited, 11, fixture.profile), fixture.image);
  const invalid = skinFixture(513);
  assert.throws(() => inspectStorage(invalid.image, invalid.profile));
});

test('v2 add/delete preserves skin map, all immutable pixels and relocated table', () => {
  const before = inspectStorage(image, profile), original = common(image), changed = addBackground(image, pixels(0x5476), profile);
  const after = inspectStorage(changed, profile), prefix = common(changed).slice(0, before.prefix_bytes);
  assert.equal(before.catalog_version, 2); assert.deepEqual(before.skin.skin_ids, [309]);
  prefix.set(original.subarray(64, 1024), 64);
  assert.deepEqual(prefix, original);
  const extra = after.backgrounds.at(-1);
  assert.deepEqual(changed.subarray(extra.absolute_offset, extra.absolute_offset + extra.size), pixels(0x5476));
  assert.deepEqual(deleteBackground(changed, 11, profile), image);
});

test('v2 unknown version, bad prefix, map and wallpaper aliases are refused', () => {
  const report = inspectStorage(image, profile), ext = report.skin.extension_offset;
  const mutations = [raw => view(raw).setUint16(68, 3, true), raw => view(raw).setUint32(76, 64, true),
    raw => view(raw).setUint32(ext + 8, report.prefix_bytes - 512, true), raw => view(raw).setUint16(ext + 12, 7, true),
    raw => view(raw).setUint32(ext + 16, ext, true), raw => view(raw).setUint16(ext + 32, 298, true)];
  for (const mutate of mutations) {
    const raw = common(image).slice(); mutate(raw);
    assert.throws(() => inspectStorage(repack(image, {'COMMON.STY': raw}, [], profile), profile));
  }
});

test('artwork editor locates original and extra slots through native relocated table pointer', async () => {
  const config = JSON.parse(await readFile(new URL('../tools/x6_artwork.json', import.meta.url)));
  config.slots = config.slots.filter(slot => slot.file === 'COMMON.STY');
  const changed = addBackground(image, pixels(0x5476), profile), slots = locatePictures({lfi: changed}, profile, config);
  assert.equal(slots.size, 12);
  assert.equal(slots.get('background-01').slot.fixed_black, true);
  const extra = slots.get('background-12');
  assert.deepEqual(changed.subarray(extra.start, extra.end), pixels(0x5476));
});
