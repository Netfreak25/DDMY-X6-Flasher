/** X6 RAM driver sequence and boot reader, ported from the existing Actions backend. */
import { assertSame, hex, sha256, validateBoot, view, wordSum } from './formats.js';

const BOOT_CHECKSUM_WINDOW = 0x10000;
const MAX_NAND_PAGE = 0x4000;
const BREC_HEADER_SIZE = 512;
const FWSC_HEADER_ROUTINE = Object.freeze({
  start: 0x11e4c2, end: 0x11e4d4, hex: 'fe21201d03f09ffa6080218801eb40002080',
});

/** Pure boot header decoder: return page rows and exact bytes from the original layout. */
export function bootLayout(mbrec, index) {
  if (![0, 1].includes(index) || mbrec.length !== 1024 || mbrec[2] !== 0x5a) throw new Error('Ungültiger Bootdatensatz.');
  const input = view(mbrec), page = input.getUint16(0x0e, true);
  let blockPages = input.getUint16(0x0c, true);
  if (page < 512 || page > MAX_NAND_PAGE || (page & (page - 1)) || !blockPages) throw new Error('Ungültige NAND-Seitengeometrie.');
  if (blockPages === 0xc0) blockPages = 0x100;
  return { page, row: mbrec[3 + index] * blockPages };
}

export function brecLength(prefix, expected) {
  const input = view(prefix), firstSectors = input.getUint16(4, true), fullSectors = input.getUint16(6, true);
  const checked = Math.min(firstSectors * 512, BOOT_CHECKSUM_WINDOW);
  if (checked < 2 || checked > prefix.length) throw new Error('Ungültige BREC-Prüflänge.');
  const checksum = (wordSum(prefix.subarray(2, checked), 2) + 0x1234) & 0xffff;
  if (input.getUint16(0, true) !== checksum) throw new Error('BREC-Prüfsumme fehlerhaft (bad brec checksum).');
  if (fullSectors < firstSectors || fullSectors * 512 !== expected) throw new Error('BREC-Größe passt nicht zum X6.');
  return fullSectors * 512;
}

/** Undo FWSC's checksum addition in a transfer copy of a finalized NAND dump. */
export async function prepareBrecTransfer(data, driver, profile) {
  if (await sha256(driver) !== profile.runtime_sha256['fwsc.bin']) throw new Error('FWSC-Hash passt nicht zum X6.');
  const base = Number(profile.protocol.work_address);
  if (hex(driver.subarray(FWSC_HEADER_ROUTINE.start - base, FWSC_HEADER_ROUTINE.end - base)) !== FWSC_HEADER_ROUTINE.hex) {
    throw new Error('FWSC-Prüfsummenroutine weicht vom geprüften Code ab.');
  }
  validateBoot(data, 'brec0.bin', profile);
  brecLength(data, profile.brec_size);
  const input = view(data), headerSum = input.getUint16(2, true);
  if (headerSum !== wordSum(data.subarray(4, BREC_HEADER_SIZE), 2)) throw new Error('BREC-Headerprüfsumme fehlerhaft.');
  const result = data.slice();
  view(result).setUint16(0, (input.getUint16(0, true) - 2 * headerSum) & 0xffff, true);
  return result;
}

function words(...values) {
  const result = new Uint8Array(values.length * 4);
  values.forEach((value, index) => view(result).setUint32(index * 4, value, true));
  return result;
}

export class X6Driver {
  constructor(usb, profile, loadPayload, log = () => {}) {
    this.usb = usb;
    this.profile = profile;
    this.loadPayload = loadPayload;
    this.log = log;
    this.payloads = new Map();
    this.bootstrapped = false;
    this.mode = null;
  }

  async payload(name) {
    if (!this.payloads.has(name)) {
      const data = await this.loadPayload(name);
      if (await sha256(data) !== this.profile.runtime_sha256[name]) throw new Error(`RAM-Helfer beschädigt: ${name}`);
      this.payloads.set(name, data);
    }
    return this.payloads.get(name);
  }

  async bootstrap() {
    if (this.bootstrapped) { this.usb.ensureAlive(); return; }
    const address = Number(this.profile.protocol.adfus_address), payload = await this.payload('adfus.bin');
    this.usb.ramBlock = this.profile.sector_size;
    const existing = await this.usb.readRam(address, this.profile.sector_size);
    if (hex(existing) === hex(payload.subarray(0, this.profile.sector_size))) {
      this.bootstrapped = true;
      this.log({event: 'adfus_reused'});
      return;
    }
    const identity = await this.usb.info();
    if (hex(identity.subarray(0, 9)) !== '004341444655443051') throw new Error('Das Gerät ist kein bestätigter ATJ2157-X6.');
    await this.usb.writeRam(address, payload);
    await this.usb.switchTo(address);
    // The Python backend opens a fresh process for the following RAM commands;
    // those use the native default of 512 bytes, not the LFI transfer size.
    this.usb.ramBlock = this.profile.sector_size;
    assertSame(payload.subarray(0, this.profile.sector_size),
      await this.usb.readRam(address, this.profile.sector_size), 'ADFUS-RAM-Prüfung');
    this.bootstrapped = true;
    this.log({event: 'adfus_verified'});
  }

  async flashDriver() {
    await this.bootstrap();
    if (this.mode === 'flash') return;
    const protocol = this.profile.protocol, work = Number(protocol.work_address);
    await this.usb.writeRam(work, await this.payload('nandhwsc.bin'));
    const scan = await this.usb.execute(work, Number(protocol.scan_result_size));
    if (scan.length < 21 || scan[0] !== 0x48 || scan[1] !== 0x57 || hex(scan.subarray(16, 21)) !== this.profile.nand_id) {
      throw new Error('Die NAND-Kennung stimmt nicht mit unserem X6-Profil überein.');
    }
    // Order matters: NANDINFO overlaps the FWSC RAM allocation.
    await this.usb.writeRam(work, await this.payload('fwsc.bin'));
    await this.usb.writeRam(Number(protocol.nandinfo_address), await this.payload('nandinfo.bin'));
    const status = await this.usb.execute(Number(protocol.driver_entry), -1);
    if (new TextDecoder().decode(status.subarray(6, 12)) !== 'NAFWSC') throw new Error('Der X6-Flash-Treiber konnte nicht starten.');
    this.mode = 'flash';
    this.log({event: 'flash_driver_ready', nand_id: this.profile.nand_id});
  }

  async readLfi(progress) {
    await this.flashDriver();
    return this.usb.readLfi(progress);
  }

  async readLfiHeader() {
    await this.flashDriver();
    const header = await this.usb.readLfiHeader();
    if (header.length !== this.profile.sector_size) throw new Error('LFI-Header wurde nicht vollständig gelesen.');
    return header;
  }

  async writeArea(name, data, progress) {
    if (this.mode !== 'flash') throw new Error('Flash-Treiber ist nicht bereit.');
    const sector = this.profile.protocol[`${name}_sector`];
    if (sector === undefined) throw new Error('Unbekannter Firmwarebereich.');
    if (name === 'brec') {
      data = await prepareBrecTransfer(data, await this.payload('fwsc.bin'), this.profile);
      this.log({event: 'brec_transfer_prepared', sha256: await sha256(data)});
    }
    await this.usb.writeFlash(Number(sector), data, progress);
    this.log({event: 'write_acknowledged', area: name, bytes: data.length});
  }

  async finalize() {
    const marker = await this.payload('vram_last.bin');
    const expected = new Uint8Array(this.profile.sector_size);
    expected.set([0xaa, 0xbb], expected.length - 2);
    assertSame(expected, marker, 'Abschlussmarker');
    await this.writeArea('final', marker);
  }

  async readBoot(index, progress = () => {}) {
    await this.bootstrap();
    this.mode = 'boot';
    const config = this.profile.protocol.nandread;
    const code = Number(config.code_address), buffer = Number(config.buffer_address);
    const args = Number(config.args_address), nandArgs = Number(config.nand_args_address);
    const partial = {}, names = index === 0 ? ['mbrec.bin', 'brec0.bin'] : ['mbrec_copy.bin', 'brec1.bin'];
    let initialized = false, failure = null;
    try {
      await this.usb.writeRam(code, await this.payload('nandread.bin'));
      await this.usb.writeRam(args, words(3, buffer));
      await this.usb.execute(code);
      if (view(await this.usb.readRam(args - 8, 4)).getUint32(0, true) !== 0x12345678) throw new Error('MBREC konnte nicht gelesen werden.');
      initialized = true;
      const mbrec = await this.usb.readRam(buffer, this.profile.mbrec_size);
      validateBoot(mbrec, names[0], this.profile);
      partial[names[0]] = mbrec;
      const layout = bootLayout(mbrec, index), output = new Uint8Array(this.profile.brec_size);
      let offset = 0, row = layout.row, pageIndex = 0, limit = BOOT_CHECKSUM_WINDOW;
      while (offset < limit) {
        const size = Math.min(layout.page, limit - offset);
        await this.usb.writeRam(nandArgs, words(row));
        await this.usb.execute(code);
        output.set(await this.usb.readRam(buffer, size), offset);
        offset += size;
        partial[names[1]] = output.subarray(0, offset);
        if (offset === BOOT_CHECKSUM_WINDOW) limit = brecLength(output.subarray(0, offset), this.profile.brec_size);
        progress(offset, this.profile.brec_size);
        if (!mbrec[0x3c0]) row++;
        else if (offset < limit) {
          if (pageIndex >= 0x3c) throw new Error('BREC-Seitentabelle ist zu kurz.');
          row += mbrec[0x3c5 + pageIndex] - mbrec[0x3c4 + pageIndex];
        }
        pageIndex++;
      }
      validateBoot(output, names[1], this.profile);
    } catch (error) {
      failure = error;
    }
    if (initialized && this.usb.alive) {
      try {
        await this.usb.writeRam(args, words(0x80));
        await this.usb.execute(code);
      } catch (error) { failure = error; }
    }
    if (failure) { failure.partial = partial; throw failure; }
    return partial;
  }

  async reset() {
    await this.bootstrap();
    await this.usb.reset();
  }
}
