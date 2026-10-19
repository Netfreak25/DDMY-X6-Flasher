/** Pure file validation shared by browser workflows and offline regression tests. */
export const BUNDLE_FILES = ['mbrec.bin', 'mbrec_copy.bin', 'brec0.bin', 'brec1.bin', 'lfi.bin'];
export const COMPONENT_LABELS = Object.freeze({'mbrec.bin': 'MBREC', 'mbrec_copy.bin': 'MBREC-Kopie',
  'brec0.bin': 'BREC 0', 'brec1.bin': 'BREC 1', 'lfi.bin': 'LFI'});
export const LFI = Object.freeze({ magic: 0x0ff0aa55, directory: 0x200, end: 0x2000, entry: 32, files: 148 });
const CATALOG = Object.freeze({offset: 0x40, table: 0x50, end: 0x400, maximum: 236, pixels: 240 * 320 * 2});
export const bytes = data => data instanceof Uint8Array ? data : new Uint8Array(data);
export const view = data => new DataView(data.buffer, data.byteOffset, data.byteLength);
export const hex = data => Array.from(data, value => value.toString(16).padStart(2, '0')).join('');
export const sha256 = async data => hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes(data))));

/** The vendor uses little-endian additive checksums, not CRC32. */
export function wordSum(data, width) {
  if (![2, 4].includes(width) || data.length % width) throw new Error('Ungültige Prüfsummenlänge.');
  const input = view(data);
  let sum = 0;
  for (let offset = 0; offset < data.length; offset += width) {
    sum = (sum + (width === 2 ? input.getUint16(offset, true) : input.getUint32(offset, true))) >>> 0;
  }
  return width === 2 ? sum & 0xffff : sum;
}

/** Read checksum-authenticated entries before checking physical allocation. */
function readLfi(data, profile) {
  data = bytes(data);
  if (data.length !== profile.lfi_size || data.length < LFI.end || data.length % profile.sector_size) throw new Error('Falsche X6-Firmwaregröße.');
  const input = view(data);
  if (input.getUint32(0, true) !== LFI.magic) throw new Error('Die Datei ist kein X6-LFI-Image.');
  if (wordSum(data.subarray(0, 0x1fe), 2) !== input.getUint16(0x1fe, true)) throw new Error('Header-Prüfsumme fehlerhaft.');
  if (wordSum(data.subarray(LFI.directory, LFI.end), 4) !== input.getUint32(0x10, true)) throw new Error('Verzeichnis-Prüfsumme fehlerhaft.');
  const entries = new Map();
  for (let pos = LFI.directory; pos < LFI.end && data[pos]; pos += LFI.entry) {
    const raw = data.subarray(pos, pos + 11);
    if (raw.some(value => value < 32 || value > 126)) throw new Error('Ungültiger Dateiname im Image.');
    const base = new TextDecoder().decode(raw.subarray(0, 8)).trimEnd();
    const ext = new TextDecoder().decode(raw.subarray(8)).trimEnd();
    const name = base + (ext ? `.${ext}` : '');
    const offset = input.getUint32(pos + 0x10, true) * profile.sector_size;
    const size = input.getUint32(pos + 0x14, true);
    if (!base || entries.has(name) || offset < LFI.end || !size || size % 4 || offset + size > data.length) throw new Error(`Ungültiger Eintrag: ${name}`);
    if (wordSum(data.subarray(offset, offset + size), 4) !== input.getUint32(pos + 0x1c, true)) throw new Error(`Dateiprüfsumme fehlerhaft: ${name}`);
    entries.set(name, { offset, size, directoryOffset: pos });
  }
  if (!entries.size) throw new Error('Leeres LFI-Verzeichnis.');
  return entries;
}

/** Physical payload ownership must be disjoint; directory order is not assumed. */
function allocations(entries, size) {
  const ordered = [...entries].sort((a, b) => a[1].offset - b[1].offset);
  if (ordered[0][1].offset !== LFI.end) throw new Error('Nicht zugeordneter Bereich vor der ersten LFI-Datei.');
  return ordered.map(([name, entry], index) => {
    const end = ordered[index + 1]?.[1].offset ?? size;
    if (entry.offset + entry.size > end) throw new Error(`Überlappende LFI-Datei: ${name}`);
    return {name, ...entry, end};
  });
}

/** Validate payload checksums and the original BREC's sector-size invariant. */
export function validateLfi(data, profile) {
  data = bytes(data);
  const entries = readLfi(data, profile);
  for (const entry of allocations(entries, data.length)) {
    if (entry.offset + entry.size !== entry.end || entry.size % profile.sector_size) {
      throw new Error(`LFI-Größenfehler: nicht zugeordnetes Padding nach ${entry.name}.`);
    }
  }
  const sectors = LFI.end / profile.sector_size + [...entries.values()].reduce((sum, entry) => sum + Math.floor(entry.size / profile.sector_size), 0);
  if (sectors !== data.length / profile.sector_size) throw new Error('LFI-Größe wird vom Bootloader abgelehnt.');
  return entries;
}

/** Refresh only a fully recognized catalog; image bytes and offsets stay intact. */
function extendCatalog(data, entry, length) {
  const header = view(data), start = entry.offset;
  if (entry.name !== 'COMMON.STY' || entry.size < CATALOG.end || header.getUint32(start, true) !== 0x19335552) return false;
  const at = start + CATALOG.offset, count = header.getUint16(at + 6, true);
  if (header.getUint32(at, true) !== 0x43533658 || header.getUint16(at + 4, true) !== 1 ||
      header.getUint32(at + 8, true) !== entry.size || header.getUint32(at + 12, true) || count < 1 || count > CATALOG.maximum) return false;
  const offsets = Array.from({length: count}, (_, index) => header.getUint32(start + CATALOG.table + index * 4, true));
  if (offsets[0] || data.subarray(start + CATALOG.table + count * 4, start + CATALOG.end).some(value => value !== 0)) return false;
  let previousEnd = CATALOG.end;
  for (const offset of offsets.slice(1).sort((a, b) => a - b)) {
    if (offset % 2 || offset < previousEnd || offset + CATALOG.pixels > entry.size) return false;
    previousEnd = offset + CATALOG.pixels;
  }
  header.setUint32(at + 8, length, true);
  return true;
}

/** Clear only unowned padding, retain all named payloads, and repair allocation.
 * Input checksums must be valid. Callers retain imported source bytes separately.
 * Changes describe every assigned gap, including previously nonzero padding.
 */
export function normalizeLfi(data, profile, changes = []) {
  data = bytes(data);
  const entries = readLfi(data, profile), output = new Uint8Array(data), header = view(output);
  let changed = false;
  for (const entry of allocations(entries, data.length)) {
    const start = entry.offset + entry.size;
    if (start === entry.end) continue;
    let cleared = 0;
    for (let offset = start; offset < entry.end; offset++) if (data[offset]) cleared++;
    output.fill(0, start, entry.end);
    const catalogUpdated = extendCatalog(output, entry, entry.end - entry.offset);
    header.setUint32(entry.directoryOffset + 0x14, entry.end - entry.offset, true);
    header.setUint32(entry.directoryOffset + 0x1c, wordSum(output.subarray(entry.offset, entry.end), 4), true);
    changes.push({file: entry.name, offset: start, padding_bytes: entry.end - start,
      cleared_nonzero_bytes: cleared, length_before: entry.size, length_after: entry.end - entry.offset,
      catalog_size_updated: catalogUpdated});
    changed = true;
  }
  if (changed) {
    header.setUint32(0x10, wordSum(output.subarray(LFI.directory, LFI.end), 4), true);
    header.setUint16(0x1fe, wordSum(output.subarray(0, 0x1fe), 2), true);
  }
  validateLfi(output, profile);
  return changed ? output : data;
}

export function validateBoot(data, name, profile) {
  const mbrec = name.startsWith('mbrec');
  if (data.length !== (mbrec ? profile.mbrec_size : profile.brec_size)) throw new Error(`Falsche Größe: ${name}`);
  if (mbrec && data[2] !== 0x5a) throw new Error('Ungültiger MBREC.');
  if (!mbrec && hex(data.subarray(0x80, 0x85)) !== profile.nand_id) throw new Error('BREC gehört zu einem anderen NAND-Chip.');
}

/** Count all differences; device contents need not be structurally valid for comparison. */
export function compareBytes(expected, actual) {
  const size = Math.max(expected.length, actual.length);
  let different = 0, first = null;
  for (let offset = 0; offset < size; offset++) {
    if (expected[offset] !== actual[offset]) {
      different++;
      if (first === null) first = offset;
    }
  }
  return { identical: different === 0, different, first, expectedSize: expected.length, actualSize: actual.length };
}

export function assertSame(expected, actual, label) {
  const result = compareBytes(expected, actual);
  if (!result.identical) throw new Error(`${label}: ${result.different} abweichende Bytes, zuerst bei 0x${result.first.toString(16)}.`);
}

export function checkHeader(candidate, current, profile) {
  for (const [start, end] of profile.protocol.preserve_header_ranges) {
    assertSame(candidate.subarray(start, end), current.subarray(start, end), 'Gerätespezifischer Firmware-Header');
  }
}

/** Keep schema 1 compatible with the existing Python toolkit. */
async function checkBundle(files, profile, allowPadding = false, allowPartial = false) {
  if (!files['manifest.json']) throw new Error('manifest.json fehlt.');
  const manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  const partial = allowPartial && manifest.status === 'partial';
  if (manifest.schema !== 1 || manifest.status !== 'complete' && !partial) throw new Error('Die Sicherung ist unvollständig.');
  if (manifest.model !== profile.model || manifest.nand_id !== profile.nand_id) throw new Error('Die Sicherung passt nicht zum X6.');
  const names = partial ? manifest.requested_components : BUNDLE_FILES;
  if (!Array.isArray(names) || !names.length || new Set(names).size !== names.length ||
      names.some(name => !BUNDLE_FILES.includes(name)) || !manifest.files ||
      Object.keys(manifest.files).length !== names.length || names.some(name => !manifest.files[name])) {
    throw new Error('Ungültige Auswahl der Sicherungsbereiche.');
  }
  for (const name of names) {
    const data = files[name], record = manifest.files?.[name];
    if (!data || !record) throw new Error(`Sicherung unvollständig: ${name} fehlt.`);
    if (name === 'lfi.bin') validateLfi(allowPadding ? normalizeLfi(data, profile) : data, profile);
    else validateBoot(data, name, profile);
    if (record.size !== data.length || record.sha256 !== await sha256(data)) throw new Error(`Sicherungs-Prüfsumme fehlerhaft: ${name}`);
  }
  if (names.includes('mbrec.bin') && names.includes('mbrec_copy.bin')) assertSame(files['mbrec.bin'], files['mbrec_copy.bin'], 'MBREC-Kopien');
  if (names.includes('brec0.bin') && names.includes('brec1.bin')) assertSame(files['brec0.bin'], files['brec1.bin'], 'BREC-Kopien');
  return manifest;
}

/** Validate captured bytes without modifying or repairing a backup. */
export async function validateBundle(files, profile, {allowPartial = false} = {}) {
  return checkBundle(files, profile, false, allowPartial);
}

/** Authenticate raw imports before creating an independently hash-bound package. */
export async function normalizeBundle(files, profile, {allowPartial = false} = {}) {
  const originalManifest = await checkBundle(files, profile, true, allowPartial), changes = [];
  if (!files['lfi.bin']) return {files, manifest: originalManifest, originals: files, changes};
  const lfi = normalizeLfi(files['lfi.bin'], profile, changes);
  const output = {...files, 'lfi.bin': lfi}, manifest = JSON.parse(JSON.stringify(originalManifest));
  if (changes.length) {
    manifest.hardware_verified = false;
    manifest.kind = 'firmware_candidate';
    manifest.lfi_normalization = {source_sha256: manifest.files['lfi.bin'].sha256, changes};
    manifest.files['lfi.bin'] = {size: lfi.length, sha256: await sha256(lfi)};
    output['manifest.json'] = new TextEncoder().encode(JSON.stringify(manifest, null, 2) + '\n');
  }
  await validateBundle(output, profile, {allowPartial});
  return {files: output, manifest, originals: files, changes};
}
