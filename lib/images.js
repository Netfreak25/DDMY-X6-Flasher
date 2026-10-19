/** Offline firmware identification. Filenames never select a flash address. */
import { bootLayout, brecLength } from './driver.js';
import { unpackBackup } from './archive.js';
import { validateLfi, validateBoot, normalizeLfi, normalizeBundle, sha256, view, wordSum } from './formats.js';

export const AREA_FILES = Object.freeze({mbrec: ['mbrec.bin', 'mbrec_copy.bin'], brec: ['brec0.bin', 'brec1.bin'], lfi: ['lfi.bin']});
export const areasOf = selection => Object.keys(AREA_FILES).filter(area => selection.components[area]);
export const selectionLabel = selection => areasOf(selection).map(area => area.toUpperCase()).join(' + ');
export const hasBoot = selection => Boolean(selection.components.mbrec || selection.components.brec);

export function identify(data, profile) {
  if (data.length === profile.lfi_size) { validateLfi(data, profile); return 'lfi'; }
  if (data.length === profile.mbrec_size) {
    validateBoot(data, 'mbrec.bin', profile);
    bootLayout(data, 0); bootLayout(data, 1);
    return 'mbrec';
  }
  if (data.length === profile.brec_size) {
    validateBoot(data, 'brec0.bin', profile);
    brecLength(data, profile.brec_size);
    if (view(data).getUint16(2, true) !== wordSum(data.subarray(4, profile.sector_size), 2)) {
      throw new Error('BREC-Headerprüfsumme fehlerhaft.');
    }
    return 'brec';
  }
  throw new Error('Kein vollständiger X6-MBREC, BREC oder LFI erkannt.');
}

/** Accept arbitrary BIN names, identical duplicate boot copies and complete ZIPs. */
export async function selectImages(sources, profile) {
  if (!sources.length) throw new Error('Bitte mindestens eine Firmwaredatei auswählen.');
  const selection = {components: {}, hashes: {}, names: [], serial: null, originals: [], normalization: []};
  for (const file of sources) {
    if (file.size > profile.lfi_size * 2) throw new Error('Firmwaredatei zu groß.');
    const data = new Uint8Array(await file.arrayBuffer());
    selection.originals.push({name: file.name, data});
    let inputs = [data];
    if (data.length >= 4 && view(data).getUint32(0, true) === 0x04034b50) {
      const normalized = await normalizeBundle(unpackBackup(data, profile), profile);
      const {files, manifest} = normalized;
      selection.normalization.push(...normalized.changes);
      if (selection.serial && manifest.device_serial && selection.serial.toUpperCase() !== manifest.device_serial.toUpperCase()) {
        throw new Error('Die Pakete gehören zu unterschiedlichen Geräten.');
      }
      selection.serial ||= manifest.device_serial;
      inputs = Object.values(AREA_FILES).flat().map(name => files[name]);
    }
    for (let input of inputs) {
      if (input.length === profile.lfi_size) input = normalizeLfi(input, profile, selection.normalization);
      const area = identify(input, profile), hash = await sha256(input);
      if (selection.hashes[area] && selection.hashes[area] !== hash) throw new Error(`Widersprüchliche ${area.toUpperCase()}-Dateien ausgewählt.`);
      selection.components[area] = input;
      selection.hashes[area] = hash;
    }
    selection.names.push(file.name);
  }
  return selection;
}

/** Recheck bytes bound to the user's selection before any USB operation. */
export async function validateSelection(selection, profile) {
  if (!areasOf(selection).length) throw new Error('Keine Firmware ausgewählt.');
  for (const [area, data] of Object.entries(selection.components)) {
    if (identify(data, profile) !== area || await sha256(data) !== selection.hashes[area]) {
      throw new Error('Die ausgewählten Dateien wurden geändert. Bitte erneut auswählen.');
    }
  }
}
