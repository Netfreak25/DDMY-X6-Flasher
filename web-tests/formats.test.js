import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { validateLfi, validateBundle, compareBytes, checkHeader } from '../lib/formats.js';

import { profile, candidate, files as fixtureFiles } from './fixtures.js';
await mkdir('private_dumps/web-tests', {recursive: true});
await writeFile('private_dumps/web-tests/synthetic.bin', candidate);

test('all synthetic LFI entries match the existing independent Python parser', () => {
  const actual = [...validateLfi(candidate, profile)].map(([name, entry]) => [name, entry.offset, entry.size]);
  const script = "import sys,json;sys.path.insert(0,'tools');import x6_lfi as l;from pathlib import Path;print(json.dumps([[n,e.data_offset,e.length] for n,e in l.lfi_entries(Path(sys.argv[1]).read_bytes()).items()]))";
  const expected = JSON.parse(execFileSync('python', ['-c', script, 'private_dumps/web-tests/synthetic.bin'], { encoding: 'utf8' }));
  assert.equal(actual.length, 148);
  assert.deepEqual(actual, expected);
});

test('reject truncated image, header corruption and changed file content', () => {
  assert.throws(() => validateLfi(candidate.subarray(1), profile), /größe/);
  const copy = candidate.slice();
  copy[10] ^= 1;
  assert.throws(() => validateLfi(copy, profile), /Header/);
  copy[10] ^= 1;
  copy[0x3000] ^= 1;
  assert.throws(() => validateLfi(copy, profile), /Dateiprüfsumme/);
});

test('comparison counts all differing bytes including an invalid device header', () => {
  const actual = candidate.slice();
  actual[0] ^= 1;
  actual[actual.length - 1] ^= 1;
  assert.deepEqual(compareBytes(candidate, actual), {
    identical: false, different: 2, first: 0, expectedSize: candidate.length, actualSize: actual.length,
  });
  actual[40] ^= 1;
  assert.throws(() => checkHeader(candidate, actual, profile), /Gerätespezifischer/);
});

test('existing original bundle is accepted; corruption and incomplete status are rejected', async () => {
  const files = {...fixtureFiles};
  assert.equal((await validateBundle(files, profile)).status, 'complete');
  files['brec1.bin'] = files['brec1.bin'].slice();
  files['brec1.bin'][200] ^= 1;
  await assert.rejects(validateBundle(files, profile), /Prüfsumme/);
  files['manifest.json'] = new TextEncoder().encode(JSON.stringify({schema: 1, status: 'incomplete'}));
  await assert.rejects(validateBundle(files, profile), /unvollständig/);
});
