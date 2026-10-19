/** Explicit offline screensaver upgrade. Opening/editing never invokes applyPatch. */
import { sha256, validateLfi, view, wordSum, LFI, normalizeLfi } from './formats.js';
import { REVISION, processBackground, catalogOffsets, configCountOffset, CATALOG } from './screensaver.js';

export const LABELS = Object.freeze({
  install: 'Screensaver nachrüsten', update: 'Screensaver aktualisieren', current: 'Screensaver bereits aktuell',
  unsupported: 'Dieser Firmwarestand wird für den Screensaver-Patch nicht unterstützt.', missing: 'Für den Screensaver-Patch ein LFI laden.',
});

function variants(recipe) {
  return recipe.schema === 1 ? Object.entries(recipe.variants).map(([state, hashes]) => ({state, hashes, target_hashes: recipe.variants.current})) : recipe.variants;
}

function resource(data, entries, name) {
  const entry = entries.get(name);
  if (!entry) throw new Error(`Screensaver-Ressource fehlt: ${name}`);
  return data.subarray(entry.offset, entry.offset + entry.size);
}

async function matchVariant(data, entries, recipe) {
  const hashes = {};
  for (const name of Object.keys(recipe.writes)) {
    if (!entries.has(name)) return null;
    hashes[name] = await sha256(resource(data, entries, name));
  }
  return variants(recipe).find(variant => Object.keys(hashes).length === Object.keys(variant.hashes).length &&
    Object.entries(hashes).every(([name, hash]) => variant.hashes[name] === hash));
}

export async function inspectPatch(data, config, device) {
  if (!data) return 'missing';
  const profile = config.screensaver, recipe = profile?.patch;
  if (![1, 2].includes(recipe?.schema) || profile.revision !== REVISION || data.length !== recipe.lfi_size) return 'unsupported';
  const entries = validateLfi(data, device);
  for (const [name, guards] of Object.entries(recipe.guards)) {
    const entry = entries.get(name);
    if (!entry) return 'unsupported';
    for (const guard of guards) {
      if (guard.offset < 0 || guard.size <= 0 || guard.offset + guard.size > entry.size) return 'unsupported';
      if (await sha256(data.subarray(entry.offset + guard.offset, entry.offset + guard.offset + guard.size)) !== guard.sha256) return 'unsupported';
    }
  }
  const variant = await matchVariant(data, entries, recipe);
  if (!variant) return 'unsupported';
  if (recipe.schema === 2 && variant.state === 'current') {
    try {
      const offsets = catalogOffsets(resource(data, entries, 'COMMON.STY')), native = resource(data, entries, 'CONFIG.BIN');
      if (view(native).getUint16(configCountOffset(native), true) !== offsets.length) return 'update';
    } catch { return 'unsupported'; }
  }
  return variant.state;
}

function backgroundRanges(data, entry, config) {
  const {sty, screensaver: profile} = config, raw = data.subarray(entry.offset, entry.offset + entry.size), input = view(raw);
  const count = input.getUint16(sty.count_offset, true), end = sty.table_offset + count * sty.record_size;
  if (input.getUint32(0, true) !== sty.magic || end > raw.length) throw new Error('Ungültige Hintergrund-Bildtabelle.');
  const records = Array.from({length: count}, (_, i) => {
    const at = sty.table_offset + i * sty.record_size;
    return {start: input.getUint32(at, true), size: input.getUint32(at + 4, true), format: input.getUint16(at + 8, true),
      width: input.getUint16(at + 10, true), height: input.getUint16(at + 12, true)};
  });
  return Array.from({length: profile.background_count}, (_, i) => {
    const index = profile.background_base + i, record = records[index];
    if (!record) throw new Error('Hintergrundplatz fehlt.');
    const {start, size, format, width, height} = record;
    if (size !== config.pixel_bytes || format !== sty.format || width !== config.width || height !== config.height || start < end || start + size > raw.length ||
        records.some((other, n) => n !== index && other.size && other.start < start + size && other.start + other.size > start)) throw new Error('Ungültiger oder überlappender Hintergrundplatz.');
    return {slot: i + 1, start: entry.offset + start, size};
  });
}

export async function applyPatch(data, config, device) {
  const state = await inspectPatch(data, config, device);
  if (state === 'current') return data;
  if (!['install', 'update'].includes(state)) throw new Error(LABELS[state]);
  const profile = config.screensaver, recipe = profile.patch, entries = validateLfi(data, device);
  const variant = await matchVariant(data, entries, recipe);
  const ranges = backgroundRanges(data, entries.get('COMMON.STY'), config), output = data.slice();
  for (const [name, writes] of Object.entries(recipe.writes)) {
    const entry = entries.get(name); let end = 0;
    for (const write of writes) {
      const hex = recipe.blobs[write.blob];
      if (!hex || hex.length % 2 || !/^[0-9a-f]+$/.test(hex)) throw new Error('Ungültige Screensaver-Patchdaten.');
      const raw = Uint8Array.from(hex.match(/../g), byte => parseInt(byte, 16));
      if (await sha256(raw) !== write.blob || write.offset < end || write.offset + raw.length > entry.size) throw new Error('Ungültige Screensaver-Patchdaten.');
      end = write.offset + raw.length; output.set(raw, entry.offset + write.offset);
    }
    if (await sha256(output.subarray(entry.offset, entry.offset + entry.size)) !== variant.target_hashes[name]) throw new Error(`Screensaver-Zielprüfung fehlgeschlagen: ${name}`);
  }
  const changed = new Set([...Object.keys(recipe.writes), 'COMMON.STY']);
  if (recipe.schema === 2) {
    const common = entries.get('COMMON.STY'), style = resource(data, entries, 'COMMON.STY');
    let offsets;
    if (style.subarray(CATALOG.offset, CATALOG.end).some(byte => byte)) offsets = catalogOffsets(style);
    else {
      if (data.subarray(ranges[0].start, ranges[0].start + ranges[0].size).some(byte => byte)) throw new Error('Der feste schwarze Screensaver-Platz enthält ein Bild.');
      offsets = [0, ...ranges.slice(1).map(item => item.start - common.offset)];
      const header = view(output), at = common.offset + CATALOG.offset;
      header.setUint32(at, 0x43533658, true); header.setUint16(at + 4, 1, true); header.setUint16(at + 6, offsets.length, true);
      header.setUint32(at + 8, common.size, true); header.setUint32(at + 12, 0, true);
      offsets.forEach((offset, i) => header.setUint32(common.offset + CATALOG.entries + i * 4, offset, true));
    }
    const native = resource(data, entries, 'CONFIG.BIN'), countAt = entries.get('CONFIG.BIN').offset + configCountOffset(native);
    view(output).setUint16(countAt, offsets.length, true); changed.add('CONFIG.BIN');
  }
  for (const {slot, start, size} of ranges) output.set(processBackground(data.subarray(start, start + size), profile, slot), start);
  const input = view(output);
  let directory = LFI.directory;
  for (const [name, entry] of entries) {
    if (changed.has(name)) input.setUint32(directory + 28, wordSum(output.subarray(entry.offset, entry.offset + entry.size), 4), true);
    directory += LFI.entry;
  }
  input.setUint32(16, wordSum(output.subarray(LFI.directory, LFI.end), 4), true);
  input.setUint16(510, wordSum(output.subarray(0, 510), 2), true);
  const result = normalizeLfi(output, device);
  if (await inspectPatch(result, config, device) !== 'current') throw new Error('Screensaver-Patch konnte nicht verifiziert werden.');
  return result;
}
