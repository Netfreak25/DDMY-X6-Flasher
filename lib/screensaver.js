/** Pixel-only import treatment. Code recognition never upgrades old firmware. */
import { sha256, view } from './formats.js';

export const REVISION = 'v7-catalog-compositor';
export const CATALOG = Object.freeze({offset: 0x40, entries: 0x50, end: 0x400, capacity: 236, imageBytes: 240 * 320 * 2});

export async function supported(data, entries, profile) {
  if (!profile || profile.revision !== REVISION || !Object.keys(profile.supported_code || {}).length) return false;
  for (const [name, expected] of Object.entries(profile.supported_code)) {
    const entry = entries.get(name);
    const allowed = Array.isArray(expected) ? expected : [expected];
    if (!entry || !allowed.includes(await sha256(data.subarray(entry.offset, entry.offset + entry.size)))) return false;
  }
  return true;
}

/** Mutable wallpaper payloads are never used as firmware fingerprints. */
export function catalogOffsets(style) {
  if (style.length < CATALOG.end) throw new Error('Screensaver-Bilderkatalog fehlt.');
  const input = view(style), at = CATALOG.offset, count = input.getUint16(at + 6, true);
  if (input.getUint32(at, true) !== 0x43533658 || input.getUint16(at + 4, true) !== 1 ||
      input.getUint32(at + 8, true) !== style.length || input.getUint32(at + 12, true) || count < 1 || count > CATALOG.capacity) {
    throw new Error('Ungültiger Screensaver-Bilderkatalog.');
  }
  const offsets = Array.from({length: count}, (_, i) => input.getUint32(CATALOG.entries + i * 4, true));
  if (offsets[0] || new Set(offsets).size !== count || offsets.slice(1).some(offset => offset < CATALOG.end || offset % 2 || offset + CATALOG.imageBytes > style.length)) {
    throw new Error('Ungültige Screensaver-Bildoffsets.');
  }
  return offsets;
}

export function configCountOffset(config) {
  const descriptor = 16 + 7 * 8, input = view(config);
  if (config.length < descriptor + 8 || input.getUint32(0, true) !== 0x676663) throw new Error('Ungültige Screensaver-Konfiguration.');
  const offset = input.getUint32(descriptor, true), length = input.getUint32(descriptor + 4, true);
  if (length !== 8 || offset < descriptor + 8 || offset + length > config.length) throw new Error('Ungültige Screensaver-Anzahl.');
  return offset;
}

/** Integer smoothstep, identical to the Python preview/importer. */
export function curve(y, points) {
  for (let i = 1; i < points.length; i++) {
    const [start, low] = points[i - 1], [end, high] = points[i];
    if (start <= y && y <= end) {
      const length = end - start, position = y - start;
      const denominator = length ** 3, smooth = 3 * position ** 2 * length - 2 * position ** 3;
      return Math.floor((low * denominator + (high - low) * smooth) / denominator);
    }
  }
  return 255;
}

export function limits(profile, slot) {
  if (!Number.isInteger(slot) || slot < 1 || slot > profile.background_count) throw new Error('Unbekannter Hintergrundplatz.');
  const shift = slot === 1 ? profile.first_slot_shift_y : 0;
  const upper = profile.upper.map(([y, value]) => [y + shift, value]);
  return Array.from({length: profile.height}, (_, y) => Math.min(curve(y, upper), curve(y, profile.lower)));
}

export function limitPixel(word, ceiling) {
  const channels = [word >>> 11, word >>> 5 & 63, word & 31], maxima = [31, 63, 31];
  let peak = channels[0], peakMax = maxima[0];
  for (let i = 1; i < channels.length; i++) {
    if (channels[i] * peakMax > peak * maxima[i]) { peak = channels[i]; peakMax = maxima[i]; }
  }
  if (peak * 255 <= ceiling * peakMax) return word;
  const numerator = ceiling * peakMax, denominator = 255 * peak;
  const [red, green, blue] = channels.map(value => Math.floor(value * numerator / denominator));
  return red << 11 | green << 5 | blue;
}

/** Never reads a previously rendered preview: input is the final crop's RGB565. */
export function processBackground(raw, profile, slot) {
  if (raw.length !== profile.width * profile.height * 2) throw new Error('Unvollständiges RGB565-Hintergrundbild.');
  const rows = limits(profile, slot), input = view(raw), output = new Uint8Array(raw.length), words = view(output);
  for (let i = 0; i < raw.length; i += 2) words.setUint16(i, limitPixel(input.getUint16(i, true), rows[Math.floor(i / 2 / profile.width)]), true);
  return output;
}
