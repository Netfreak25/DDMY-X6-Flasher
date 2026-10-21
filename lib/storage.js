/** Bounded offline LFI allocation. Only the named zero reserve is free space. */
import layout from '../tools/x6_storage.json' with {type: 'json'};
import {LFI, bytes, view, wordSum, validateLfi} from './formats.js';

export const STORAGE = Object.freeze(layout);
const SKIN_HEADER_BYTES = 32, SKIN_COUNT = 8, MAX_SKIN_ROLES = 512, NATIVE_BITMAP_LIMIT = 0x1000;
const SKIN_PALETTE_HEADER_BYTES = 40, SKIN_PALETTE_BYTES = 128;
const overlap = (a, b) => a[0] < b[1] && b[0] < a[1];

/** Follow the native pointer; relocated bitmap tables may follow old pixels. */
export function bitmapTable(raw) {
  const input = view(raw);
  if (raw.length < STORAGE.catalog_end || input.getUint32(0, true) !== STORAGE.sty_magic) throw new Error('Ungültige STY-Datei.');
  const table = input.getUint32(0x10, true), count = input.getUint16(STORAGE.bitmap_count_offset, true);
  if (table < STORAGE.catalog_end || table % 4 || table + count * STORAGE.bitmap_record > raw.length) throw new Error('Bildtabelle ist unvollständig.');
  return {table, count};
}

/** Independent bounded v2 layout reader; immutable skins never alias wallpapers. */
function skinLayout(raw, extension, count) {
  if (extension < STORAGE.catalog_end || extension % 4 || extension + SKIN_HEADER_BYTES > raw.length) throw new Error('Ungültiger Skin-Metadatenverweis.');
  const input = view(raw), at = extension;
  const version = input.getUint16(at + 4, true), size = input.getUint16(at + 6, true),
    expectedSize = version === 1 ? SKIN_HEADER_BYTES : version === 2 ? SKIN_PALETTE_HEADER_BYTES : 0;
  const prefix = input.getUint32(at + 8, true), skins = input.getUint16(at + 12, true), roles = input.getUint16(at + 14, true),
    mapping = input.getUint32(at + 16, true), mapBytes = input.getUint32(at + 20, true);
  if (new TextDecoder().decode(raw.subarray(at, at + 4)) !== 'X6SK' || !expectedSize || size !== expectedSize ||
      input.getUint32(at + 24, true) || input.getUint32(at + 28, true) ||
      skins !== SKIN_COUNT || roles < 1 || roles > MAX_SKIN_ROLES || mapBytes !== skins * roles * 2 || prefix % STORAGE.sector_size ||
      at + size > prefix || prefix + (count - STORAGE.standard_count) * STORAGE.pixel_bytes !== raw.length ||
      mapping < at + size || mapping % 2 || mapping + mapBytes > prefix) throw new Error('Ungültiger Skin-Grundbereich.');
  const {table, count: bitmapCount} = bitmapTable(raw), tableSpan = [table, table + bitmapCount * STORAGE.bitmap_record];
  const protectedSpans = [[at, at + size], [mapping, mapping + mapBytes]];
  let paletteOffset = 0, paletteBytes = 0;
  if (version === 2) {
    paletteOffset = input.getUint32(at + SKIN_HEADER_BYTES, true); paletteBytes = input.getUint32(at + SKIN_HEADER_BYTES + 4, true);
    if (paletteBytes !== SKIN_PALETTE_BYTES || paletteOffset % 2 || paletteOffset < mapping + mapBytes || paletteOffset + paletteBytes > prefix) {
      throw new Error('Ungültige geschützte Skin-Farbtabelle.');
    }
    protectedSpans.push([paletteOffset, paletteOffset + paletteBytes]);
  }
  if (bitmapCount > NATIVE_BITMAP_LIMIT || tableSpan[1] > prefix || protectedSpans.some(span => overlap(tableSpan, span))) {
    throw new Error('Skin-Bildtabelle überlappt Metadaten oder Zusatzbilder.');
  }
  const ids = [...new Set(Array.from({length: skins * roles}, (_, index) => input.getUint16(mapping + index * 2, true)))].sort((a, b) => a - b);
  const standard = Array.from({length: STORAGE.standard_count}, (_, index) => {
    const pos = table + (STORAGE.bitmap_base + index) * STORAGE.bitmap_record;
    if (pos + STORAGE.bitmap_record > tableSpan[1]) throw new Error('Standardbilder fehlen im Skin-Grundbereich.');
    const offset = input.getUint32(pos, true); return [offset, offset + input.getUint32(pos + 4, true)];
  });
  for (const index of ids) {
    if (index >= bitmapCount || index >= NATIVE_BITMAP_LIMIT) throw new Error('Skin-Bild-ID liegt außerhalb der nativen Tabelle.');
    const pos = table + index * STORAGE.bitmap_record, offset = input.getUint32(pos, true), length = input.getUint32(pos + 4, true),
      format = input.getUint16(pos + 8, true), width = input.getUint16(pos + 10, true), height = input.getUint16(pos + 12, true);
    if (format !== STORAGE.bitmap_format || !width || !height || length !== width * height * 2 ||
        standard.some(span => overlap([offset, offset + length], span))) throw new Error('Skin-Bild teilt einen editierbaren Bildbereich oder hat ein falsches Format.');
  }
  return {prefix_bytes: prefix, protected: protectedSpans, skin_ids: ids, extension_offset: extension, role_count: roles,
    layout_version: version, palette_offset: paletteOffset, palette_bytes: paletteBytes};
}

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
  if (![STORAGE.catalog_version, 2].includes(version) || (version === 1 && flags) || size !== raw.length || count < STORAGE.standard_count || count > STORAGE.maximum) {
    throw new Error('Ungültiger oder unbekannter Bilderkatalog.');
  }
  if (raw.subarray(table + count * 4, end).some(value => value)) throw new Error('Reservierte Katalogbytes sind nicht null.');
  const offsets = Array.from({length: count}, (_, index) => input.getUint32(table + index * 4, true));
  const skin = version === 2 ? skinLayout(raw, flags, count) : null, baseSize = skin?.prefix_bytes || STORAGE.common_base_size;
  const expectedSize = baseSize + (count - STORAGE.standard_count) * STORAGE.pixel_bytes;
  if (raw.length !== expectedSize) throw new Error('Unbekannter Zusatzbereich von COMMON.STY.');
  const {table: bitmapStart, count: bitmapCount} = bitmapTable(raw), tableEnd = bitmapStart + bitmapCount * STORAGE.bitmap_record;
  const protectedSpans = [[bitmapStart, tableEnd], ...(skin?.protected || [])];
  if (bitmapCount < STORAGE.bitmap_base + STORAGE.standard_count || tableEnd > baseSize) throw new Error('Unbekannte Standard-Bildtabelle.');
  for (let index = 0; index < bitmapCount; index++) {
    const at = bitmapStart + index * STORAGE.bitmap_record, offset = input.getUint32(at, true), length = input.getUint32(at + 4, true);
    if (length && (offset < STORAGE.catalog_end || offset % 2 || offset + length > baseSize ||
        protectedSpans.some(span => overlap([offset, offset + length], span)))) {
      throw new Error('Eine Standardressource greift in den verwalteten Zusatzbereich.');
    }
  }
  const standardOffsets = [];
  for (let index = 0; index < STORAGE.standard_count; index++) {
    const at = bitmapStart + (STORAGE.bitmap_base + index) * STORAGE.bitmap_record;
    const offset = input.getUint32(at, true), length = input.getUint32(at + 4, true), format = input.getUint16(at + 8, true),
      width = input.getUint16(at + 10, true), height = input.getUint16(at + 12, true);
    if (length !== STORAGE.pixel_bytes || format !== STORAGE.bitmap_format || width !== STORAGE.width || height !== STORAGE.height ||
        offset < STORAGE.catalog_end || offset % 2 || offset + length > baseSize) throw new Error('Unbekannter Standard-Hintergrundbereich.');
    standardOffsets.push(offset);
  }
  const expected = [0, ...standardOffsets.slice(1), ...Array.from({length: count - STORAGE.standard_count},
    (_, index) => baseSize + index * STORAGE.pixel_bytes)];
  if (offsets.some((offset, index) => offset !== expected[index])) throw new Error('Katalogoffsets entsprechen keinem verwalteten Bildlayout.');
  let previous = STORAGE.catalog_end;
  for (const offset of [...standardOffsets].sort((a, b) => a - b)) {
    if (offset < previous) throw new Error('Überlappende Standard-Hintergründe.');
    previous = offset + STORAGE.pixel_bytes;
  }
  const config = entries.get(STORAGE.config_name);
  if (!config) throw new Error('CONFIG.BIN fehlt zum Bilderkatalog.');
  const configRaw = payload(image, config);
  if (view(configRaw).getUint16(configOffset(configRaw), true) !== count) throw new Error('Bilderkatalog und konfigurierte Bildanzahl widersprechen sich.');
  return {...report, supported: true, reason: '', count, catalog_version: version, prefix_bytes: baseSize, skin,
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
  const entries = entriesFor(image, profile), baseSize = report.prefix_bytes, common = new Uint8Array(baseSize + extras.length * STORAGE.pixel_bytes);
  common.set(payload(image, entries.get(STORAGE.common_name)).subarray(0, baseSize));
  extras.forEach((raw, index) => common.set(raw, baseSize + index * STORAGE.pixel_bytes));
  const offsets = [...report.backgrounds.slice(0, STORAGE.standard_count).map(item => item.offset),
    ...extras.map((_, index) => baseSize + index * STORAGE.pixel_bytes)], header = view(common);
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
