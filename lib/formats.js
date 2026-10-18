/** Pure file validation shared by browser workflows and offline regression tests. */
export const BUNDLE_FILES = ['mbrec.bin', 'mbrec_copy.bin', 'brec0.bin', 'brec1.bin', 'lfi.bin'];
export const LFI = Object.freeze({ magic: 0x0ff0aa55, directory: 0x200, end: 0x2000, entry: 32, files: 148 });
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

/** Validate every embedded file before a candidate can reach a write operation. */
export function validateLfi(data, profile) {
  data = bytes(data);
  if (data.length !== profile.lfi_size || data.length < LFI.end) throw new Error('Falsche X6-Firmwaregröße.');
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
    if (!base || entries.has(name) || offset < LFI.end || offset + size > data.length) throw new Error(`Ungültiger Eintrag: ${name}`);
    if (wordSum(data.subarray(offset, offset + size), 4) !== input.getUint32(pos + 0x1c, true)) throw new Error(`Dateiprüfsumme fehlerhaft: ${name}`);
    entries.set(name, { offset, size });
  }
  if (entries.size !== LFI.files) throw new Error(`Erwartet werden ${LFI.files} Dateien im Image.`);
  return entries;
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
export async function validateBundle(files, profile) {
  if (!files['manifest.json']) throw new Error('manifest.json fehlt.');
  const manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
  if (manifest.schema !== 1 || manifest.status !== 'complete') throw new Error('Die Sicherung ist unvollständig.');
  if (manifest.model !== profile.model || manifest.nand_id !== profile.nand_id) throw new Error('Die Sicherung passt nicht zum X6.');
  for (const name of BUNDLE_FILES) {
    const data = files[name], record = manifest.files?.[name];
    if (!data || !record) throw new Error(`Sicherung unvollständig: ${name} fehlt.`);
    if (name === 'lfi.bin') validateLfi(data, profile);
    else validateBoot(data, name, profile);
    if (record.size !== data.length || record.sha256 !== await sha256(data)) throw new Error(`Sicherungs-Prüfsumme fehlerhaft: ${name}`);
  }
  assertSame(files['mbrec.bin'], files['mbrec_copy.bin'], 'MBREC-Kopien');
  assertSame(files['brec0.bin'], files['brec1.bin'], 'BREC-Kopien');
  return manifest;
}
