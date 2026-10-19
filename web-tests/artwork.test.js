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
  for (const id of editor.ranges.keys()) { editor.replace(id, red); assert.deepEqual(editor.rgb(id), red); editor.reset(id); assert.equal(editor.edits.size, 0); }
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
