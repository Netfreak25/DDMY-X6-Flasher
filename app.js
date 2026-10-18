import { ActionsUSB, usbId, delay } from './lib/transport.js';
import { X6Driver } from './lib/driver.js';
import { X6Operations } from './lib/workflows.js';
import { validateLfi, validateBundle, sha256, assertSame } from './lib/formats.js';
import { packBackup, unpackBackup, importFolder } from './lib/archive.js';
import { sessionFromLocation, bridgeRequest, pairHelper, saveSession } from './lib/bridge.js';
import { hex } from './lib/formats.js';

const $ = id => document.getElementById(id);
let session = null, sessionError = null;
try { session = sessionFromLocation(location, sessionStorage); }
catch (error) { sessionError = error; }
history.replaceState(null, '', location.pathname);
const state = {profile: null, site: null, usb: null, driver: null, serial: null, firmware: null, backup: null,
  busy: false, permissionNeeded: false, expectedReset: false, report: [], pendingBackup: null};
const sizeLabel = size => `${(size / 1_000_000).toFixed(1).replace('.', ',')} MB`;
const stamp = () => new Date().toISOString().replace(/[:.]/g, '-');

/** Hardware API calls go to the explicitly paired loopback helper only. */
async function api(path, method = 'GET', binary = false) {
  return bridgeRequest(session, path, method, binary);
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
}

function progress(phase, done, total) {
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
}

function deviceStatus(label, description, connected = false) {
  $('device-badge').textContent = label;
  $('device-badge').className = `badge${connected ? ' connected' : ''}`;
  $('device-description').textContent = description;
}

/** Serialize UI work across this page and other toolkit tabs on the same origin. */
async function run(title, action) {
  if (state.busy) return;
  state.busy = true;
  state.report = [];
  log({event: 'start', action: title});
  result(title, 'Vorgang wird vorbereitet.');
  updateControls();
  const execute = async lock => {
    if (!lock) throw new Error('Das Toolkit arbeitet bereits in einem anderen Tab.');
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
    const identity = await state.usb.info();
    if (hex(identity.subarray(0, 9)) !== '004341444655443051') throw new Error('Die ADFU-Kennung passt nicht zum X6.');
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
    if (chosen) { await establish(await chosen); return; }
    progress('Windows-App verbinden', 0, 0);
    session = saveSession(await pairHelper(session?.origin || state.site.helper_origin, state.site.helper_timeout_ms), location, sessionStorage);
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
  });
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
  const file = event.target.files[0];
  if (!file) return;
  state.firmware = null;
  $('file-heading').textContent = file.name;
  $('firmware-info').textContent = 'Datei wird geprüft …';
  void run('Firmware prüfen', async () => {
    if (file.size !== state.profile.lfi_size) throw new Error('Die Datei hat nicht die erwartete X6-Firmwaregröße.');
    const data = new Uint8Array(await file.arrayBuffer());
    validateLfi(data, state.profile);
    const hash = await sha256(data);
    state.firmware = {name: file.name, data, hash};
    $('file-heading').textContent = file.name;
    $('firmware-info').textContent = `${sizeLabel(data.length)} · Alle 148 Dateiprüfsummen gültig`;
    log({event: 'firmware_selected', name: file.name, sha256: hash});
    result('Firmware ist gültig.', 'Du kannst sie jetzt mit dem Player vergleichen oder flashen.', 'success');
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
  const compared = await operations().verify(state.firmware.data);
  result(compared.identical ? 'Firmware ist identisch.' : 'Firmware weicht ab.',
    compared.identical ? 'Der gesamte Firmwarebereich stimmt Byte für Byte mit der ausgewählten Datei überein.' :
      `${compared.different.toLocaleString('de-DE')} Bytes unterscheiden sich. Der Vergleich hat nichts in den Flash geschrieben.`,
    compared.identical ? 'success' : 'warning');
}));

$('flash').addEventListener('click', () => void run('Firmware flashen', async () => {
  if (!await confirmAction('LFI-Teilaktualisierung installieren?', `${state.firmware.name}\nHier wird nur das LFI geändert. Für die komplette Firmware mit BREC verwende ein vollständiges Paket. Zuerst wählst du den Speicherort für das bisherige LFI. Nach dem Schreiben wird das gesamte neue Image zurückgelesen und geprüft.`)) {
    result('Abgebrochen', 'Es wurde nichts geschrieben.'); return;
  }
  const handle = await savePicker(`x6-vor-flash-${stamp()}.bin`, '.bin');
  const outcome = await operations().flash(state.firmware.data, async data => {
    // Keep it recoverable in this session if a filesystem error interrupts saving.
    state.pendingBackup = {data, name: `x6-vor-flash-${stamp()}.bin`, extension: '.bin'};
    await saveToHandle(handle, data);
    state.pendingBackup = null;
    return true;
  });
  result('LFI geschrieben und verifiziert.', outcome.normalMode ? 'Der Player ist wieder normal gestartet.' :
    'Der vollständige Rücklesevergleich war erfolgreich. Der normale Neustart ist noch nicht bestätigt.', outcome.normalMode ? 'success' : 'warning');
}));

$('dump').addEventListener('click', () => void run('Sicherung erstellen', async () => {
  const handle = await savePicker(`x6-sicherung-${stamp()}.x6backup.zip`, '.zip');
  const operation = operations(), backup = await operation.dump();
  const data = packBackup(backup.files, backup.manifest);
  state.pendingBackup = {data, name: `x6-${backup.manifest.status}-${stamp()}.x6backup.zip`, extension: '.zip'};
  await saveToHandle(handle, data);
  state.pendingBackup = null;
  if (backup.manifest.status === 'complete') {
    const boot = await operation.reboot();
    result('Vollständige Sicherung gespeichert.', `Bootdatensätze und Firmware wurden gesichert.${boot.normalMode ? ' Der Player ist normal gestartet.' : ' Der Neustart ist noch nicht bestätigt.'}`, boot.normalMode ? 'success' : 'warning');
  } else {
    result('Unvollständige Sicherung gespeichert.', 'Mindestens ein Bereich konnte nicht gültig gelesen werden. Die verfügbaren Daten wurden gespeichert; dieses Archiv ist nicht für Full Restore freigegeben.', 'warning');
  }
}));

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

window.addEventListener('beforeunload', event => { if (state.busy || state.pendingBackup) { event.preventDefault(); event.returnValue = ''; } });
navigator.usb?.addEventListener('disconnect', event => {
  if (state.usb?.device !== event.device || state.expectedReset) return;
  state.usb.alive = false; state.driver = null;
  log({event: 'disconnected'});
  deviceStatus('Verbindung verloren', 'Der USB-Zugriff wurde beendet. Es wird nicht automatisch weitergeschrieben.');
  if (!state.busy) result('Player getrennt.', 'Verbinde den Player erneut, um weiterzuarbeiten.', 'warning');
  updateControls();
});

async function init() {
  updateControls();
  try {
    const [profileResponse, siteResponse] = await Promise.all([fetch('./tools/x6_profile.json'), fetch('./site.json')]);
    if (!profileResponse.ok || !siteResponse.ok) throw new Error('Die Website konnte ihre Konfiguration nicht laden.');
    state.profile = await profileResponse.json();
    const site = await siteResponse.json();
    state.site = site;
    $('helper-download').href = site.helper_download;
    $('repository-link').href = site.repository;
    $('setup-note').hidden = Boolean(session);
    if (sessionError) throw sessionError;
    if (!navigator.usb) throw new Error('Bitte die Website in Chrome oder Edge öffnen; WebUSB fehlt.');
    updateControls();
  } catch (error) { result('Starter benötigt', error.message, 'error'); }
}
void init();
