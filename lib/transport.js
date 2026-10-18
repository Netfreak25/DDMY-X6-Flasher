/** Minimal Actions bulk transport. An uncertain transaction permanently closes this session. */
import { bytes, view } from './formats.js';

const CBW_SIGNATURE = 0x43425355, CSW_SIGNATURE = 0x53425355;
const CBW_SIZE = 31, CSW_SIZE = 13, RECEIVE_SIZE = 1024;
export const CMD = Object.freeze({ FLASH: 0x10, WRITE_RAM: 0x13, READ_RAM: 0x93,
  SWITCH: 0x20, EXEC: 0x21, RET_SIZE: 0x22, READ_RET: 0x23 });
export const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export function usbId(identity) {
  const match = /^VID_([0-9a-f]{4})&PID_([0-9a-f]{4})$/i.exec(identity);
  if (!match) throw new Error('Ungültiges USB-Profil.');
  return { vendorId: parseInt(match[1], 16), productId: parseInt(match[2], 16) };
}

/** Encode the 31-byte, little-endian Actions command wrapper; the tag resets per command. */
export function commandPacket(cdb, dataLength, incoming) {
  const packet = new Uint8Array(CBW_SIZE), output = view(packet);
  output.setUint32(0, CBW_SIGNATURE, true);
  output.setUint32(4, 0, true);
  output.setUint32(8, dataLength, true);
  packet[12] = incoming ? 0x80 : 0;
  packet[14] = 16;
  packet.set(cdb, 15);
  return packet;
}

export class ActionsUSB {
  constructor(device, profile, log = () => {}) {
    this.device = device;
    this.profile = profile;
    this.log = log;
    this.alive = false;
    this.active = false;
    this.pending = new Uint8Array();
    this.ramBlock = profile.sector_size;
  }

  async open() {
    const identity = usbId(this.profile.adfu_usb_id);
    if (this.device.vendorId !== identity.vendorId || this.device.productId !== identity.productId) {
      throw new Error('Bitte den X6 im ADFU-Modus auswählen.');
    }
    this.alive = true;
    await this.transfer(() => this.device.open());
    if (!this.device.configuration) {
      if (this.device.configurations.length !== 1) return this.fail(new Error('USB-Konfiguration nicht eindeutig.'));
      await this.transfer(() => this.device.selectConfiguration(this.device.configurations[0].configurationValue));
    }
    const matches = [];
    for (const item of this.device.configuration.interfaces) {
      for (const alt of item.alternates) {
        const incoming = alt.endpoints.filter(ep => ep.type === 'bulk' && ep.direction === 'in');
        const outgoing = alt.endpoints.filter(ep => ep.type === 'bulk' && ep.direction === 'out');
        if (alt.interfaceClass === 0xff && incoming.length === 1 && outgoing.length === 1) {
          matches.push({ number: item.interfaceNumber, alt: alt.alternateSetting,
            in: incoming[0].endpointNumber, out: outgoing[0].endpointNumber });
        }
      }
    }
    if (matches.length !== 1) return this.fail(new Error('Keine eindeutige ADFU-Schnittstelle gefunden.'));
    this.interface = matches[0];
    await this.transfer(() => this.device.claimInterface(this.interface.number));
    await this.transfer(() => this.device.selectAlternateInterface(this.interface.number, this.interface.alt));
  }

  ensureAlive() {
    if (!this.alive) throw new Error('USB-Sitzung beendet. Gerät erneut verbinden.');
  }

  fail(error) {
    this.alive = false;
    this.log({ event: 'usb_error', message: error.message, command: this.lastCommand });
    // Closing is best-effort cleanup, never a protocol retry or reset command.
    Promise.resolve().then(() => this.device.close()).catch(closeError =>
      this.log({ event: 'usb_close_error', message: closeError.message }));
    throw error;
  }

  async transfer(operation) {
    this.ensureAlive();
    let timer;
    try {
      const result = await Promise.race([operation(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('USB-Zeitüberschreitung. Der Vorgang wurde gestoppt.')), this.profile.usb_timeout_ms);
      })]);
      this.ensureAlive();
      if (result?.status && result.status !== 'ok') throw new Error(`USB-Übertragung fehlgeschlagen: ${result.status}`);
      return result;
    } catch (error) {
      return this.fail(error);
    } finally {
      clearTimeout(timer);
    }
  }

  async send(data) {
    const result = await this.transfer(() => this.device.transferOut(this.interface.out, data));
    if (result.bytesWritten !== data.length) this.fail(new Error('USB-Schreibbestätigung ist unvollständig.'));
  }

  async receive(length) {
    const result = new Uint8Array(length);
    let offset = 0;
    while (offset < length) {
      if (!this.pending.length) {
        const response = await this.transfer(() => this.device.transferIn(this.interface.in, RECEIVE_SIZE));
        if (!response.data?.byteLength) this.fail(new Error('Leere USB-Antwort.'));
        this.pending = new Uint8Array(response.data.buffer, response.data.byteOffset, response.data.byteLength);
      }
      const take = Math.min(this.pending.length, length - offset);
      result.set(this.pending.subarray(0, take), offset);
      this.pending = this.pending.subarray(take);
      offset += take;
    }
    return result;
  }

  async transaction(cdb, dataLength, incoming, payload = null) {
    this.ensureAlive();
    if (this.active) throw new Error('Es läuft bereits ein USB-Befehl.');
    this.active = true;
    this.lastCommand = {cdb: Array.from(cdb, value => value.toString(16).padStart(2, '0')).join(''),
      dataLength, incoming};
    try {
      if (this.pending.length) throw new Error('Unerwartete Restdaten vor USB-Befehl.');
      await this.send(commandPacket(cdb, dataLength, incoming));
      if (!incoming && dataLength) await this.send(payload);
      const response = await this.receive((incoming ? dataLength : 0) + CSW_SIZE);
      const status = view(response.subarray(response.length - CSW_SIZE));
      if (status.getUint32(0, true) !== CSW_SIGNATURE || status.getUint32(4, true) !== 0 ||
          status.getUint32(8, true) !== 0 || status.getUint8(12) !== 0) {
        throw new Error('USB-Befehl wurde nicht gültig bestätigt (Signatur, Tag, Restlänge oder Status).');
      }
      if (this.pending.length) throw new Error('Unerwartete zusätzliche USB-Antwort.');
      return response.subarray(0, incoming ? dataLength : 0);
    } catch (error) {
      return this.fail(error);
    } finally {
      this.active = false;
    }
  }

  command(command, length, address, incoming = false, payload = null) {
    const cdb = new Uint8Array(16), output = view(cdb);
    cdb[0] = 0xcd;
    output.setUint32(1, command, true);
    output.setUint32(5, length, true);
    output.setUint32(9, address >>> 0, true);
    const dataLength = command === CMD.FLASH ? length * this.profile.sector_size : length;
    return this.transaction(cdb, dataLength, incoming, payload);
  }

  info() {
    const cdb = new Uint8Array(16);
    cdb[0] = 0xcc;
    cdb[7] = 0x12;
    return this.transaction(cdb, 0x12, true);
  }

  async writeRam(address, data) {
    for (let offset = 0; offset < data.length; offset += this.ramBlock) {
      const block = data.subarray(offset, offset + this.ramBlock);
      await this.command(CMD.WRITE_RAM, block.length, address + offset, false, block);
    }
  }

  async readRam(address, length) {
    const result = new Uint8Array(length);
    for (let offset = 0; offset < length; offset += this.ramBlock) {
      const size = Math.min(this.ramBlock, length - offset);
      result.set(await this.command(CMD.READ_RAM, size, address + offset, true), offset);
    }
    return result;
  }

  async execute(address, length = 0) {
    await this.command(CMD.EXEC, 0, address);
    if (length === -1) length = view(await this.command(CMD.RET_SIZE, 4, 0, true)).getUint32(0, true);
    if (length < 0 || length > 0x40000) return this.fail(new Error('Ungültige Rückgabelänge des RAM-Helfers.'));
    return length ? this.command(CMD.READ_RET, length, 0, true) : new Uint8Array();
  }

  async switchTo(address) {
    await this.command(CMD.SWITCH, 0, address);
    await delay(10);
    this.ramBlock = this.profile.transfer_size;
  }

  async readLfi(progress = () => {}) {
    const result = new Uint8Array(this.profile.lfi_size);
    let offset = 0;
    try {
      while (offset < result.length) {
        const size = Math.min(this.profile.transfer_size, result.length - offset);
        result.set(await this.command(CMD.FLASH, size / this.profile.sector_size,
          (0x80000000 + offset / this.profile.sector_size) >>> 0, true), offset);
        offset += size;
        progress(offset, result.length);
      }
    } catch (error) {
      // Keep only fully acknowledged blocks, never zero-fill an incomplete dump.
      if (offset) error.partialLfi = result.slice(0, offset);
      throw error;
    }
    return result;
  }

  async writeFlash(sector, data, progress = () => {}) {
    data = bytes(data);
    if (!data.length || data.length % this.profile.sector_size) throw new Error('Ungültige Flash-Datenlänge.');
    for (let offset = 0; offset < data.length; offset += this.profile.transfer_size) {
      const block = data.subarray(offset, offset + this.profile.transfer_size);
      await this.command(CMD.FLASH, block.length / this.profile.sector_size,
        (sector + offset / this.profile.sector_size) >>> 0, false, block);
      progress(offset + block.length, data.length);
      await delay(this.profile.write_delay_ms);
    }
  }

  async reset() {
    const cdb = new Uint8Array(16);
    cdb[0] = 0xb0;
    await this.transaction(cdb, 0, false);
    await this.close();
  }

  async close() {
    this.alive = false;
    await this.device.close();
  }
}
