/** Synthetic blank image tables, not firmware extracted from a device. */
import { readFile } from 'node:fs/promises';
import { view, wordSum, sha256 } from '../lib/formats.js';
import { STORAGE } from '../lib/storage.js';

export const config = JSON.parse(await readFile(new URL('../tools/x6_artwork.json', import.meta.url)));
export const baseProfile = JSON.parse(await readFile(new URL('../tools/x6_profile.json', import.meta.url)));

export async function artworkFixture(size = 3 * 1024 * 1024, managed = false) {
  if (managed) size = Math.max(size, 8 * 1024 * 1024);
  const profile = {...baseProfile, lfi_size: size};
  const lfi = new Uint8Array(size), input = view(lfi);
  input.setUint32(0, 0x0ff0aa55, true);
  let offset = 8192;
  for (let index = 0; index < 148; index++) {
    let name = index < 3 ? ['COMMON.STY', 'LOGO.STY', 'END.STY'][index] : `T${String(index).padStart(7, '0')}.BIN`;
    if (managed && index === 3) name = 'CONFIG.BIN';
    if (managed && index === 147) name = 'X6FREE.BIN';
    const slots = config.slots.filter(slot => slot.file === name);
    let content = new Uint8Array(4);
    if (slots.length) {
      const count = Math.max(...slots.map(slot => slot.index)) + 1, tableEnd = 1024 + count * 16;
      content = new Uint8Array(tableEnd + slots.length * config.pixel_bytes);
      const table = view(content);
      table.setUint32(0, config.sty.magic, true); table.setUint16(20, count, true);
      table.setUint32(0x10, config.sty.table_offset, true);
      slots.forEach((slot, nth) => {
        const pos = 1024 + slot.index * 16;
        table.setUint32(pos, tableEnd + nth * config.pixel_bytes, true); table.setUint32(pos + 4, config.pixel_bytes, true);
        table.setUint16(pos + 8, 2, true); table.setUint16(pos + 10, 240, true); table.setUint16(pos + 12, 320, true);
      });
      if (managed && name === 'COMMON.STY') {
        const expanded = new Uint8Array(STORAGE.common_base_size); expanded.set(content); content = expanded;
        const catalog = view(content);
        catalog.setUint32(64, 0x43533658, true); catalog.setUint16(68, 1, true); catalog.setUint16(70, 11, true);
        catalog.setUint32(72, content.length, true);
        for (let nth = 1; nth < 11; nth++) catalog.setUint32(80 + nth * 4, tableEnd + nth * config.pixel_bytes, true);
      }
    }
    if (name === 'CONFIG.BIN') {
      content = new Uint8Array(512); const settings = view(content);
      settings.setUint32(0, 0x676663, true); settings.setUint32(72, 128, true); settings.setUint32(76, 8, true); settings.setUint16(128, 11, true);
    }
    const pos = 512 + index * 32, [base, ext] = name.split('.');
    lfi.set(new TextEncoder().encode(base.padEnd(8) + ext), pos);
    const allocated = index === 147 ? lfi.length - offset : Math.ceil(content.length / 512) * 512;
    input.setUint32(pos + 16, offset / 512, true); input.setUint32(pos + 20, allocated, true);
    input.setUint32(pos + 28, wordSum(content, 4), true); lfi.set(content, offset);
    offset += Math.ceil(content.length / 512) * 512;
  }
  input.setUint32(16, wordSum(lfi.subarray(512, 8192), 4), true); input.setUint16(510, wordSum(lfi.subarray(0, 510), 2), true);
  const brec = new Uint8Array(profile.brec_size), boot = view(brec), mbrec = new Uint8Array(profile.mbrec_size);
  boot.setUint16(4, 92, true); boot.setUint16(6, 393, true); brec.set([0x2c, 0x68, 4, 0x4a, 0xa9], 128);
  boot.setUint16(0x210, 0x1234, true);
  boot.setUint16(2, wordSum(brec.subarray(4, 512), 2), true);
  boot.setUint16(0, (wordSum(brec.subarray(2, 92 * 512), 2) + 0x1234) & 0xffff, true);
  brec.set(config.brec.descriptor_magic, brec.length - 512);
  boot.setUint16(brec.length - 508, 240); boot.setUint16(brec.length - 506, 320);
  mbrec.set([0x5a, 2, 3], 2); view(mbrec).setUint16(12, 256, true); view(mbrec).setUint16(14, 8192, true);
  const components = {lfi, brec, mbrec}, hashes = {};
  for (const [area, data] of Object.entries(components)) hashes[area] = await sha256(data);
  return {profile, selection: {components, hashes, names: ['synthetic.bin'], serial: null}};
}
