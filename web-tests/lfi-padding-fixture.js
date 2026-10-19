/** Shared synthetic inputs for independent Python/JavaScript normalization tests. */
import {view, wordSum} from '../lib/formats.js';

export function makeImage(vector) {
  const data = new Uint8Array(vector.size), header = view(data);
  header.setUint32(0, 0x0ff0aa55, true);
  for (const [start, end, value] of vector.padding || []) data.fill(value, start, end);
  for (const [, offset, , value] of vector.entries) header.setUint32(offset, value, true);
  for (const [name, offset, size] of vector.entries) if (vector.catalog && name === 'COMMON.STY') {
    header.setUint32(offset + 0x40, 0x43533658, true);
    header.setUint16(offset + 0x44, 1, true); header.setUint16(offset + 0x46, 1, true);
    header.setUint32(offset + 0x48, size, true);
  }
  vector.entries.forEach(([name, offset, size], index) => {
    const pos = 512 + index * 32, [base, ext] = name.split('.');
    data.set(new TextEncoder().encode(base.padEnd(8) + ext.padEnd(3)), pos);
    header.setUint32(pos + 16, offset / 512, true); header.setUint32(pos + 20, size, true);
    header.setUint32(pos + 28, wordSum(data.subarray(offset, offset + size), 4), true);
  });
  header.setUint32(16, wordSum(data.subarray(512, 8192), 4), true);
  header.setUint16(510, wordSum(data.subarray(0, 510), 2), true);
  return data;
}
