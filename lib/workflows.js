/** User operations; keep verified data, confirmed writes and boot verification distinct. */
import { validateLfi, validateBoot, validateBundle, sha256, assertSame, compareBytes, checkHeader, BUNDLE_FILES } from './formats.js';
import { encodeManifest } from './archive.js';
import { prepareBrecTransfer } from './driver.js';
import { AREA_FILES, areasOf, hasBoot, validateSelection } from './images.js';

/** Explain the saved manifest instead of hiding which backup area failed. */
export function incompleteDumpMessage(manifest) {
  const labels = {lfi: 'Firmware', brec0: 'Bootkopie 1', brec1: 'Bootkopie 2'};
  const results = Object.entries(labels).map(([name, label]) => {
    const step = manifest.steps?.[name];
    return `${label}: ${step?.status === 'ok' ? 'gültig' : step?.error || 'nicht vollständig gelesen'}.`;
  });
  if (manifest.error) results.push(manifest.error);
  return `${results.join(' ')} Die verfügbaren Daten wurden gespeichert; dieses Archiv ist nicht für Full Restore freigegeben.`;
}

export class X6Operations {
  constructor(driver, profile, {serial = null, progress = () => {}, log = () => {}, restart = async () => false} = {}) {
    this.driver = driver;
    this.profile = profile;
    this.serial = serial;
    this.progress = progress;
    this.log = log;
    this.restart = restart;
    this.busy = false;
  }

  async exclusive(action) {
    if (this.busy) throw new Error('Ein anderer Vorgang läuft bereits.');
    this.busy = true;
    try { return await action(); }
    finally { this.busy = false; }
  }

  phase(name) {
    this.log({event: 'phase', name});
    this.progress(name, 0, 0);
    return (done, total) => this.progress(name, done, total);
  }

  async reboot() {
    this.phase('Neustarten');
    try { return {normalMode: await this.restart(this.driver)}; }
    catch (error) {
      this.log({event: 'restart_unconfirmed', message: error.message});
      return {normalMode: false, restartError: error.message};
    }
  }

  verify(candidate) {
    return this.exclusive(async () => {
      validateLfi(candidate, this.profile);
      const actual = await this.driver.readLfi(this.phase('Geräteinhalt lesen'));
      const result = {...compareBytes(candidate, actual), imageSha256: await sha256(candidate), deviceSha256: await sha256(actual)};
      this.log({event: 'comparison', ...result});
      return result;
    });
  }

  flash(candidate, saveBefore) {
    return this.exclusive(async () => {
      validateLfi(candidate, this.profile);
      const before = await this.driver.readLfi(this.phase('Sichern'));
      validateLfi(before, this.profile);
      checkHeader(candidate, before, this.profile);
      this.phase('Sicherung speichern');
      if (await saveBefore(before) !== true) throw new Error('Die Sicherung wurde nicht bestätigt gespeichert. Es wurde nichts geschrieben.');
      await this.driver.flashDriver();
      await this.driver.writeArea('lfi', candidate, this.phase('Schreiben'));
      await this.driver.finalize();
      const after = await this.driver.readLfi(this.phase('Prüfen'));
      assertSame(candidate, after, 'Firmware-Rückleseprüfung');
      this.log({event: 'lfi_verified', sha256: await sha256(after)});
      return {lfiVerified: true, ...await this.reboot()};
    });
  }

  checkSelectionDevice(selection) {
    if (selection.serial && this.serial && selection.serial.toUpperCase() !== this.serial.toUpperCase()) {
      throw new Error('Die Sicherung gehört zu einem anderen X6.');
    }
  }

  verifySelection(selection) {
    return this.exclusive(async () => {
      await validateSelection(selection, this.profile);
      this.checkSelectionDevice(selection);
      const readback = {}, results = {};
      if (selection.components.lfi) readback['lfi.bin'] = await this.driver.readLfi(this.phase('Geräte-LFI vergleichen'));
      if (hasBoot(selection)) for (const index of [0, 1]) {
        Object.assign(readback, await this.driver.readBoot(index, this.phase(`Bootkopie ${index + 1} vergleichen`)));
      }
      let different = 0;
      for (const area of areasOf(selection)) for (const name of AREA_FILES[area]) {
        const expected = selection.components[area], actual = readback[name];
        if (!actual) throw new Error(`Rücklesung fehlt: ${name}`);
        results[name] = {...compareBytes(expected, actual), imageSha256: await sha256(expected), deviceSha256: await sha256(actual)};
        different += results[name].different;
      }
      const result = {identical: different === 0, different, files: results};
      this.log({event: 'component_comparison', ...result});
      return result;
    });
  }

  /** Full recovery package is required before any selected boot area is written. */
  async backupBeforeBootWrite() {
    const files = {'lfi.bin': await this.driver.readLfi(this.phase('Firmware sichern'))};
    for (const index of [0, 1]) Object.assign(files, await this.driver.readBoot(index, this.phase(`Bootkopie ${index + 1} sichern`)));
    const manifest = {schema: 1, model: this.profile.model, nand_id: this.profile.nand_id, device_serial: this.serial,
      status: 'complete', files: {}, steps: Object.fromEntries(['lfi', 'brec0', 'brec1'].map(name => [name, {status: 'ok'}]))};
    for (const name of BUNDLE_FILES) {
      if (!files[name]) throw new Error(`Rücksicherung unvollständig: ${name}`);
      manifest.files[name] = {size: files[name].length, sha256: await sha256(files[name])};
    }
    await validateBundle({...files, 'manifest.json': encodeManifest(manifest)}, this.profile);
    return {files, manifest};
  }

  async flashSelection(selection, saveBefore) {
    await validateSelection(selection, this.profile);
    this.checkSelectionDevice(selection);
    if (!hasBoot(selection)) return this.flash(selection.components.lfi, saveBefore);
    return this.exclusive(async () => {
      if (!this.profile.boot_write_mapping_verified) throw new Error('Boot-Schreibweg nicht freigegeben. Bitte die Notfall-Wiederherstellung verwenden.');
      const chosen = selection.components;
      // Check the pinned driver's BREC adjustment before any write, including MBREC.
      if (chosen.brec) await prepareBrecTransfer(chosen.brec, await this.driver.payload('fwsc.bin'), this.profile);
      const before = await this.backupBeforeBootWrite();
      if (chosen.lfi) checkHeader(chosen.lfi, before.files['lfi.bin'], this.profile);
      if (chosen.mbrec) assertSame(chosen.mbrec.subarray(3, 16), before.files['mbrec.bin'].subarray(3, 16), 'MBREC-Bootpositionen und NAND-Geometrie');
      this.phase('Rücksicherung speichern');
      if (await saveBefore(before) !== true) throw new Error('Die Rücksicherung wurde nicht bestätigt gespeichert. Es wurde nichts geschrieben.');
      await this.driver.flashDriver(); // Boot reader has overwritten FWSC RAM.
      for (const area of areasOf(selection)) await this.driver.writeArea(area, chosen[area], this.phase(`${area.toUpperCase()} schreiben`));
      await this.driver.finalize();
      if (chosen.lfi) assertSame(chosen.lfi, await this.driver.readLfi(this.phase('LFI prüfen')), 'LFI-Rückleseprüfung');
      const readback = {};
      for (const index of [0, 1]) Object.assign(readback, await this.driver.readBoot(index, this.phase(`Bootkopie ${index + 1} prüfen`)));
      for (const area of ['mbrec', 'brec']) for (const name of AREA_FILES[area]) {
        if (!readback[name]) throw new Error(`Rücklesung fehlt: ${name}`);
        assertSame(chosen[area] || before.files[name], readback[name], `Rückleseprüfung ${name}`);
      }
      const areas = areasOf(selection);
      this.log({event: 'components_verified', areas});
      return {areas, ...await this.reboot()};
    });
  }

  dump() {
    return this.exclusive(async () => {
      const files = {}, manifest = {schema: 1, model: this.profile.model, nand_id: this.profile.nand_id,
        status: 'incomplete', files: {}, steps: {}, created: new Date().toISOString()};
      if (this.serial) manifest.device_serial = this.serial;
      const record = async (name, data) => {
        files[name] = data;
        manifest.files[name] = {size: data.length, sha256: await sha256(data)};
      };
      try {
        const lfi = await this.driver.readLfi(this.phase('Firmware sichern'));
        await record('lfi.bin', lfi);
        validateLfi(lfi, this.profile);
        manifest.steps.lfi = {status: 'ok'};
      } catch (error) {
        if (error.partialLfi?.length) await record('lfi.bin', error.partialLfi);
        manifest.steps.lfi = {status: 'failed', error: error.message};
      }
      for (const index of [0, 1]) {
        const step = `brec${index}`;
        try {
          const copy = await this.driver.readBoot(index, this.phase(`Bootkopie ${index + 1} sichern`));
          for (const [name, data] of Object.entries(copy)) {
            validateBoot(data, name, this.profile);
            await record(name, data);
          }
          manifest.steps[step] = {status: 'ok'};
        } catch (error) {
          for (const [name, data] of Object.entries(error.partial || {})) await record(name, data);
          manifest.steps[step] = {status: 'failed', error: error.message};
        }
      }
      if (Object.values(manifest.steps).every(step => step.status === 'ok')) {
        manifest.status = 'complete';
        try { await validateBundle({...files, 'manifest.json': encodeManifest(manifest)}, this.profile); }
        catch (error) { manifest.status = 'incomplete'; manifest.error = error.message; }
      }
      this.log({event: 'dump_finished', status: manifest.status, steps: manifest.steps, error: manifest.error});
      return {files, manifest};
    });
  }

  restore(files, emergency = false) {
    return this.exclusive(async () => {
      const manifest = await validateBundle(files, this.profile);
      if (!emergency && !this.profile.boot_write_mapping_verified) throw new Error('Für diese Boot-Rücksicherung ist der bestätigte Notfallmodus erforderlich.');
      if (this.serial && manifest.device_serial && this.serial.toUpperCase() !== manifest.device_serial.toUpperCase()) {
        throw new Error('Die Sicherung gehört zu einer anderen Geräteserie.');
      }
      await prepareBrecTransfer(files['brec0.bin'], await this.driver.payload('fwsc.bin'), this.profile);
      await this.driver.flashDriver();
      for (const [area, name, label] of [['mbrec', 'mbrec.bin', 'Bootdatensatz schreiben'],
        ['brec', 'brec0.bin', 'Bootloader schreiben'], ['lfi', 'lfi.bin', 'Firmware schreiben']]) {
        await this.driver.writeArea(area, files[name], this.phase(label));
      }
      await this.driver.finalize();
      assertSame(files['lfi.bin'], await this.driver.readLfi(this.phase('Firmware prüfen')), 'Firmware-Rückleseprüfung');
      this.log({event: 'lfi_verified', sha256: await sha256(files['lfi.bin'])});
      const readback = {}, bootErrors = [];
      for (const index of [0, 1]) {
        try { Object.assign(readback, await this.driver.readBoot(index, this.phase(`Bootkopie ${index + 1} prüfen`))); }
        catch (error) { bootErrors.push(error.message); }
      }
      for (const name of BUNDLE_FILES.filter(name => name !== 'lfi.bin')) {
        if (readback[name]) assertSame(files[name], readback[name], `Rückleseprüfung ${name}`);
        else if (!bootErrors.length) bootErrors.push(`Rücklesung fehlt: ${name}`);
      }
      if (bootErrors.length && !emergency) throw new Error(`Boot-Rücklesung fehlgeschlagen: ${bootErrors.join('; ')}`);
      const bootVerified = bootErrors.length === 0;
      this.log({event: 'restore_result', lfiVerified: true, bootVerified, bootErrors});
      return {lfiVerified: true, bootVerified, bootErrors,
        ...(bootVerified ? await this.reboot() : {normalMode: false})};
    });
  }
}
