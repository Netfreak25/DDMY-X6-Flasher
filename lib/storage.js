/** Bounded offline LFI allocation. Only the named zero reserve is free space. */
import layout from '../tools/x6_storage.json' with {type: 'json'};
import {LFI, bytes, view, wordSum, validateLfi} from './formats.js';

export const STORAGE = Object.freeze(layout);

function entriesFor(image, profile = STORAGE) {
  const entries = validateLfi(image, profile);
  if (profile.sector_size !== STORAGE.sector_size) throw new Error('Falsche X6-Sektorgröße.');
  const reserve = entries.get(STORAGE.reserve_name);
  if (reserve && image.subarray(reserve.offset, reserve.offset + reserve.size).some(value => value)) {
    throw new Error('X6FREE.BIN enthält Daten und ist kein freier Speicher.');
  }
  return entries;
}

const payload = (image, entry) => image.subarray(entry.offset, entry.offset + entry.size);

function configOffset(raw) {
  const descriptor = STORAGE.config_descriptor, recordSize = STORAGE.config_record_size;
  if (new TextDecoder().decode(raw.subarray(0, 4)) !== 'cfg\0' || raw.length < descriptor + recordSize) {
    throw new Error('Ungültige Screensaver-Konfiguration.');
  }
  const input = view(raw), offset = input.getUint32(descriptor, true), size = input.getUint32(descriptor + 4, true);
  if (size !== recordSize || offset < descriptor + recordSize || offset + size > raw.length) {
    throw new Error('Ungültiger Screensaver-Anzahleintrag.');
  }
  return offset;
}

/** Return validated resources/capacity without modifying the imported firmware. */
export function inspectStorage(image, profile = STORAGE) {
  image = bytes(image);
  const entries = entriesFor(image, profile), free = entries.get(STORAGE.reserve_name)?.size || 0;
  const report = {supported: false, reason: 'Kein unterstützter Bilderkatalog vorhanden.', free_bytes: free,
    capacity: 0, count: 0, standard_count: STORAGE.standard_count, maximum: STORAGE.maximum, common_size: 0,
    backgrounds: [], entries: [...entries].map(([name, item]) => ({name, offset: item.offset, size: item.size}))};
  const common = entries.get(STORAGE.common_name);
  if (!common) return report;
  const raw = payload(image, common), input = view(raw), header = STORAGE.catalog_offset,
    table = STORAGE.catalog_table, end = STORAGE.catalog_end;
  report.common_size = raw.length;
  if (new TextDecoder().decode(raw.subarray(header, header + 4)) !== STORAGE.catalog_magic) return report;
  if (raw.length < end || input.getUint32(0, true) !== STORAGE.sty_magic) throw new Error('Ungültige COMMON.STY für den Bilderkatalog.');
  const version = input.getUint16(header + 4, true), count = input.getUint16(header + 6, true),
    size = input.getUint32(header + 8, true), flags = input.getUint32(header + 12, true);
  if (version !== STORAGE.catalog_version || flags || size !== raw.length || count < STORAGE.standard_count || count > STORAGE.maximum) {
    throw new Error('Ungültiger oder unbekannter Bilderkatalog.');
  }
  if (raw.subarray(table + count * 4, end).some(value => value)) throw new Error('Reservierte Katalogbytes sind nicht null.');
  const offsets = Array.from({length: count}, (_, index) => input.getUint32(table + index * 4, true));
  const expectedSize = STORAGE.common_base_size + (count - STORAGE.standard_count) * STORAGE.pixel_bytes;
  if (raw.length !== expectedSize) throw new Error('Unbekannter Zusatzbereich von COMMON.STY.');
  const bitmapCount = input.getUint16(STORAGE.bitmap_count_offset, true), tableEnd = STORAGE.bitmap_table + bitmapCount * STORAGE.bitmap_record;
  if (bitmapCount < STORAGE.bitmap_base + STORAGE.standard_count || tableEnd > STORAGE.common_base_size) throw new Error('Unbekannte Standard-Bildtabelle.');
  for (let index = 0; index < bitmapCount; index++) {
    const at = STORAGE.bitmap_table + index * STORAGE.bitmap_record, offset = input.getUint32(at, true), length = input.getUint32(at + 4, true);
    if (length && (offset < tableEnd || offset + length > STORAGE.common_base_size)) {
      throw new Error('Eine Standardressource greift in den verwalteten Zusatzbereich.');
    }
  }
  const standardOffsets = [];
  for (let index = 0; index < STORAGE.standard_count; index++) {
    const at = STORAGE.bitmap_table + (STORAGE.bitmap_base + index) * STORAGE.bitmap_record;
    const offset = input.getUint32(at, true), length = input.getUint32(at + 4, true), format = input.getUint16(at + 8, true),
      width = input.getUint16(at + 10, true), height = input.getUint16(at + 12, true);
    if (length !== STORAGE.pixel_bytes || format !== STORAGE.bitmap_format || width !== STORAGE.width || height !== STORAGE.height ||
        offset < tableEnd || offset % 2 || offset + length > STORAGE.common_base_size) throw new Error('Unbekannter Standard-Hintergrundbereich.');
    standardOffsets.push(offset);
  }
  const expected = [0, ...standardOffsets.slice(1), ...Array.from({length: count - STORAGE.standard_count},
    (_, index) => STORAGE.common_base_size + index * STORAGE.pixel_bytes)];
  if (offsets.some((offset, index) => offset !== expected[index])) throw new Error('Katalogoffsets entsprechen keinem verwalteten Bildlayout.');
  let previous = tableEnd;
  for (const offset of [...standardOffsets].sort((a, b) => a - b)) {
    if (offset < previous) throw new Error('Überlappende Standard-Hintergründe.');
    previous = offset + STORAGE.pixel_bytes;
  }
  const config = entries.get(STORAGE.config_name);
  if (!config) throw new Error('CONFIG.BIN fehlt zum Bilderkatalog.');
  const configRaw = payload(image, config);
  if (view(configRaw).getUint16(configOffset(configRaw), true) !== count) throw new Error('Bilderkatalog und konfigurierte Bildanzahl widersprechen sich.');
  return {...report, supported: true, reason: '', count,
    capacity: Math.min(Math.floor(free / STORAGE.pixel_bytes), STORAGE.maximum - count),
    backgrounds: offsets.map((offset, index) => ({index, id: `background-${String(index + 1).padStart(2, '0')}`,
      offset, absolute_offset: common.offset + (index === 0 ? standardOffsets[0] : offset), size: STORAGE.pixel_bytes,
      standard: index < STORAGE.standard_count, fixed_black: index === 0}))};
}

/** Preserve retained records and payloads; atomically return a fully owned LFI.
 * Arbitrary new resources and implicit reclamation of zero-filled APs are refused.
 */
export function repack(image, replacements = {}, remove = [], profile = STORAGE) {
  image = bytes(image);
  const entries = entriesFor(image, profile), removed = new Set(remove), reserveName = STORAGE.reserve_name;
  const replaced = Object.keys(replacements);
  if (Object.hasOwn(replacements, reserveName) || removed.has(reserveName) || replaced.some(name => removed.has(name))) {
    throw new Error('Widersprüchliche Änderungen am verwalteten Speicher.');
  }
  if ([...replaced, ...removed].some(name => !entries.has(name))) throw new Error('Unbekannte LFI-Datei für die Speicheränderung.');
  const payloads = new Map([...entries].filter(([name]) => !removed.has(name) && name !== reserveName)
    .map(([name, entry]) => [name, Object.hasOwn(replacements, name) ? bytes(replacements[name]) : payload(image, entry)]));
  if ([...payloads.values()].some(raw => !raw.length || raw.length % STORAGE.sector_size)) {
    throw new Error('LFI-Dateien müssen vollständig sektorweise zugeordnet bleiben.');
  }
  const free = image.length - LFI.end - [...payloads.values()].reduce((sum, raw) => sum + raw.length, 0);
  if (free < 0) throw new Error('Nicht genügend freier Firmware-Speicher.');
  if (free) payloads.set(reserveName, new Uint8Array(free));
  const names = [...entries.keys()].filter(name => payloads.has(name) && name !== reserveName);
  if (free) names.push(reserveName);
  if (names.length >= (LFI.end - LFI.directory) / LFI.entry) throw new Error('Das LFI-Verzeichnis hat keinen Platz für den Abschlusseintrag.');
  const output = new Uint8Array(image.length), header = view(output);
  output.set(image.subarray(0, LFI.directory));
  const physical = [...entries].sort((a, b) => a[1].offset - b[1].offset).map(([name]) => name)
    .filter(name => payloads.has(name) && name !== reserveName);
  if (free) physical.push(reserveName);
  const positions = new Map();
  let cursor = LFI.end;
  for (const name of physical) {
    const raw = payloads.get(name);
    positions.set(name, cursor); output.set(raw, cursor); cursor += raw.length;
  }
  for (let index = 0; index < names.length; index++) {
    const name = names[index], pos = LFI.directory + index * LFI.entry, entry = entries.get(name), raw = payloads.get(name);
    if (entry) output.set(image.subarray(entry.directoryOffset, entry.directoryOffset + LFI.entry), pos);
    else {
      const [base, extension] = name.split('.');
      output.set(new TextEncoder().encode(base.padEnd(8) + extension.padEnd(3)), pos);
    }
    header.setUint32(pos + 0x10, positions.get(name) / STORAGE.sector_size, true);
    header.setUint32(pos + 0x14, raw.length, true);
    header.setUint32(pos + 0x1c, wordSum(raw, 4), true);
  }
  header.setUint32(0x10, wordSum(output.subarray(LFI.directory, LFI.end), 4), true);
  header.setUint16(0x1fe, wordSum(output.subarray(0, 0x1fe), 2), true);
  entriesFor(output, profile);
  return output;
}

/** Explicit candidate operation; opening/editing images never removes dictionaries. */
export function removeDictionaries(image, profile = STORAGE) {
  image = bytes(image);
  const entries = entriesFor(image, profile), removed = STORAGE.dictionaries.filter(name => entries.has(name));
  return removed.length ? repack(image, {}, removed, profile) : image;
}

function rewriteBackgrounds(image, extras, report, profile) {
  const entries = entriesFor(image, profile), common = new Uint8Array(STORAGE.common_base_size + extras.length * STORAGE.pixel_bytes);
  common.set(payload(image, entries.get(STORAGE.common_name)).subarray(0, STORAGE.common_base_size));
  extras.forEach((raw, index) => common.set(raw, STORAGE.common_base_size + index * STORAGE.pixel_bytes));
  const offsets = [...report.backgrounds.slice(0, STORAGE.standard_count).map(item => item.offset),
    ...extras.map((_, index) => STORAGE.common_base_size + index * STORAGE.pixel_bytes)], header = view(common);
  common.fill(0, STORAGE.catalog_table, STORAGE.catalog_end);
  header.setUint16(STORAGE.catalog_offset + 6, offsets.length, true);
  header.setUint32(STORAGE.catalog_offset + 8, common.length, true);
  offsets.forEach((offset, index) => header.setUint32(STORAGE.catalog_table + index * 4, offset, true));
  const config = payload(image, entries.get(STORAGE.config_name)).slice();
  view(config).setUint16(configOffset(config), offsets.length, true);
  const result = repack(image, {[STORAGE.common_name]: common, [STORAGE.config_name]: config}, [], profile);
  inspectStorage(result, profile);
  return result;
}

const extraPixels = (image, report) => report.backgrounds.slice(STORAGE.standard_count)
  .map(item => image.subarray(item.absolute_offset, item.absolute_offset + item.size));

/** Append prepared RGB565 LE pixels, without embedding clock or date. */
export function addBackground(image, pixels, profile = STORAGE) {
  image = bytes(image); pixels = bytes(pixels);
  const report = inspectStorage(image, profile);
  if (!report.supported) throw new Error(report.reason);
  if (pixels.length !== STORAGE.pixel_bytes) throw new Error('Das Hintergrundbild muss genau 240 × 320 RGB565-Pixel enthalten.');
  if (!report.capacity) throw new Error('Kein Platz für weitere Hintergrundbilder.');
  return rewriteBackgrounds(image, [...extraPixels(image, report), pixels], report, profile);
}

/** Only appended images can be removed; original image positions stay stable. */
export function deleteBackground(image, index, profile = STORAGE) {
  image = bytes(image);
  const report = inspectStorage(image, profile);
  if (!report.supported) throw new Error(report.reason);
  if (!Number.isInteger(index) || index < STORAGE.standard_count || index >= report.count) {
    throw new Error('Schwarz und die Standardbilder können nicht gelöscht werden.');
  }
  const extras = extraPixels(image, report);
  extras.splice(index - STORAGE.standard_count, 1);
  return rewriteBackgrounds(image, extras, report, profile);
}
