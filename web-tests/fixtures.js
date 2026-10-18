/** Generated test bytes only. This file contains no device firmware or user data. */
import { readFile } from 'node:fs/promises';
import { view, wordSum, sha256, BUNDLE_FILES } from '../lib/formats.js';
import { encodeManifest } from '../lib/archive.js';

export const profile = JSON.parse(await readFile(new URL('../tools/x6_profile.json', import.meta.url)));
export const candidate = new Uint8Array(profile.lfi_size);
const header = view(candidate);
header.setUint32(0, 0x0ff0aa55, true);
for (let index = 0; index < 148; index++) {
  const pos = 0x200 + index * 32, offset = 0x2000 + index * 512;
  candidate.set(new TextEncoder().encode(`T${String(index).padStart(7, '0')}BIN`), pos);
  header.setUint32(pos + 0x10, offset / 512, true);
  header.setUint32(pos + 0x14, 4, true);
  header.setUint32(offset, index + 1, true);
  header.setUint32(pos + 0x1c, index + 1, true);
}
header.setUint32(0x10, wordSum(candidate.subarray(0x200, 0x2000), 4), true);
header.setUint16(0x1fe, wordSum(candidate.subarray(0, 0x1fe), 2), true);
const mbrec = new Uint8Array(profile.mbrec_size), brec = new Uint8Array(profile.brec_size);
mbrec.set([0x5a, 2, 3], 2);
view(mbrec).setUint16(0x0c, 256, true);
view(mbrec).setUint16(0x0e, 8192, true);
view(brec).setUint16(4, 128, true);
view(brec).setUint16(6, brec.length / 512, true);
brec.set([0x2c, 0x68, 4, 0x4a, 0xa9], 0x80);
view(brec).setUint16(2, wordSum(brec.subarray(4, 512), 2), true);
view(brec).setUint16(0, (wordSum(brec.subarray(2, 65536), 2) + 0x1234) & 0xffff, true);
export const files = {'lfi.bin': candidate, 'mbrec.bin': mbrec, 'mbrec_copy.bin': mbrec.slice(),
  'brec0.bin': brec, 'brec1.bin': brec.slice()};
const manifest = {schema: 1, model: profile.model, nand_id: profile.nand_id, status: 'complete', files: {}};
for (const name of BUNDLE_FILES) manifest.files[name] = {size: files[name].length, sha256: await sha256(files[name])};
files['manifest.json'] = encodeManifest(manifest);
const helpers = {};
for (const name of Object.keys(profile.runtime_sha256)) {
  helpers[name] = new Uint8Array(512).fill(name.length);
  if (name === 'fwsc.bin') {
    helpers[name] = new Uint8Array(0x4d4);
    helpers[name].set(Uint8Array.from('fe21201d03f09ffa6080218801eb40002080'.match(/../g), value => parseInt(value, 16)), 0x4c2);
  }
  profile.runtime_sha256[name] = await sha256(helpers[name]);
}
export const payload = async name => helpers[name];
