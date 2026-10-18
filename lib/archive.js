/** Flat, bounded ZIP archives compatible with the existing toolkit's backup folders. */
import { zipSync, unzipSync } from '../vendor/fflate.js';
import { BUNDLE_FILES } from './formats.js';

export const encodeManifest = manifest => new TextEncoder().encode(JSON.stringify(manifest, null, 2) + '\n');

export function packBackup(files, manifest) {
  const selected = Object.fromEntries(BUNDLE_FILES.filter(name => files[name]).map(name => [name, files[name]]));
  selected['manifest.json'] = encodeManifest(manifest);
  return zipSync(selected, {level: 0});
}

export function unpackBackup(data, profile) {
  const seen = new Set();
  if (data.length > profile.lfi_size * 2) throw new Error('Sicherungsarchiv ist zu groß.');
  return unzipSync(data, {filter: entry => {
    if (!BUNDLE_FILES.includes(entry.name) && entry.name !== 'manifest.json') return false;
    if (seen.has(entry.name)) throw new Error(`Doppelter Archiveintrag: ${entry.name}`);
    seen.add(entry.name);
    const limit = entry.name === 'manifest.json' ? 65536 : entry.name === 'lfi.bin' ? profile.lfi_size :
      entry.name.startsWith('mbrec') ? profile.mbrec_size : profile.brec_size;
    if (entry.originalSize > limit) throw new Error(`Archiveintrag zu groß: ${entry.name}`);
    return true;
  }});
}

export async function importFolder(selection, profile) {
  const result = {};
  const limits = { 'manifest.json': 65536, 'lfi.bin': profile.lfi_size,
    'mbrec.bin': profile.mbrec_size, 'mbrec_copy.bin': profile.mbrec_size,
    'brec0.bin': profile.brec_size, 'brec1.bin': profile.brec_size };
  for (const file of selection) {
    if (!(file.name in limits)) continue;
    if (file.size > limits[file.name] || result[file.name]) throw new Error(`Ungültige oder doppelte Sicherungsdatei: ${file.name}`);
    result[file.name] = new Uint8Array(await file.arrayBuffer());
  }
  return result;
}
