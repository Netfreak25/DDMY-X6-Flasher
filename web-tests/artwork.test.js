import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Artwork, encodeRgb565, decodeRgb565, locatePictures } from '../lib/artwork.js';
import { geometry, pictureInfo } from '../lib/picture.js';
import { packBackup } from '../lib/archive.js';
import { selectImages } from '../lib/images.js';
import { compareBytes, validateLfi, view } from '../lib/formats.js';
import { config, artworkFixture } from './artwork-fixture.js';

const vectors = JSON.parse(await readFile(new URL('../tests/artwork_vectors.json', import.meta.url)));
const red = new Uint8Array(config.width * config.height * 3);
for (let i = 0; i < red.length; i += 3) red[i] = 255;

test('shared RGB vectors detect LE/BE swapping and rounding', () => {
  for (const value of vectors) for (const [endian, key] of [['little', 'le'], ['big', 'be']]) {
    const encoded = encodeRgb565(new Uint8Array(value.rgb), endian);
    assert.deepEqual([...encoded], value[key]);
    assert.deepEqual([...decodeRgb565(encoded, endian)], value.decoded);
  }
});

test('14 fixed slots; no-op and reset retain all original bytes', async () => {
  const {profile, selection} = await artworkFixture(), editor = await Artwork.create(selection, profile, config);
  assert.equal(editor.ranges.size, 14);
  assert.deepEqual(editor.build(), selection.components);
  for (const id of editor.ranges.keys()) {
    if (editor.ranges.get(id).slot.fixed_black) {
      assert.throws(() => editor.replace(id, red), /Schwarz/);
      assert.throws(() => editor.reset(id), /Schwarz/);
      continue;
    }
    editor.replace(id, red); assert.deepEqual(editor.rgb(id), red); editor.reset(id); assert.equal(editor.edits.size, 0);
  }
  assert.deepEqual(editor.build(), selection.components);
});

test('coupled starts use different packing with equal decoded colors; only allowed bytes change', async () => {
  const {profile, selection} = await artworkFixture(), editor = await Artwork.create(selection, profile, config);
  editor.replace('startup', red);
  assert.deepEqual(editor.changedAreas, ['brec', 'lfi']);
  assert.deepEqual(editor.rgb('startup'), editor.rgb('boot-startup'));
  assert.deepEqual([...editor.pixels('startup').subarray(0, 2)], [0, 248]);
  assert.deepEqual([...editor.pixels('boot-startup').subarray(0, 2)], [248, 0]);
  const output = editor.build();
  assert.equal(validateLfi(output.lfi, profile).size, 148);
  for (const [area, original] of Object.entries(selection.components)) {
    assert.equal(output[area].length, original.length);
    const allowed = new Uint8Array(original.length);
    for (const id of editor.edits.keys()) {
      const item = editor.ranges.get(id);
      if (item.slot.area === area) {
        allowed.fill(1, item.start, item.end);
        if (item.entry) allowed.fill(1, item.entry.directoryOffset + 28, item.entry.directoryOffset + 32);
      }
    }
    if (area === 'lfi') { allowed.fill(1, 16, 20); allowed.fill(1, 510, 512); }
    for (let i = 0; i < original.length; i++) if (original[i] !== output[area][i]) assert.equal(allowed[i], 1, `${area}: ${i}`);
  }
});

test('modified components and full package reopen; MBREC untouched', async () => {
  const {profile, selection} = await artworkFixture(), editor = await Artwork.create(selection, profile, config);
  editor.replace('startup', red);
  const selected = await editor.changedSelection(), partial = await Artwork.create(selected, profile, config);
  assert.deepEqual(Object.keys(selected.components), ['brec', 'lfi']);
  assert.deepEqual(partial.rgb('startup'), red);
  const files = await editor.packageFiles(), archive = packBackup(files, JSON.parse(new TextDecoder().decode(files['manifest.json'])));
  const loaded = await selectImages([{name: 'edited.zip', size: archive.length, arrayBuffer: async () => archive.buffer}], profile);
  assert.deepEqual(loaded.components.mbrec, selection.components.mbrec);
  assert.equal(compareBytes(loaded.components.brec, selected.components.brec).different, 0);
  const reopened = await Artwork.create(loaded, profile, config);
  assert.deepEqual(reopened.rgb('boot-startup'), red);
});

test('missing components, uncoupling and invalid descriptor', async () => {
  const {profile, selection} = await artworkFixture(), editor = await Artwork.create(selection, profile, config);
  editor.coupled = false; editor.replace('boot-startup', red);
  assert.deepEqual(editor.changedAreas, ['brec']);
  const partial = await Artwork.create(await editor.changedSelection(), profile, config);
  assert.equal(partial.ranges.size, 1); assert.equal(partial.coupled, false);
  assert.throws(() => partial.replace('startup', red), /nicht geladen/);
  const corrupt = selection.components.brec.slice(); corrupt[corrupt.length - 512] ^= 1;
  assert.throws(() => locatePictures({brec: corrupt}, profile, config), /deskriptor/);
});

test('crop geometry and input limits/animation checked before browser decoding', () => {
  assert.deepEqual(geometry(480, 320, config), {x: -120, y: 0, width: 480, height: 320});
  assert.deepEqual(geometry(480, 320, config, {mode: 'contain'}), {x: 0, y: 80, width: 240, height: 160});
  assert.throws(() => geometry(10, 10, config, {zoom: NaN}), /Ungültig/);
  assert.throws(() => pictureInfo(new Uint8Array(5), {...config, max_input_bytes: 4}), /25 MiB/);
  const png = new Uint8Array(45), input = view(png);
  png.set([137, 80, 78, 71, 13, 10, 26, 10]); input.setUint32(8, 13); png.set(new TextEncoder().encode('IHDR'), 12);
  input.setUint32(16, 10000); input.setUint32(20, 10000); png.set(new TextEncoder().encode('IEND'), 37);
  assert.throws(() => pictureInfo(png, config), /40 Mega/);
  input.setUint32(16, 10); input.setUint32(20, 20);
  assert.deepEqual(pictureInfo(png, config), {width: 10, height: 20, type: 'image/png'});
  png.set(new TextEncoder().encode('acTL'), 37);
  assert.throws(() => pictureInfo(png, config), /Animierte/);
  assert.throws(() => pictureInfo(new Uint8Array(20), config), /gültiges/);
});

test('managed add/delete compaction retains reset originals and pending edits; export reopens', async () => {
  const {profile, selection} = await artworkFixture(undefined, true), editor = await Artwork.create(selection, profile, config);
  const original = editor.build(), capacity = editor.storage.capacity;
  assert.equal(editor.contour, true); assert.equal(editor.hasChanges, false);
  editor.replace('startup', red);
  const blue = new Uint8Array(red.length);
  for (let i = 2; i < blue.length; i += 3) blue[i] = 255;
  const first = editor.addBackground(red), second = editor.addBackground(blue);
  assert.deepEqual([first, second], ['background-12', 'background-13']);
  assert.equal(editor.storage.capacity, capacity - 2);
  const snapshot = editor.pixels(second, true).slice();
  editor.replace(second, red);
  assert.equal(editor.deleteBackground(first).get(second), first);
  assert.deepEqual(editor.pixels(first, true), snapshot);
  editor.reset(first); editor.reset('startup');
  assert.deepEqual(editor.pixels(first), snapshot);
  const reopened = await Artwork.create(await editor.changedSelection(), profile, config);
  assert.deepEqual(reopened.pixels(first), snapshot);
  assert.equal(reopened.storage.capacity, capacity - 1);
  editor.deleteBackground(first);
  assert.deepEqual(editor.build(), original);
  assert.equal(editor.hasChanges, false);
});

test('new-image treatment is idempotent and protected/invalid operations are atomic', async () => {
  const {profile, selection} = await artworkFixture(undefined, true), editor = await Artwork.create(selection, profile, config);
  const white = new Uint8Array(red.length).fill(255), id = editor.addBackground(white), expected = editor.prepareNew(white);
  assert.deepEqual(editor.pixels(id), expected);
  editor.replace(id, editor.rgb(id)); assert.deepEqual(editor.pixels(id), expected);
  const before = editor.build();
  for (const protectedId of ['background-01', 'background-02']) assert.throws(() => editor.deleteBackground(protectedId), /Standardbilder/);
  assert.throws(() => editor.addBackground(new Uint8Array(3)), /240/);
  assert.deepEqual(editor.build(), before);
  await assert.rejects(editor.applyScreensaver(), /deaktiviert/);
  const legacy = await artworkFixture(), old = await Artwork.create(legacy.selection, legacy.profile, config);
  assert.throws(() => old.addBackground(white), /Bilderkatalog/);
  assert.equal(old.hasChanges, false);
});
