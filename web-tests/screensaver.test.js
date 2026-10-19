import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { processBackground, limits, limitPixel, supported, REVISION } from '../lib/screensaver.js';
import { sha256, view } from '../lib/formats.js';
import { Artwork } from '../lib/artwork.js';
import { config, artworkFixture } from './artwork-fixture.js';

const vectors = JSON.parse(await readFile(new URL('../tests/screensaver_vectors.json', import.meta.url)));
const profile = config.screensaver;

test('shared Python/native vectors, all slots, black and idempotence', async () => {
  assert.equal(vectors.revision, profile.revision);
  for (const c of vectors.pixels) assert.equal(limitPixel(c.word, c.ceiling), c.result);
  const raw = new Uint8Array(240 * 320 * 2), words = view(raw);
  for (let i = 0; i < raw.length / 2; i++) words.setUint16(i * 2, (i * 40503 + 97) & 65535, true);
  for (const c of vectors.backgrounds) {
    assert.deepEqual(limits(profile, c.slot), c.limits);
    const output = processBackground(raw, profile, c.slot);
    assert.equal(await sha256(output), c.sha256);
    assert.deepEqual(processBackground(output, profile, c.slot), output);
  }
  assert.deepEqual(processBackground(new Uint8Array(raw.length), profile, 1), new Uint8Array(raw.length));
});

test('recognition is independent of images and rejects unknown code', async () => {
  const data = new TextEncoder().encode('codepicture');
  const policy = {revision: REVISION, supported_code: {'MAINMENU.AP': await sha256(data.subarray(0, 4))}};
  const entries = new Map([['MAINMENU.AP', {offset: 0, size: 4}]]);
  assert.equal(await supported(data, entries, policy), true);
  data[5] ^= 255; assert.equal(await supported(data, entries, policy), true);
  data[0] ^= 255; assert.equal(await supported(data, entries, policy), false);
  assert.equal(await supported(data, new Map(), policy), false);
});

test('supported preview/apply/reset/export with untouched legacy and boot pictures', async () => {
  const {selection, profile: device} = await artworkFixture();
  const model = await Artwork.create(selection, device, config);
  assert.equal(model.contour, false);
  assert.deepEqual(model.build(), selection.components);
  const rgb = new Uint8Array(240 * 320 * 3).fill(255);
  model.replace('background-01', rgb); assert.deepEqual(model.rgb('background-01'), rgb);
  model.reset('background-01'); model.contour = true;
  for (const id of ['background-01', 'background-02', 'background-11']) {
    const preview = model.previewRgb(id, rgb);
    model.replace(id, rgb); assert.deepEqual(model.rgb(id), preview);
    const bytes = model.pixels(id).slice(); model.replace(id, model.rgb(id));
    assert.deepEqual(model.pixels(id), bytes);
  }
  for (const id of ['startup', 'boot-startup', 'shutdown']) assert.deepEqual(model.previewRgb(id, rgb), rgb);
  model.reset('background-01'); assert.equal(model.edits.has('background-01'), false);
  const reopened = await Artwork.create(await model.changedSelection(), device, config);
  assert.deepEqual(reopened.pixels('background-02'), model.pixels('background-02'));
});
