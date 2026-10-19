/** Offline gallery; all device actions use the existing injected workflows. */
import { Artwork, decodeRgb565 } from './artwork.js';
import { selectImages, AREA_FILES } from './images.js';
import { normalizeBundle, validateBundle, assertSame } from './formats.js';
import { importFolder, packBackup, unpackBackup } from './archive.js';
import { importPicture, renderPicture, drawRgb, geometry } from './picture.js';

const $ = id => document.getElementById(id);

export async function selectionFromBackup(files, profile) {
  const normalized = await normalizeBundle(files, profile), manifest = normalized.manifest;
  const sources = Object.values(AREA_FILES).flat().map(name => ({name, size: normalized.files[name].length, arrayBuffer: async () => normalized.files[name]}));
  const selection = await selectImages(sources, profile);
  selection.originals = Object.entries(normalized.originals).map(([name, data]) => ({name, data}));
  selection.normalization = [...(selection.normalization || []), ...normalized.changes];
  selection.serial = manifest.device_serial || null;
  return selection;
}

export class ArtworkPanel {
  constructor(profile, config, host) {
    this.profile = profile; this.config = config; this.host = host;
    this.model = null; this.active = null; this.dirty = false; this.sources = new Map(); this.tiles = new Map();
    for (const slot of config.slots) {
      const button = document.createElement('button');
      button.className = 'artwork-tile'; button.dataset.slot = slot.id;
      const canvas = document.createElement('canvas'); canvas.width = config.width; canvas.height = config.height;
      const label = document.createElement('span'); label.textContent = slot.label;
      const status = document.createElement('small'); status.textContent = 'Nicht geladen';
      button.append(canvas, label, status); button.addEventListener('click', () => this.select(slot.id));
      $('artwork-gallery').append(button); this.tiles.set(slot.id, {button, canvas, status});
    }
    $('artwork-toggle').addEventListener('click', () => {
      $('artwork-panel').hidden = !$('artwork-panel').hidden;
      $('artwork-toggle').setAttribute('aria-expanded', String(!$('artwork-panel').hidden));
    });
    for (const [id, folder] of [['artwork-files', false], ['artwork-folder', true]]) $(id).addEventListener('change', event => {
      const sources = [...event.target.files]; event.target.value = '';
      if (sources.length) void this.loadSources(sources, folder);
    });
    $('artwork-use-selection').addEventListener('click', () => void host.run('Bilder laden', async () => {
      if (await this.discardAllowed()) await this.loadSelection(host.selection());
    }, false));
    $('artwork-device').addEventListener('click', () => void host.run('Sicherung für den Bildeditor erstellen', async () => {
      if (!await this.discardAllowed()) return;
      const files = await host.dump();
      if (files) await this.loadSelection(await selectionFromBackup(files, profile));
    }));
    $('artwork-coupled').addEventListener('change', event => { this.model.coupled = event.target.checked; });
    $('artwork-add').addEventListener('click', () => $('artwork-add-picture').click());
    $('artwork-add-picture').addEventListener('change', event => {
      const file = event.target.files[0]; event.target.value = '';
      if (file) void host.run('Zusatzbild einlesen', async () => this.crop(await importPicture(file, config), {}, true), false);
    });
    $('artwork-delete').addEventListener('click', () => void host.run('Zusatzbild löschen', async () => {
      if (!await host.confirm('Zusatzbild löschen?', 'Das ausgewählte Zusatzbild aus der Arbeitskopie entfernen?')) return;
      const remap = this.model.deleteBackground(this.active);
      this.sources = new Map([...this.sources].filter(([id]) => remap.has(id)).map(([id, value]) => [remap.get(id), value]));
      this.active = 'background-01'; this.dirty = this.model.hasChanges; this.refresh();
    }, false));
    $('artwork-replace').addEventListener('click', () => $('artwork-picture').click());
    $('artwork-picture').addEventListener('change', event => {
      const file = event.target.files[0]; event.target.value = '';
      if (file) void host.run('Bild einlesen', async () => this.crop(await importPicture(file, config)), false);
    });
    $('artwork-edit').addEventListener('click', () => {
      if (this.sources.has(this.active)) { const {source, options} = this.sources.get(this.active); this.crop(source, options); }
      else { const source = document.createElement('canvas'); drawRgb(source, this.model.rgb(this.active), config); this.crop(source); }
    });
    $('artwork-reset').addEventListener('click', () => {
      for (const id of this.model.targets(this.active)) this.sources.delete(id);
      this.model.reset(this.active); this.dirty = this.model.hasChanges; this.refresh();
    });
    $('artwork-save').addEventListener('click', () => void this.saveBins());
    $('artwork-package').addEventListener('click', () => void this.savePackage());
    $('artwork-flash').addEventListener('click', () => void host.run('Bildänderungen flashen', async () => {
      const labels = [...this.model.ranges.values()].filter(item => this.model.edits.has(item.slot.id)).map(item => item.slot.label);
      if (this.model.workingLfi) labels.push('Bilderkatalog / Zusatzbilder');
      await host.flash(await this.model.changedSelection(), labels);
    }));
    this.bindCrop(); this.update();
  }

  async discardAllowed() {
    const allowed = !this.dirty || await this.host.confirm('Bildänderungen verwerfen?', 'Die noch nicht gespeicherten Bildänderungen verwerfen?');
    if (!allowed) this.host.result('Abgebrochen', 'Die bisherigen Bildänderungen bleiben erhalten.');
    return allowed;
  }

  async loadSources(sources, folder) {
    await this.host.run('Bilder laden', async () => {
      if (!await this.discardAllowed()) return;
      const selection = folder ? await selectionFromBackup(await importFolder(sources, this.profile), this.profile) : await selectImages(sources, this.profile);
      await this.loadSelection(selection);
    }, false);
  }

  async loadSelection(selection) {
    const model = await Artwork.create(selection, this.profile, this.config);
    this.model = model; this.sources.clear(); this.dirty = model.hasChanges; this.active = model.ranges.keys().next().value;
    $('artwork-coupled').checked = model.coupled;
    $('artwork-panel').hidden = false; $('artwork-toggle').setAttribute('aria-expanded', 'true');
    this.refresh();
    this.host.result('Bilder geladen.', 'Die Arbeitsgrundlage bleibt unverändert. Wähle einen Bildplatz aus.', 'success');
  }

  update() {
    const busy = this.host.busy(), loaded = Boolean(this.model), changed = Boolean(this.model?.hasChanges);
    for (const control of document.querySelectorAll('[data-art-control]')) control.disabled = busy;
    $('artwork-use-selection').disabled ||= !this.host.selection();
    $('artwork-device').disabled ||= !this.host.connected();
    const fixed = this.model?.ranges.get(this.active)?.slot.fixed_black;
    for (const id of ['artwork-replace', 'artwork-edit']) $(id).disabled ||= !loaded || fixed;
    $('artwork-reset').disabled ||= fixed || !this.model?.edits.has(this.active) && !this.model?.contour;
    $('artwork-coupled').disabled ||= !this.config.startup_ids.every(id => this.model?.ranges.has(id));
    for (const id of ['artwork-save', 'artwork-package', 'artwork-flash']) $(id).disabled ||= !changed;
    $('artwork-package').disabled ||= !Object.keys(AREA_FILES).every(area => this.model?.originals[area]);
    $('artwork-flash').disabled ||= !this.host.connected();
    $('artwork-screensaver').disabled = true; $('artwork-screensaver').hidden = true;
    $('artwork-screensaver-info').textContent = 'Der seitliche Bildwechsel auf dem Player ist weiterhin ungeklärt. Hier wird nur die Bildverwaltung bearbeitet.';
    const report = this.model?.storage;
    $('artwork-add').disabled ||= !report?.supported || !report?.capacity;
    $('artwork-delete').disabled ||= !this.model?.ranges.get(this.active)?.slot.custom;
    $('artwork-capacity').textContent = report?.supported ? `${report.free_bytes.toLocaleString('de-DE')} Bytes frei · Platz für ${report.capacity} weitere Bilder` :
      'Zusatzbilder benötigen einen vorhandenen unterstützten Bilderkatalog.';
    for (const [id, tile] of this.tiles) tile.button.disabled = busy || !this.model?.ranges.has(id);
  }

  refresh() {
    if (!this.model) return;
    const slots = new Map(this.config.slots.map(slot => [slot.id, slot]));
    for (const [id, item] of this.model.ranges) slots.set(id, item.slot);
    for (const [id, tile] of this.tiles) if (!slots.has(id)) { tile.button.remove(); this.tiles.delete(id); }
    for (const [id, slot] of slots) if (!this.tiles.has(id)) {
      const button = document.createElement('button'); button.className = 'artwork-tile'; button.dataset.slot = id;
      const canvas = document.createElement('canvas'); canvas.width = this.config.width; canvas.height = this.config.height;
      const label = document.createElement('span'); label.textContent = slot.label;
      const status = document.createElement('small');
      button.append(canvas, label, status); button.addEventListener('click', () => this.select(id));
      $('artwork-gallery').append(button); this.tiles.set(id, {button, canvas, status});
    }
    let text = `${Object.keys(this.model.originals).map(area => area.toUpperCase()).join(' + ')} geladen · ${this.model.edits.size} Bildplätze geändert`;
    if (this.model.normalization.length) text += ' · LFI-Dateizuordnung korrigiert; als neue Datei speichern.';
    if (!this.config.startup_ids.every(id => this.model.ranges.has(id))) text += this.model.originals.brec ? ' · Nur das frühe Startbild ist geladen.' : ' · Frühes Startbild fehlt (BREC nicht geladen).';
    $('artwork-info').textContent = text;
    for (const [id, tile] of this.tiles) {
      tile.button.classList.toggle('changed', this.model.edits.has(id));
      tile.status.textContent = !this.model.ranges.has(id) ? 'Nicht geladen' : this.model.edits.has(id) ? '● Geändert' : 'Original';
      if (this.model.ranges.has(id)) drawRgb(tile.canvas, this.model.rgb(id), this.config);
      else tile.canvas.getContext('2d').clearRect(0, 0, tile.canvas.width, tile.canvas.height);
    }
    this.select(this.active); this.update();
  }

  select(id) {
    this.active = id;
    for (const [key, tile] of this.tiles) tile.button.setAttribute('aria-pressed', String(key === id));
    $('artwork-title').textContent = this.model.ranges.get(id).slot.label;
    drawRgb($('artwork-original'), this.model.rgb(id, true), this.config);
    drawRgb($('artwork-preview'), this.model.rgb(id), this.config);
    this.update();
  }

  crop(source, options = {}, adding = false) {
    this.cropSource = source; this.cropKey = this.active; this.cropAdding = adding; this.rotation = options.rotation || 0;
    $('crop-mode').value = options.mode || 'cover'; $('crop-zoom').value = options.zoom || 1;
    $('crop-x').value = options.panX || 0; $('crop-y').value = options.panY || 0;
    this.previewCrop(); $('artwork-crop').showModal();
  }

  cropOptions() {
    return {rotation: this.rotation, mode: $('crop-mode').value, zoom: Number($('crop-zoom').value), panX: Number($('crop-x').value), panY: Number($('crop-y').value)};
  }

  previewCrop() {
    this.cropRgb = renderPicture(this.cropSource, this.config, this.cropOptions());
    const rgb = this.cropAdding ? decodeRgb565(this.model.prepareNew(this.cropRgb), 'little') : this.model.previewRgb(this.cropKey, this.cropRgb);
    drawRgb($('crop-preview'), rgb, this.config);
  }

  bindCrop() {
    for (const id of ['crop-mode', 'crop-zoom', 'crop-x', 'crop-y']) $(id).addEventListener('input', () => this.previewCrop());
    $('crop-rotate').addEventListener('click', () => { this.rotation = (this.rotation + 90) % 360; $('crop-x').value = 0; $('crop-y').value = 0; this.previewCrop(); });
    $('crop-reset').addEventListener('click', () => {
      this.rotation = 0; $('crop-mode').value = 'cover'; $('crop-zoom').value = 1; $('crop-x').value = 0; $('crop-y').value = 0; this.previewCrop();
    });
    $('crop-cancel').addEventListener('click', () => $('artwork-crop').close());
    $('crop-apply').addEventListener('click', () => void this.host.run('Bildausschnitt übernehmen', async () => {
      let targets;
      if (this.cropAdding) { this.active = this.model.addBackground(this.cropRgb); targets = [this.active]; }
      else { targets = this.model.targets(this.cropKey); this.model.replace(this.cropKey, this.cropRgb); }
      for (const id of targets) this.sources.set(id, {source: this.cropSource, options: this.cropOptions()});
      this.dirty = this.model.hasChanges; $('artwork-crop').close(); this.refresh();
    }, false));
    const canvas = $('crop-preview');
    canvas.addEventListener('pointerdown', event => {
      this.drag = {x: event.clientX, y: event.clientY, ...this.cropOptions()}; canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointerup', () => { this.drag = null; });
    canvas.addEventListener('pointercancel', () => { this.drag = null; });
    canvas.addEventListener('pointermove', event => {
      if (!this.drag) return;
      const source = this.cropSource, swapped = this.rotation % 180 !== 0;
      const box = geometry(swapped ? source.height : source.width, swapped ? source.width : source.height, this.config, this.cropOptions());
      const scale = this.config.width / canvas.getBoundingClientRect().width;
      for (const [id, initial, delta, distance] of [
        ['crop-x', this.drag.panX, event.clientX - this.drag.x, Math.abs(this.config.width - box.width)],
        ['crop-y', this.drag.panY, event.clientY - this.drag.y, Math.abs(this.config.height - box.height)],
      ]) if (distance) $(id).value = Math.max(-1, Math.min(1, initial + delta * scale * 2 / distance));
      this.previewCrop();
    });
  }

  /** Reject existing nonempty outputs, protecting original BIN and ZIP sources. */
  async requireNew(handle) {
    if ((await handle.getFile()).size) throw new Error('Bitte einen neuen Dateinamen verwenden. Vorhandene Dateien werden nicht überschrieben.');
  }

  async saveBins() {
    await this.host.run('Bearbeitete BINs speichern', async () => {
      const areas = this.model.changedAreas, handles = {};
      if (areas.length === 1) handles[areas[0]] = await this.host.savePicker(`x6-bilder-${areas[0]}.bin`, '.bin');
      else {
        const directory = await window.showDirectoryPicker({mode: 'readwrite'});
        for (const area of areas) handles[area] = await directory.getFileHandle(`x6-bilder-${area}.bin`, {create: true});
      }
      for (const handle of Object.values(handles)) await this.requireNew(handle);
      const selected = await this.model.changedSelection();
      for (const area of areas) {
        const handle = handles[area], data = selected.components[area];
        await this.host.save(handle, data);
        const reopened = await selectImages([await handle.getFile()], this.profile);
        assertSame(data, reopened.components[area], 'Gespeicherte Firmware');
      }
      this.dirty = false;
      this.host.result('Bearbeitete BINs gespeichert.', `${areas.map(area => area.toUpperCase()).join(' + ')} erneut eingelesen und geprüft.`, 'success');
    }, false);
  }

  async savePackage() {
    await this.host.run('Firmwarepaket speichern', async () => {
      const handle = await this.host.savePicker('x6-bilder.x6backup.zip', '.zip');
      await this.requireNew(handle);
      const files = await this.model.packageFiles(), manifest = JSON.parse(new TextDecoder().decode(files['manifest.json']));
      await this.host.save(handle, packBackup(files, manifest));
      await validateBundle(unpackBackup(new Uint8Array(await (await handle.getFile()).arrayBuffer()), this.profile), this.profile);
      this.dirty = false;
      this.host.result('Bearbeitetes Firmwarepaket gespeichert.', 'MBREC unverändert, beide BREC-Kopien konsistent. Paket erneut geprüft.', 'success');
    }, false);
  }
}
