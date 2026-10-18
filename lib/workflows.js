/** User operations; keep verified data, confirmed writes and boot verification distinct. */
import { validateLfi, validateBoot, validateBundle, sha256, assertSame, compareBytes, checkHeader, BUNDLE_FILES } from './formats.js';
import { encodeManifest } from './archive.js';

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
      this.log({event: 'dump_finished', status: manifest.status, steps: manifest.steps});
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
