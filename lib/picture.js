/** Bounded image decoding stays entirely in the browser; no helper upload. */
import { view } from './formats.js';
import { decodeRgb565, encodeRgb565 } from './artwork.js';

const ascii = (data, start, length) => new TextDecoder().decode(data.subarray(start, start + length));

/** Read dimensions and animation markers before allocating a decoded bitmap. */
export function pictureInfo(data, config) {
  if (data.length > config.max_input_bytes) throw new Error('Das Bild darf höchstens 25 MiB groß sein.');
  const input = view(data);
  let width = 0, height = 0, type;
  if (data.length >= 33 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, i) => data[i] === value)) {
    type = 'image/png';
    if (ascii(data, 12, 4) !== 'IHDR') throw new Error('PNG-Bildkopf fehlt.');
    width = input.getUint32(16); height = input.getUint32(20);
    let end = false;
    for (let pos = 8; pos + 12 <= data.length;) {
      const size = input.getUint32(pos), chunk = ascii(data, pos + 4, 4);
      if (pos + size + 12 > data.length) throw new Error('Unvollständiges PNG.');
      if (chunk === 'acTL') throw new Error('Animierte Bilder werden nicht unterstützt.');
      if (chunk === 'IEND') { end = true; break; }
      pos += size + 12;
    }
    if (!end) throw new Error('Unvollständiges PNG.');
  } else if (data.length >= 12 && ascii(data, 0, 4) === 'RIFF' && ascii(data, 8, 4) === 'WEBP') {
    type = 'image/webp';
    if (input.getUint32(4, true) + 8 !== data.length) throw new Error('Unvollständiges WebP.');
    for (let pos = 12; pos + 8 <= data.length;) {
      const size = input.getUint32(pos + 4, true), chunk = ascii(data, pos, 4), start = pos + 8;
      if (start + size > data.length) throw new Error('Unvollständiges WebP.');
      if (chunk === 'ANIM' || chunk === 'ANMF' || (chunk === 'VP8X' && data[start] & 2)) throw new Error('Animierte Bilder werden nicht unterstützt.');
      if (chunk === 'VP8X' && size >= 10) {
        width = 1 + data[start + 4] + (data[start + 5] << 8) + (data[start + 6] << 16);
        height = 1 + data[start + 7] + (data[start + 8] << 8) + (data[start + 9] << 16);
      } else if (chunk === 'VP8L' && size >= 5 && data[start] === 0x2f && !width) {
        const bits = input.getUint32(start + 1, true);
        width = 1 + (bits & 0x3fff); height = 1 + (bits >>> 14 & 0x3fff);
      } else if (chunk === 'VP8 ' && size >= 10 && [0x9d, 1, 0x2a].every((value, i) => data[start + 3 + i] === value) && !width) {
        width = input.getUint16(start + 6, true) & 0x3fff; height = input.getUint16(start + 8, true) & 0x3fff;
      }
      pos = start + size + (size & 1);
    }
  } else if (data.length >= 4 && data[0] === 0xff && data[1] === 0xd8) {
    type = 'image/jpeg';
    let pos = 2;
    while (pos + 4 <= data.length) {
      if (data[pos++] !== 0xff) throw new Error('Ungültiger JPEG-Bildkopf.');
      while (data[pos] === 0xff) pos++;
      const marker = data[pos++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 1 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (pos + 2 > data.length) throw new Error('Unvollständiges JPEG.');
      const size = input.getUint16(pos);
      if (size < 2 || pos + size > data.length) throw new Error('Unvollständiges JPEG.');
      if ([0xc0, 0xc1, 0xc2].includes(marker) && size >= 8) { height = input.getUint16(pos + 3); width = input.getUint16(pos + 5); }
      pos += size;
    }
  }
  if (!width || !height || !type) throw new Error('Bitte ein gültiges statisches PNG-, JPEG- oder WebP-Bild wählen.');
  if (width * height > config.max_input_pixels) throw new Error('Das Bild darf höchstens 40 Megapixel enthalten.');
  return {width, height, type};
}

export async function importPicture(file, config) {
  if (file.size > config.max_input_bytes) throw new Error('Das Bild darf höchstens 25 MiB groß sein.');
  const data = new Uint8Array(await file.arrayBuffer()), info = pictureInfo(data, config);
  const bitmap = await window.createImageBitmap(new Blob([data], {type: info.type}), {imageOrientation: 'from-image', colorSpaceConversion: 'default'});
  try {
    if (bitmap.width * bitmap.height > config.max_input_pixels || bitmap.width * bitmap.height !== info.width * info.height) throw new Error('Abweichende Bildabmessungen.');
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    const context = canvas.getContext('2d', {colorSpace: 'srgb'});
    context.fillStyle = 'black'; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0);
    return canvas;
  } finally { bitmap.close(); }
}

export function geometry(width, height, config, {mode = 'cover', zoom = 1, panX = 0, panY = 0} = {}) {
  if (!['cover', 'contain'].includes(mode) || ![zoom, panX, panY].every(Number.isFinite) ||
      zoom < 1 || zoom > config.max_zoom || Math.abs(panX) > 1 || Math.abs(panY) > 1 || Math.min(width, height) < 1) throw new Error('Ungültiger Bildausschnitt.');
  const scale = (mode === 'cover' ? Math.max : Math.min)(config.width / width, config.height / height) * zoom;
  const w = width * scale, h = height * scale;
  return {x: (config.width - w) / 2 + panX * Math.abs(config.width - w) / 2,
    y: (config.height - h) / 2 + panY * Math.abs(config.height - h) / 2, width: w, height: h};
}

/** Draw in sRGB over black, then expose packed-and-decoded RGB565 preview pixels. */
export function renderPicture(source, config, options = {}) {
  const rotation = options.rotation || 0;
  if (![0, 90, 180, 270].includes(rotation)) throw new Error('Nur Drehungen in 90°-Schritten werden unterstützt.');
  const rotated = document.createElement('canvas'), swapped = rotation % 180 !== 0;
  rotated.width = swapped ? source.height : source.width; rotated.height = swapped ? source.width : source.height;
  const turn = rotated.getContext('2d', {colorSpace: 'srgb'});
  turn.translate(rotated.width / 2, rotated.height / 2); turn.rotate(rotation * Math.PI / 180);
  turn.drawImage(source, -source.width / 2, -source.height / 2);
  const canvas = document.createElement('canvas'); canvas.width = config.width; canvas.height = config.height;
  const context = canvas.getContext('2d', {colorSpace: 'srgb'}), box = geometry(rotated.width, rotated.height, config, options);
  context.fillStyle = 'black'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingQuality = 'high'; context.drawImage(rotated, box.x, box.y, box.width, box.height);
  const rgba = context.getImageData(0, 0, canvas.width, canvas.height, {colorSpace: 'srgb'}).data;
  const rgb = new Uint8Array(config.width * config.height * 3);
  for (let i = 0; i < rgb.length / 3; i++) rgb.set(rgba.subarray(i * 4, i * 4 + 3), i * 3);
  return rgb;
}

export function drawRgb(canvas, rgb, config) {
  canvas.width = config.width; canvas.height = config.height;
  const context = canvas.getContext('2d', {colorSpace: 'srgb'}), image = context.createImageData(config.width, config.height);
  for (let i = 0; i < rgb.length / 3; i++) { image.data.set(rgb.subarray(i * 3, i * 3 + 3), i * 4); image.data[i * 4 + 3] = 255; }
  context.putImageData(image, 0, 0);
}

export const quantized = rgb => decodeRgb565(encodeRgb565(rgb, 'little'), 'little');
