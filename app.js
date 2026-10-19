import { ActionsUSB, usbId, delay } from './lib/transport.js';
import { X6Driver } from './lib/driver.js';
import { X6Operations, incompleteDumpMessage } from './lib/workflows.js';
import { validateBundle, sha256, assertSame } from './lib/formats.js';
import { selectImages, selectionLabel, hasBoot } from './lib/images.js';
import { packBackup, unpackBackup, importFolder } from './lib/archive.js';
import { sessionFromLocation, bridgeRequest, pairHelper, saveSession } from './lib/bridge.js';
import { ArtworkPanel } from './lib/artwork-ui.js';

const $ = id => document.getElementById(id);
let session = null, sessionError = null;
let artwork = null;
try { session = sessionFromLocation(location, sessionStorage); }
catch (error) { sessionError = error; }
history.replaceState(null, '', location.pathname);
const state = {profile: null, site: null, usb: null, driver: null, serial: null, firmware: null, backup: null,
  busy: false, permissionNeeded: false, expectedReset: false, report: [], pendingBackup: null, job: null};
const sizeLabel = size => `${(size / 1_000_000).toFixed(1).replace('.', ',')} MB`;
const stamp = () => new Date().toISOString().replace(/[:.]/g, '-');

/** Hardware API calls go to the explicitly paired loopback helper only. */
async function api(path, method = 'GET', binary = false) {
  return bridgeRequest(session, path, method, binary, state.job);
}

/** Hold the helper's device lease for the full WebUSB connection lifetime. */
async function acquireDevice() {
  if (!state.job) state.job = (await api('jobs/acquire', 'POST')).job;
  if (!state.job) throw new Error('Bitte die aktuelle Windows-App verwenden; Gerätefreigabe fehlt.');
}

async function releaseDevice() {
  if (!state.job) return;
  const job = state.job;
  state.job = null;
  await bridgeRequest(session, 'jobs/release', 'POST', false, job);
}

function log(entry) {
  state.report.push({time: new Date().toISOString(), ...entry});
  $('report').textContent = JSON.stringify(state.report, null, 2);
}

function result(title, message, kind = '') {
  $('activity').className = `activity ${kind}`;
  $('activity-heading').textContent = title;
  $('activity-message').textContent = message;
  $('progress').hidden = true;
  $('progress-text').textContent = '';
  $('artwork-activity').textContent = `${title} ${message}`;
}

function progress(phase, done, total) {
  $('artwork-activity').textContent = `${phase}${total ? ` · ${Math.round(done / total * 100)} %` : ''}`;
  $('activity-heading').textContent = phase;
  $('activity-message').textContent = 'Bitte den Player angeschlossen und diese Seite geöffnet lassen.';
  $('progress').hidden = false;
  if (total) {
    $('progress').value = done / total * 100;
    $('progress-text').textContent = `${Math.round(done / total * 100)} % · ${sizeLabel(done)} / ${sizeLabel(total)}`;
  } else {
    $('progress').removeAttribute('value');
    $('progress-text').textContent = '';
  }
}

function updateControls() {
  for (const control of document.querySelectorAll('[data-control]')) control.disabled = state.busy || !state.profile;
  const connected = Boolean(state.driver && state.usb?.alive);
  $('dump').disabled ||= !connected;
  $('restore').disabled ||= !connected || !state.backup;
  $('flash').disabled ||= !connected || !state.firmware;
  $('verify').disabled ||= !connected || !state.firmware;
  $('restart').hidden = !connected;
  $('connect').textContent = connected ? 'Trennen' : state.permissionNeeded ? 'USB-Zugriff erlauben' : 'Verbinden ↗';
  $('save-pending').hidden = !state.pendingBackup;
  $('save-pending').disabled = state.busy;
  artwork?.update();
}

function deviceStatus(label, description, connected = false) {
  $('device-badge').textContent = label;
  $('device-badge').className = `badge${connected ? ' connected' : ''}`;
  $('device-description').textContent = description;
}

/** Serialize UI work across this page and other toolkit tabs on the same origin. */
async function run(title, action, checkLease = true) {
  if (state.busy) return;
  state.busy = true;
  state.report = [];
  log({event: 'start', action: title});
  result(title, 'Vorgang wird vorbereitet.');
  updateControls();
  const execute = async lock => {
    if (!lock) throw new Error('Das Toolkit arbeitet bereits in einem anderen Tab.');
    if (checkLease && state.driver) await api('jobs/check');
    return action();
  };
  try {
    if (!navigator.locks) throw new Error('Bitte eine aktuelle Version von Chrome oder Edge verwenden.');
    await navigator.locks.request('x6-device-operation', {ifAvailable: true}, execute);
  } catch (error) {
    log({event: 'error', message: error.message});
    result(error.name === 'AbortError' ? 'Abgebrochen' : 'Vorgang gestoppt',
      error.name === 'AbortError' ? 'Die Auswahl wurde abgebrochen.' : error.message,
      error.name === 'AbortError' ? '' : 'error');
    if (state.usb && !state.usb.alive) {
      state.driver = null;
      deviceStatus('Neu verbinden', 'Die USB-Sitzung wurde beendet. Es werden keine weiteren Befehle gesendet.');
    }
  } finally {
    if (!state.usb?.alive && state.job) {
      try { await releaseDevice(); }
      catch (error) { log({event: 'lease_release_failed', message: error.message}); }
    }
    state.busy = false;
    updateControls();
  }
}

async function establish(device) {
  const current = await api('status');
  if (current.mode !== 'adfu') throw new Error('Der X6 ist nicht mehr im ADFU-Modus. Bitte erneut verbinden.');
  state.usb = new ActionsUSB(device, state.profile, log);
  await state.usb.open();
  state.driver = new X6Driver(state.usb, state.profile, name => api(`payloads/${name}`, 'GET', true), log);
  progress('Player vorbereiten', 0, 0);
  try {
    await state.driver.bootstrap();
  }
  catch (error) { await state.usb.close(); state.driver = null; throw error; }
  state.permissionNeeded = false;
  deviceStatus('Verbunden', 'X6 erkannt und bereit für Sicherung, Vergleich oder Aktualisierung.', true);
  result('X6 ist verbunden.', 'Wähle jetzt die gewünschte Aktion.', 'success');
}

$('connect').addEventListener('click', () => {
  if (state.busy || !state.profile) return;
  // The browser chooser must be requested inside this actual user gesture.
  let chosen;
  if (state.permissionNeeded && !state.driver) {
    chosen = navigator.usb.requestDevice({filters: [usbId(state.profile.adfu_usb_id)]});
    chosen.catch(() => {}); // The awaited operation below displays the chooser error.
  }
  void run('Verbinden', async () => {
    if (!navigator.usb) throw new Error('WebUSB ist nicht verfügbar. Bitte Chrome oder Edge verwenden.');
    if (state.driver && state.usb.alive) {
      await state.usb.close(); state.driver = null;
      deviceStatus('Getrennt', 'Der Browserzugriff ist geschlossen. Der Player bleibt gegebenenfalls in ADFU.');
      result('Verbindung getrennt.', 'Zum normalen Start den Player erneut verbinden und „Player starten“ wählen.');
      return;
    }
    if (chosen) { await acquireDevice(); await establish(await chosen); return; }
    progress('Windows-App verbinden', 0, 0);
    session = saveSession(await pairHelper(session?.origin || state.site.helper_origin, state.site.helper_timeout_ms), location, sessionStorage);
    await acquireDevice();
    $('setup-note').hidden = true;
    const current = await api('status');
    if (current.mode === 'missing') throw new Error('Kein X6 gefunden. Bitte den Player per USB anschließen.');
    if (current.mode === 'normal') {
      state.serial = current.serial;
      progress('In den Wartungsmodus wechseln', 0, 0);
      await api('enter-adfu', 'POST');
    }
    const id = usbId(state.profile.adfu_usb_id);
    const devices = (await navigator.usb.getDevices()).filter(device => device.vendorId === id.vendorId && device.productId === id.productId);
    if (devices.length > 1) throw new Error('Mehrere passende Geräte gefunden. Bitte nur einen X6 anschließen.');
    if (devices.length === 1) { await establish(devices[0]); return; }
    state.permissionNeeded = true;
    // Fast pairing can retain the click's transient activation. After a slow
    // ADFU switch, request a fresh click instead of triggering SecurityError.
    if (navigator.userActivation?.isActive) {
      await establish(await navigator.usb.requestDevice({filters: [id]}));
      return;
    }
    deviceStatus('ADFU bereit', 'Bitte einmal „USB-Zugriff erlauben“ anklicken und den Player auswählen.');
    result('USB-Zugriff erlauben', 'Der X6 ist bereit. Bestätige den Zugriff über die Schaltfläche oben.');
  }, false); // Disconnect must remain possible even after a helper restart.
});

async function restart(driver) {
  state.expectedReset = true;
  try {
    await driver.reset();
    state.driver = null;
    const deadline = performance.now() + state.profile.mode_timeout_s * 1000;
    while (performance.now() < deadline) {
      const current = await api('status');
      if (current.mode === 'normal') {
        if (state.serial && current.serial !== state.serial) throw new Error('Nach dem Neustart wurde ein anderer Player erkannt.');
        deviceStatus('Normal gestartet', 'Der X6 ist wieder als normaler USB-Player verfügbar.');
        return true;
      }
      await delay(1000);
    }
    deviceStatus('Neustart offen', 'Der normale USB-Modus wurde noch nicht erkannt.');
    return false;
  } finally { state.expectedReset = false; }
}

function operations() {
  if (!state.driver || !state.usb.alive) throw new Error('Bitte den X6 zuerst verbinden.');
  return new X6Operations(state.driver, state.profile, {serial: state.serial, progress, log, restart});
}

$('restart').addEventListener('click', () => void run('Player starten', async () => {
  progress('Neustarten', 0, 0);
  const normal = await restart(state.driver);
  result(normal ? 'Player neu gestartet.' : 'Neustart noch nicht bestätigt.',
    normal ? 'Der normale USB-Modus wurde erkannt.' : 'Bitte den Playerzustand prüfen.', normal ? 'success' : 'warning');
}));

/** A successfully closed file is read back before any flash write is allowed. */
async function saveToHandle(handle, data) {
  const writable = await handle.createWritable();
  try { await writable.write(data); await writable.close(); }
  catch (error) {
    try { await writable.abort(); } catch (abortError) { log({event: 'file_abort_error', message: abortError.message}); }
    throw error;
  }
  assertSame(data, new Uint8Array(await (await handle.getFile()).arrayBuffer()), 'Gespeicherte Sicherung');
  log({event: 'backup_saved', name: handle.name || 'X6-Sicherung', size: data.length, sha256: await sha256(data)});
  return true;
}

function savePicker(name, extension) {
  if (!window.showSaveFilePicker) throw new Error('Sicheres Speichern benötigt Chrome oder Edge.');
  return window.showSaveFilePicker({suggestedName: name, types: [{description: 'X6-Sicherung', accept: {'application/octet-stream': [extension]}}]});
}

function confirmAction(title, description, emergency = false) {
  $('confirm-title').textContent = title;
  $('confirm-description').textContent = description;
  $('emergency-option').hidden = !emergency;
  $('emergency-check').checked = false;
  $('confirm-go').disabled = emergency;
  $('emergency-check').onchange = () => { $('confirm-go').disabled = emergency && !$('emergency-check').checked; };
  const dialog = $('confirmation');
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}));
}

$('firmware-file').addEventListener('change', event => {
  const files = Array.from(event.target.files);
  if (!files.length) return;
  state.firmware = null;
  $('file-heading').textContent = files.map(file => file.name).join(', ');
  $('firmware-info').textContent = 'Dateien werden erkannt und geprüft …';
  void run('Firmware prüfen', async () => {
    const selected = await selectImages(files, state.profile);
    state.firmware = selected;
    $('firmware-info').textContent = `Erkannt: ${selectionLabel(selected)} · Dateien geprüft`;
    log({event: 'firmware_selected', names: selected.names, hashes: selected.hashes});
    result('Firmware ist gültig.', `${selectionLabel(selected)} kann jetzt verglichen oder geflasht werden.`, 'success');
  });
});

async function selectBackup(selection, folder) {
  state.backup = null;
  $('backup-info').textContent = 'Sicherung wird geprüft …';
  await run('Sicherung prüfen', async () => {
    const files = folder ? await importFolder(selection, state.profile) :
      unpackBackup(new Uint8Array(await selection[0].arrayBuffer()), state.profile);
    const manifest = await validateBundle(files, state.profile);
    state.backup = {files, manifest};
    $('backup-info').textContent = 'Vollständige X6-Sicherung · Prüfsummen gültig';
    result('Sicherung ist vollständig.', 'Bootdatensätze und Firmware sind für eine Wiederherstellung vorhanden.', 'success');
  });
  if (!state.backup) $('backup-info').textContent = 'Keine gültige Sicherung ausgewählt';
}

$('backup-file').addEventListener('change', event => { if (event.target.files.length) void selectBackup(event.target.files, false); });
$('backup-folder').addEventListener('change', event => { if (event.target.files.length) void selectBackup(event.target.files, true); });

$('verify').addEventListener('click', () => void run('Mit Gerät vergleichen', async () => {
  const compared = await operations().verifySelection(state.firmware);
  result(compared.identical ? 'Firmware ist identisch.' : 'Firmware weicht ab.',
    compared.identical ? `Alle ausgewählten Bereiche (${selectionLabel(state.firmware)}) stimmen Byte für Byte überein.` :
      `${compared.different.toLocaleString('de-DE')} Bytes unterscheiden sich. Der Vergleich hat nichts in den Flash geschrieben.`,
    compared.identical ? 'success' : 'warning');
}));

$('flash').addEventListener('click', () => void run('Firmware flashen', () => flashSelected(state.firmware)));

async function flashSelected(selected, changedLabels = []) {
  const label = selectionLabel(selected), boot = hasBoot(selected);
  const backup = $('backup-before-flash').checked;
  const description = (changedLabels.length ? `Geänderte Bilder: ${changedLabels.join(', ')}. ` : '') +
    `Erkannte Bereiche: ${label}. ${selected.components.brec ? 'BREC wird in beiden Bootkopien aktualisiert. ' : ''}` +
    (backup ? 'Zuerst wird die Rücksicherung gespeichert. ' : 'Ohne neue Rücksicherung. ') +
    'Anschließend werden diese Bereiche geschrieben und vollständig zurückgelesen.';
  if (!await confirmAction(`${label} flashen?`, description)) {
    result('Abgebrochen', 'Es wurde nichts geschrieben.'); return;
  }
  const extension = boot ? '.zip' : '.bin', name = `x6-vor-flash-${stamp()}${boot ? '.x6backup.zip' : '.bin'}`;
  const handle = backup ? await savePicker(name, extension) : null;
  if (handle && changedLabels.length && (await handle.getFile()).size) throw new Error('Die Rücksicherung benötigt einen neuen Dateinamen. Vorhandene Dateien bleiben erhalten.');
  const outcome = await operations().flashSelection(selected, async backup => {
    const data = boot ? packBackup(backup.files, backup.manifest) : backup;
    // Keep it recoverable in this session if a filesystem error interrupts saving.
    state.pendingBackup = {data, name, extension};
    await saveToHandle(handle, data);
    state.pendingBackup = null;
    return true;
  }, {backup});
  result(`${label} geschrieben und verifiziert.`, outcome.normalMode ? 'Der Player ist wieder normal gestartet.' :
    'Der vollständige Rücklesevergleich war erfolgreich. Der normale Neustart ist noch nicht bestätigt.', outcome.normalMode ? 'success' : 'warning');
}

$('dump').addEventListener('click', () => void run('Sicherung erstellen', () => dumpAndSave()));

async function dumpAndSave(forEditor = false) {
  const handle = await savePicker(`x6-sicherung-${stamp()}.x6backup.zip`, '.zip');
  if (forEditor && (await handle.getFile()).size) throw new Error('Die Originalsicherung benötigt einen neuen Dateinamen.');
  const operation = operations(), backup = await operation.dump();
  const data = packBackup(backup.files, backup.manifest);
  state.pendingBackup = {data, name: `x6-${backup.manifest.status}-${stamp()}.x6backup.zip`, extension: '.zip'};
  await saveToHandle(handle, data);
  state.pendingBackup = null;
  if (backup.manifest.status === 'complete') {
    const boot = await operation.reboot();
    result('Vollständige Sicherung gespeichert.', `Bootdatensätze und Firmware wurden gesichert.${boot.normalMode ? ' Der Player ist normal gestartet.' : ' Der Neustart ist noch nicht bestätigt.'}`, boot.normalMode ? 'success' : 'warning');
    return {...backup.files, 'manifest.json': new TextEncoder().encode(JSON.stringify(backup.manifest))};
  } else {
    result('Unvollständige Sicherung gespeichert.', incompleteDumpMessage(backup.manifest), 'warning');
  }
  return null;
}

$('restore').addEventListener('click', () => void run('Sicherung wiederherstellen', async () => {
  const emergency = !state.profile.boot_write_mapping_verified;
  const description = 'Bootdatensätze und Firmware werden durch die ausgewählte Sicherung ersetzt. ' +
    (emergency ? 'Die Wirkung des Boot-Schreibbefehls auf beide Kopien ist am X6 noch nicht vollständig belegt. Ohne erfolgreiche Boot-Rücklesung bleibt das Ergebnis teilweise unverifiziert.' : 'Anschließend werden alle Bereiche vollständig zurückgelesen.');
  if (!await confirmAction('Full Restore starten?', description, emergency)) { result('Abgebrochen', 'Es wurde nichts geschrieben.'); return; }
  const outcome = await operations().restore(state.backup.files, emergency);
  result(outcome.bootVerified ? 'Vollständig zurückgeschrieben und verifiziert.' : 'Firmware verifiziert, Boot-Rücklesung offen.',
    outcome.bootVerified ? (outcome.normalMode ? 'Der Player ist wieder normal gestartet.' : 'Die Rücksicherung ist geprüft. Der Neustart ist noch nicht bestätigt.') :
      'Die Schreibbefehle wurden bestätigt und das LFI vollständig geprüft. Die Bootdatensätze konnten nicht vollständig zurückgelesen werden. Der Player wurde nicht automatisch neu gestartet.',
    outcome.bootVerified && outcome.normalMode ? 'success' : 'warning');
}));

$('save-pending').addEventListener('click', () => void run('Sicherung speichern', async () => {
  const backup = state.pendingBackup;
  const handle = await savePicker(backup.name, backup.extension);
  await saveToHandle(handle, backup.data);
  state.pendingBackup = null;
  result('Sicherung gespeichert.', 'Die bereits gelesenen Daten wurden lokal gespeichert.', 'success');
}));

$('save-report').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.report, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url; link.download = `x6-bericht-${stamp()}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

window.addEventListener('beforeunload', event => { if (state.busy || state.pendingBackup || state.job || artwork?.dirty) { event.preventDefault(); event.returnValue = ''; } });
navigator.usb?.addEventListener('disconnect', event => {
  if (state.usb?.device !== event.device || state.expectedReset) return;
  state.usb.alive = false; state.driver = null;
  log({event: 'disconnected'});
  deviceStatus('Verbindung verloren', 'Der USB-Zugriff wurde beendet. Es wird nicht automatisch weitergeschrieben.');
  if (!state.busy) result('Player getrennt.', 'Verbinde den Player erneut, um weiterzuarbeiten.', 'warning');
  if (!state.busy) void releaseDevice().catch(error => log({event: 'lease_release_failed', message: error.message}));
  updateControls();
});

async function init() {
  updateControls();
  try {
    const [profileResponse, siteResponse, artworkResponse] = await Promise.all([fetch('./tools/x6_profile.json'), fetch('./site.json'), fetch('./tools/x6_artwork.json')]);
    if (!profileResponse.ok || !siteResponse.ok || !artworkResponse.ok) throw new Error('Die Website konnte ihre Konfiguration nicht laden.');
    state.profile = await profileResponse.json();
    const site = await siteResponse.json();
    state.site = site;
    artwork = new ArtworkPanel(state.profile, await artworkResponse.json(), {
      run, result, confirm: confirmAction, save: saveToHandle, savePicker, flash: flashSelected, dump: () => dumpAndSave(true),
      busy: () => state.busy, connected: () => Boolean(state.driver && state.usb?.alive), selection: () => state.firmware,
    });
    $('helper-download').href = site.helper_download;
    $('repository-link').href = site.repository;
    $('setup-note').hidden = Boolean(session);
    if (sessionError) throw sessionError;
    if (!navigator.usb) throw new Error('Bitte die Website in Chrome oder Edge öffnen; WebUSB fehlt.');
    updateControls();
  } catch (error) { result('Starter benötigt', error.message, 'error'); }
}
void init();
