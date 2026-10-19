/** Synthetic firmware bytes from the same fixture specification as Python. */
import spec from '../tests/storage_fixture.json' with {type: 'json'};
import {STORAGE} from '../lib/storage.js';
import {LFI, view, wordSum} from '../lib/formats.js';

export function storageFixture() {
  const common = new Uint8Array(STORAGE.common_base_size), commonView = view(common);
  commonView.setUint32(0, STORAGE.sty_magic, true);
  commonView.setUint16(STORAGE.bitmap_count_offset, STORAGE.bitmap_base + STORAGE.standard_count, true);
  const offsets = [0];
  for (let index = 0; index < STORAGE.standard_count; index++) {
    const at = STORAGE.bitmap_table + (STORAGE.bitmap_base + index) * STORAGE.bitmap_record,
      offset = spec.bitmap_pixels_start + index * STORAGE.pixel_bytes;
    commonView.setUint32(at, offset, true); commonView.setUint32(at + 4, STORAGE.pixel_bytes, true);
    commonView.setUint16(at + 8, STORAGE.bitmap_format, true); commonView.setUint16(at + 10, STORAGE.width, true);
    commonView.setUint16(at + 12, STORAGE.height, true);
    if (index) { common.fill(index, offset, offset + STORAGE.pixel_bytes); offsets.push(offset); }
  }
  common.set(new TextEncoder().encode(STORAGE.catalog_magic), STORAGE.catalog_offset);
  commonView.setUint16(STORAGE.catalog_offset + 4, 1, true);
  commonView.setUint16(STORAGE.catalog_offset + 6, offsets.length, true);
  commonView.setUint32(STORAGE.catalog_offset + 8, common.length, true);
  offsets.forEach((offset, index) => commonView.setUint32(STORAGE.catalog_table + index * 4, offset, true));
  const config = new Uint8Array(512), configView = view(config);
  config.set(new TextEncoder().encode('cfg\0'));
  configView.setUint32(STORAGE.config_descriptor, spec.config_value_offset, true);
  configView.setUint32(STORAGE.config_descriptor + 4, STORAGE.config_record_size, true);
  configView.setUint16(spec.config_value_offset, offsets.length, true);
  const resources = {'COMMON.STY': common, 'CONFIG.BIN': config,
    'USER2.AP': Uint8Array.from({length: 512}, (_, index) => index % 256), 'USER4.AP': new Uint8Array(512),
    ...Object.fromEntries(Object.entries(spec.dictionary_sizes).map(([name, size], index) => [name, new Uint8Array(size).fill(index + 31)]))};
  const image = new Uint8Array(LFI.end + Object.values(resources).reduce((sum, data) => sum + data.length, 0)), header = view(image);
  header.setUint32(0, LFI.magic, true);
  let cursor = LFI.end;
  const positions = {};
  for (const name of spec.physical_files) {
    positions[name] = cursor; image.set(resources[name], cursor); cursor += resources[name].length;
  }
  spec.files.forEach((name, index) => {
    const at = LFI.directory + index * LFI.entry, [base, ext] = name.split('.'), raw = resources[name];
    image.set(new TextEncoder().encode(base.padEnd(8) + ext.padEnd(3)), at); image.fill(index + 1, at + 11, at + 16);
    header.setUint32(at + 16, positions[name] / 512, true); header.setUint32(at + 20, raw.length, true);
    header.setUint32(at + 24, index + 123, true); header.setUint32(at + 28, wordSum(raw, 4), true);
  });
  header.setUint32(16, wordSum(image.subarray(512, 8192), 4), true);
  header.setUint16(510, wordSum(image.subarray(0, 510), 2), true);
  return {image, profile: {lfi_size: image.length, sector_size: 512}};
}

export function pixels(value) {
  const raw = new Uint8Array(STORAGE.pixel_bytes), input = view(raw);
  for (let index = 0; index < raw.length; index += 2) input.setUint16(index, value, true);
  return raw;
}
