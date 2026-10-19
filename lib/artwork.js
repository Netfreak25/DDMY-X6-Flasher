/** Offline fixed-slot editor. Original components are copied and never mutated. */
import { LFI, view, wordSum, validateLfi, validateBundle, sha256, compareBytes } from './formats.js';
import { AREA_FILES, identify, validateSelection } from './images.js';
import { supported, processBackground } from './screensaver.js';

/** Same integer channel rounding as the native implementation. */
export function encodeRgb565(rgb, endian) {
  if (rgb.length % 3 || !['little', 'big'].includes(endian)) throw new Error('Ungültige RGB-Daten.');
  const output = new Uint8Array(rgb.length / 3 * 2), words = view(output);
  for (let i = 0; i < rgb.length; i += 3) {
    const word = (Math.floor((rgb[i] * 31 + 127) / 255) << 11) |
      (Math.floor((rgb[i + 1] * 63 + 127) / 255) << 5) | Math.floor((rgb[i + 2] * 31 + 127) / 255);
    words.setUint16(i / 3 * 2, word, endian === 'little');
  }
  return output;
}

export function decodeRgb565(data, endian) {
  if (data.length % 2 || !['little', 'big'].includes(endian)) throw new Error('Ungültige RGB565-Daten.');
  const output = new Uint8Array(data.length / 2 * 3), words = view(data);
  for (let i = 0; i < data.length; i += 2) {
    const word = words.getUint16(i, endian === 'little');
    output.set([Math.floor((word >>> 11) * 255 / 31), Math.floor((word >>> 5 & 63) * 255 / 63), Math.floor((word & 31) * 255 / 31)], i / 2 * 3);
  }
  return output;
}

/** Validate complete directory, picture tables and the independent BREC descriptor. */
export function locatePictures(components, profile, config) {
  const ranges = new Map();
  if (components.lfi) {
    const data = components.lfi, entries = validateLfi(data, profile), sty = config.sty;
    let previous = LFI.end;
    for (const entry of [...entries.values()].sort((a, b) => a.offset - b.offset)) {
      if (entry.offset < previous) throw new Error('Überlappende LFI-Dateien.');
      previous = entry.offset + entry.size;
    }
    const names = [...entries.keys()];
    for (const slot of config.slots.filter(item => item.area === 'lfi')) {
      const entry = entries.get(slot.file);
      if (!entry) throw new Error(`Bildressource fehlt: ${slot.file}`);
      const raw = data.subarray(entry.offset, entry.offset + entry.size), input = view(raw);
      if (raw.length < sty.table_offset || input.getUint32(0, true) !== sty.magic) throw new Error('Ungültige STY-Datei.');
      const count = input.getUint16(sty.count_offset, true), tableEnd = sty.table_offset + count * sty.record_size;
      if (slot.index >= count || tableEnd > raw.length) throw new Error('Bildtabelle ist unvollständig.');
      const pos = sty.table_offset + slot.index * sty.record_size;
      const offset = input.getUint32(pos, true), size = input.getUint32(pos + 4, true);
      if (size !== config.pixel_bytes || input.getUint16(pos + 8, true) !== sty.format ||
          input.getUint16(pos + 10, true) !== config.width || input.getUint16(pos + 12, true) !== config.height) {
        throw new Error(`Unbekanntes Bildformat: ${slot.id}`);
      }
      for (let index = 0; index < count; index++) {
        const pointer = sty.table_offset + index * sty.record_size;
        const start = input.getUint32(pointer, true), length = input.getUint32(pointer + 4, true);
        if (length && (start < tableEnd || start + length > raw.length)) throw new Error('Bildbereich außerhalb der STY-Datei.');
        if (index !== slot.index && start < offset + size && start + length > offset) throw new Error('Überlappende Bildressourcen.');
      }
      ranges.set(slot.id, {start: entry.offset + offset, end: entry.offset + offset + size, slot,
        entry: {...entry, directoryOffset: LFI.directory + names.indexOf(slot.file) * LFI.entry}});
    }
  }
  if (components.brec) {
    const data = components.brec;
    if (identify(data, profile) !== 'brec') throw new Error('BREC fehlt.');
    const input = view(data), code = input.getUint16(4, true), total = input.getUint16(6, true);
    const start = code * profile.sector_size, end = data.length - profile.sector_size;
    if (code !== config.brec.code_sectors || total !== config.brec.total_sectors) throw new Error('Unbekanntes BREC-Bildlayout.');
    if (end - start !== config.pixel_bytes || !config.brec.descriptor_magic.every((value, i) => data[end + i] === value) ||
        input.getUint16(end + 4) !== config.width || input.getUint16(end + 6) !== config.height) throw new Error('Ungültiger BREC-Bilddeskriptor.');
    const slot = config.slots.find(item => item.area === 'brec');
    ranges.set(slot.id, {start, end, slot});
  }
  return ranges;
}

export class Artwork {
  /** Call create() to validate source hashes before constructing the editor. */
  static async create(selection, profile, config) {
    await validateSelection(selection, profile);
    const model = new Artwork(selection, profile, config);
    model.contour = Boolean(model.originals.lfi && await supported(model.originals.lfi, validateLfi(model.originals.lfi, profile), config.screensaver));
    return model;
  }

  constructor(selection, profile, config) {
    this.profile = profile;
    this.config = config;
    this.selection = selection;
    this.originals = Object.fromEntries(Object.entries(selection.components).map(([area, data]) => [area, data.slice()]));
    this.ranges = locatePictures(this.originals, profile, config);
    if (!this.ranges.size) throw new Error('LFI oder BREC mit Bildern fehlt.');
    this.edits = new Map();
    this.contour = false;
    this.coupled = config.startup_ids.every(id => this.ranges.has(id));
  }

  targets(id) {
    if (!this.ranges.has(id)) throw new Error('Dieser Bildplatz ist nicht geladen.');
    return this.coupled && this.config.startup_ids.includes(id) ? this.config.startup_ids : [id];
  }

  pixels(id, original = false) {
    const item = this.ranges.get(id);
    return !original && this.edits.has(id) ? this.edits.get(id) : this.originals[item.slot.area].subarray(item.start, item.end);
  }

  rgb(id, original = false) { return decodeRgb565(this.pixels(id, original), this.ranges.get(id).slot.endian); }

  preparePixels(id, raw) {
    const slot = this.ranges.get(id).slot, profile = this.config.screensaver;
    const index = slot.index - (profile?.background_base || 0) + 1;
    return this.contour && slot.file === 'COMMON.STY' && index >= 1 && index <= profile.background_count ? processBackground(raw, profile, index) : raw;
  }

  /** Single final-crop pipeline used by the preview and stored replacement. */
  prepare(id, rgb) {
    if (rgb.length !== this.config.width * this.config.height * 3) throw new Error('Das Bild muss genau 240 × 320 RGB-Pixel enthalten.');
    return this.preparePixels(id, encodeRgb565(rgb, this.ranges.get(id).slot.endian));
  }

  previewRgb(id, rgb) { return decodeRgb565(this.prepare(id, rgb), this.ranges.get(id).slot.endian); }

  store(id, encoded) {
    if (compareBytes(encoded, this.pixels(id, true)).identical) this.edits.delete(id);
    else this.edits.set(id, encoded);
  }

  replace(id, rgb) {
    if (rgb.length !== this.config.width * this.config.height * 3) throw new Error('Das Bild muss genau 240 × 320 RGB-Pixel enthalten.');
    for (const target of this.targets(id)) {
      this.store(target, this.prepare(target, rgb));
    }
  }

  reset(id) { for (const target of this.targets(id)) this.store(target, this.preparePixels(target, this.pixels(target, true))); }
  get changedAreas() { return ['brec', 'lfi'].filter(area => [...this.edits.keys()].some(id => this.ranges.get(id).slot.area === area)); }

  build() {
    const output = {...this.originals};
    for (const area of this.changedAreas) {
      const data = output[area].slice(), changedFiles = new Map();
      for (const [id, pixels] of this.edits) {
        const item = this.ranges.get(id);
        if (item.slot.area !== area) continue;
        data.set(pixels, item.start);
        if (area === 'lfi') changedFiles.set(item.slot.file, item.entry);
      }
      if (area === 'lfi') {
        const input = view(data);
        for (const entry of changedFiles.values()) input.setUint32(entry.directoryOffset + 28, wordSum(data.subarray(entry.offset, entry.offset + entry.size), 4), true);
        input.setUint32(16, wordSum(data.subarray(LFI.directory, LFI.end), 4), true);
        input.setUint16(510, wordSum(data.subarray(0, 510), 2), true);
      }
      output[area] = data;
    }
    locatePictures(output, this.profile, this.config);
    return output;
  }

  async changedSelection() {
    if (!this.edits.size) throw new Error('Keine Bilder geändert.');
    const output = this.build(), components = {}, hashes = {};
    for (const area of this.changedAreas) { components[area] = output[area]; hashes[area] = await sha256(output[area]); }
    return {components, hashes, names: this.changedAreas.map(area => `x6-bilder-${area}.bin`), serial: this.selection.serial};
  }

  async packageFiles() {
    if (Object.keys(this.originals).length !== Object.keys(AREA_FILES).length || !this.edits.size) throw new Error('Ein bearbeitetes Paket benötigt MBREC, BREC und LFI.');
    const output = this.build(), files = {};
    const manifest = {schema: 1, model: this.profile.model, nand_id: this.profile.nand_id, status: 'complete',
      kind: 'edited-firmware', files: {}, edited_images: [...this.edits.keys()], steps: {artwork: {status: 'ok'}}};
    if (this.selection.serial) manifest.device_serial = this.selection.serial;
    for (const [area, names] of Object.entries(AREA_FILES)) {
      for (const name of names) {
        files[name] = output[area];
        manifest.files[name] = {size: files[name].length, sha256: await sha256(files[name])};
      }
    }
    files['manifest.json'] = new TextEncoder().encode(JSON.stringify(manifest, null, 2));
    await validateBundle(files, this.profile);
    return files;
  }
}
