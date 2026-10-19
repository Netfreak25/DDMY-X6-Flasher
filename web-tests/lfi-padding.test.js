import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Buffer} from 'node:buffer';
import {normalizeLfi, validateLfi, normalizeBundle, validateBundle, sha256} from '../lib/formats.js';
import {selectImages} from '../lib/images.js';
import {makeImage} from './lfi-padding-fixture.js';
import {files, profile} from './fixtures.js';
import {view, wordSum} from '../lib/formats.js';

const vectors = JSON.parse(await readFile(new URL('../tests/lfi_padding_vectors.json', import.meta.url)));

test('shared padding vectors retain every named payload and normalize idempotently', () => {
  for (const vector of vectors) {
    const source = makeImage(vector), device = {lfi_size: source.length, sector_size: 512};
    if (vector.error) { assert.throws(() => normalizeLfi(source, device), undefined, vector.name); continue; }
    const saved = source.slice(), changes = [], output = normalizeLfi(source, device, changes);
    assert.deepEqual(source, saved, vector.name);
    const entries = validateLfi(output, device);
    assert.deepEqual([...entries.values()].map(entry => entry.size), vector.lengths, vector.name);
    assert.deepEqual(normalizeLfi(output, device), output, vector.name);
    assert.equal(8192 + [...entries.values()].reduce((sum, entry) => sum + Math.floor(entry.size / 512) * 512, 0), output.length);
    for (const [name, offset, size] of vector.entries) {
      const expected = source.slice(offset, offset + size);
      if (vector.catalog && name === 'COMMON.STY') {
        view(expected).setUint32(0x48, entries.get(name).size, true);
        assert.equal(changes[0].catalog_size_updated, true);
      }
      assert.deepEqual(output.subarray(offset, offset + size), expected);
    }
    for (const change of changes) assert.ok(output.subarray(change.offset, change.offset + change.padding_bytes).every(value => value === 0));
  }
});

test('Buffer input stays byte-identical when gap normalization creates a new image', () => {
  const source = Buffer.from(makeImage(vectors[3])), before = Buffer.from(source);
  normalizeLfi(source, {lfi_size: source.length, sector_size: 512});
  assert.deepEqual(source, before);
});

test('bad file checksums are rejected rather than hidden by normalization', () => {
  const source = makeImage(vectors[1]); source[8192] ^= 1;
  assert.throws(() => normalizeLfi(source, {lfi_size: source.length, sector_size: 512}), /Dateiprüfsumme/);
});

test('image selection retains original source and binds repaired bytes to their hash', async () => {
  const source = makeImage(vectors[3]), device = {lfi_size: source.length, sector_size: 512};
  const selection = await selectImages([{name: 'old.bin', size: source.length, arrayBuffer: async () => source.buffer}], device);
  assert.deepEqual(selection.originals[0].data, source);
  assert.equal(selection.hashes.lfi, await sha256(normalizeLfi(source, device)));
  assert.equal(selection.normalization[0].cleared_nonzero_bytes, 512);
});

test('bundle normalization authenticates original hashes and creates a valid new manifest', async () => {
  const raw = files['lfi.bin'].slice(), header = view(raw), entries = validateLfi(raw, profile);
  const last = [...entries.values()].at(-1);
  header.setUint32(last.directoryOffset + 20, 512, true);
  header.setUint32(16, wordSum(raw.subarray(512, 8192), 4), true);
  header.setUint16(510, wordSum(raw.subarray(0, 510), 2), true);
  const manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  manifest.files['lfi.bin'].sha256 = await sha256(raw);
  const source = {...files, 'lfi.bin': raw, 'manifest.json': new TextEncoder().encode(JSON.stringify(manifest))};
  const normalized = await normalizeBundle(source, profile);
  assert.deepEqual(normalized.originals['lfi.bin'], raw);
  assert.deepEqual(normalized.files['lfi.bin'], files['lfi.bin']);
  assert.equal((await validateBundle(normalized.files, profile)).files['lfi.bin'].sha256, await sha256(files['lfi.bin']));
  assert.equal(normalized.manifest.lfi_normalization.source_sha256, manifest.files['lfi.bin'].sha256);
  assert.equal(normalized.manifest.hardware_verified, false);
  assert.equal(normalized.manifest.kind, 'firmware_candidate');
  raw[8192] ^= 1;
  await assert.rejects(() => normalizeBundle(source, profile), /prüfsumme/i);
});
