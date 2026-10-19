/** Synthetic code states for offline patch and UI tests. */
import {artworkFixture, config as catalog} from './artwork-fixture.js';
import {validateLfi, view, wordSum, sha256} from '../lib/formats.js';

export function sums(data) {
  const input = view(data);
  for (let at = 512; at < 8192 && data[at]; at += 32) {
    const start = input.getUint32(at + 16, true) * 512, size = input.getUint32(at + 20, true);
    input.setUint32(at + 28, wordSum(data.subarray(start, start + size), 4), true);
  }
  input.setUint32(16, wordSum(data.subarray(512, 8192), 4), true);
  input.setUint16(510, wordSum(data.subarray(0, 510), 2), true); return data;
}

export async function patchFixture() {
  const {selection, profile} = await artworkFixture(), config = JSON.parse(JSON.stringify(catalog));
  const entries = validateLfi(selection.components.lfi, profile), names = ['T0000003.BIN', 'T0000004.BIN'];
  const variants = {}, versions = {};
  for (const [state, byte] of [['install', 0], ['update', 17], ['current', 34]]) {
    const data = selection.components.lfi.slice(), hashes = {};
    for (const name of names) {
      const entry = entries.get(name), value = new Uint8Array(entry.size);
      value.fill(byte, 0, 4);
      data.set(value, entry.offset); hashes[name] = await sha256(value);
    }
    variants[state] = hashes; versions[state] = sums(data);
  }
  const blob = await sha256(new Uint8Array(4).fill(34));
  config.screensaver.patch = {schema: 1, lfi_size: profile.lfi_size, guards: {}, variants,
    writes: Object.fromEntries(names.map(name => [name, [{offset: 0, blob}]])), blobs: {[blob]: '22222222'}};
  config.screensaver.supported_code = variants.current;
  return {selection, profile, config, versions};
}
