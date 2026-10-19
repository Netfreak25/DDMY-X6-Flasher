import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {config as catalog} from './artwork-fixture.js';
import {validateLfi, view, normalizeLfi} from '../lib/formats.js';
import {supported, catalogOffsets, configCountOffset, CATALOG} from '../lib/screensaver.js';
import {Artwork, locatePictures} from '../lib/artwork.js';
import {selectImages} from '../lib/images.js';
import {patchFixture, sums} from './screensaver-patch-fixture.js';
import {inspectPatch, applyPatch} from '../lib/screensaver-patch.js';

test('install and update are explicit, current is byte-identical, mixed code rejected', async () => {
  const {profile, config, versions} = await patchFixture();
  for (const [state, raw] of Object.entries(versions)) {
    assert.equal(await inspectPatch(raw, config, profile), state);
    const output = await applyPatch(raw, config, profile);
    assert.deepEqual(output, versions.current);
    assert.deepEqual(await applyPatch(output, config, profile), output);
  }
  const mixed = versions.install.slice(), entry = validateLfi(mixed, profile).get('T0000003.BIN');
  mixed.fill(34, entry.offset, entry.offset + entry.size); sums(mixed);
  assert.equal(await inspectPatch(mixed, config, profile), 'unsupported');
  await assert.rejects(applyPatch(mixed, config, profile));
});

test('invalid payload or background rejected without input mutation', async () => {
  const {profile, config, versions} = await patchFixture(), before = versions.install.slice();
  const invalid = JSON.parse(JSON.stringify(config)), blob = Object.keys(invalid.screensaver.patch.blobs)[0];
  invalid.screensaver.patch.blobs[blob] = 'deadbeef';
  await assert.rejects(applyPatch(versions.install, invalid, profile), /Patchdaten/);
  assert.deepEqual(versions.install, before);
  const common = validateLfi(before, profile).get('COMMON.STY');
  view(before).setUint32(common.offset + 1024 + 298 * 16, 0, true); sums(before);
  await assert.rejects(applyPatch(before, config, profile), /Hintergrundplatz/);
});

test('private real install/update output equals audited native candidate', {skip: !process.env.X6_FIRMWARE_FIXTURES}, async () => {
  const root = process.env.X6_FIRMWARE_FIXTURES, profile = JSON.parse(await readFile('tools/x6_profile.json'));
  const current = new Uint8Array(await readFile(`${root}/x6-loki-screensaver-v7-no-hello-final/lfi.bin`));
  for (const name of ['x6-loki-video-petrol', 'x6-loki-video-screensaver-final', 'x6-loki-video-screensaver-bankfix', 'x6-loki-video-screensaver-scanline']) {
    const raw = new Uint8Array(await readFile(`${root}/${name}/lfi.bin`));
    assert.deepEqual(await applyPatch(raw, catalog, profile), current);
  }
  assert.deepEqual(await applyPatch(current, catalog, profile), current);
  const hello = new Uint8Array(await readFile(`${root}/x6-loki-screensaver-v7-hello-padded/lfi.bin`));
  const previous = normalizeLfi(new Uint8Array(await readFile(`${root}/x6-loki-screensaver-hello/lfi.bin`)), profile);
  assert.deepEqual(await applyPatch(previous, catalog, profile), hello);
  assert.deepEqual(await applyPatch(hello, catalog, profile), hello);
  assert.equal(await supported(hello, validateLfi(hello, profile), catalog.screensaver), true);
});

test('v7 recognition follows code with variable catalog counts and repairs the native count mirror', {skip: !process.env.X6_FIRMWARE_FIXTURES}, async () => {
  const root = process.env.X6_FIRMWARE_FIXTURES, profile = JSON.parse(await readFile('tools/x6_profile.json'));
  const current = new Uint8Array(await readFile(`${root}/x6-loki-screensaver-v7-no-hello-final/lfi.bin`));
  const entries = validateLfi(current, profile), common = entries.get('COMMON.STY'), native = entries.get('CONFIG.BIN');
  const style = current.subarray(common.offset, common.offset + common.size), offsets = catalogOffsets(style);
  const countAt = native.offset + configCountOffset(current.subarray(native.offset, native.offset + native.size));
  const blackPixels = view(style).getUint32(catalog.sty.table_offset + 298 * catalog.sty.record_size, true);
  for (const selected of [[0], offsets, [...offsets, blackPixels]]) {
    const data = current.slice(), input = view(data);
    input.setUint16(common.offset + CATALOG.offset + 6, selected.length, true);
    selected.forEach((offset, i) => input.setUint32(common.offset + CATALOG.entries + i * 4, offset, true));
    input.setUint16(countAt, selected.length, true);
    if (selected.length > 1) input.setUint16(common.offset + selected[1], 0xffff, true);
    sums(data);
    assert.equal(await inspectPatch(data, catalog, profile), 'current');
    assert.deepEqual(await applyPatch(data, catalog, profile), data);
  }
  const wrongCount = current.slice(); view(wrongCount).setUint16(countAt, 1, true); sums(wrongCount);
  assert.equal(await inspectPatch(wrongCount, catalog, profile), 'update');
  assert.deepEqual(await applyPatch(wrongCount, catalog, profile), current);
  const badOffset = current.slice(); view(badOffset).setUint32(common.offset + CATALOG.entries + 4, common.size, true); sums(badOffset);
  assert.equal(await inspectPatch(badOffset, catalog, profile), 'unsupported');
  assert.equal(locatePictures({lfi: current}, profile, catalog).get('background-01').slot.fixed_black, true);
});

test('repaired import can be exported without changing images or opting into the screensaver', async () => {
  const {profile, config, versions} = await patchFixture(), raw = versions.install.slice();
  const last = [...validateLfi(raw, profile).values()].at(-1);
  view(raw).setUint32(last.directoryOffset + 20, 512, true); sums(raw);
  const selection = await selectImages([{name: 'gap.bin', size: raw.length, arrayBuffer: async () => raw}], profile);
  const model = await Artwork.create(selection, profile, config);
  assert.equal(model.hasChanges, true);
  assert.deepEqual(model.changedAreas, ['lfi']);
  assert.equal(model.patchedLfi, null);
  assert.equal(model.patchState, 'install');
  assert.deepEqual((await model.changedSelection()).components.lfi, versions.install);
  assert.deepEqual(selection.originals[0].data, raw);
});
