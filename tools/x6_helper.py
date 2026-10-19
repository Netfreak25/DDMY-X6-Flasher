"""Standalone Windows flasher; optional API bridge for GitHub Pages."""

from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

import x6_poweroff
import x6_toolkit as toolkit
import x6_web as web
from x6_jobs import DeviceJobs
from x6_native import NativeOperations

APP_ID = "Netfreak25.DDMYX6.Flasher"
BACKGROUND = "#f3f8f6"
INK = "#173b36"
TEAL = "#147d6c"
HTTP_SELF_TEST_TIMEOUT_S = 5
UI_POLL_MS = 100


def check_http_bridge(server: web.WebServer) -> dict[str, int]:
    """Exercise actual HTTP pairing without querying or opening USB hardware."""
    site = urlsplit(server.pages_url)
    origin = f"{site.scheme}://{site.netloc}"
    checks = {}
    for method, headers, expected, label in (
        ("OPTIONS", {"Origin": origin, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "x-x6-pair"}, 204, "preflight_status"),
        ("POST", {"Origin": origin, "X-X6-Pair": "1"}, 200, "pair_status"),
    ):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=HTTP_SELF_TEST_TIMEOUT_S)
        try:
            connection.request(method, "/api/pair", headers=headers)
            response = connection.getresponse()
            body = response.read()
            if response.status != expected or response.getheader("Access-Control-Allow-Origin") != origin:
                raise toolkit.ToolkitError(f"HTTP-Selbsttest fehlgeschlagen: {method}, Status {response.status}.")
            if method == "POST" and json.loads(body) != {"origin": server.origin, "token": server.token}:
                raise toolkit.ToolkitError("HTTP-Selbsttest: unpassende Sitzung.")
            checks[label] = response.status
        finally:
            connection.close()
    return checks


class HelperWindow:
    """All widgets stay on the Tk thread; one worker owns each complete job."""

    def __init__(self, root: tk.Tk, profile: toolkit.Profile, runtime: Path, jobs: DeviceJobs,
                 server: web.WebServer | None = None, bridge_error: str = "") -> None:
        self.root, self.server, self.jobs = root, server, jobs
        self.events = queue.SimpleQueue()
        self.operations = NativeOperations(profile, runtime, self.events.put)
        self.busy = False
        self.connected = False
        self.report = []
        self.controls = []
        self.device_controls = []
        self.thread = None
        self.poll_id = None
        self.artwork_window = None
        self.completion = None
        self.job_result = None
        self.device_job = True
        if server:
            self.thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.thread.start()
        root.title("DDMY X6 · Flasher")
        root.geometry("780x780")
        root.minsize(720, 760)
        root.configure(background=BACKGROUND)
        self.icon = tk.PhotoImage(file=str(toolkit.ROOT / "assets/x6-icon.png"))
        root.iconphoto(True, self.icon)
        root.iconbitmap(str(toolkit.ROOT / "assets/x6-icon.ico"))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TProgressbar", background=TEAL, troughcolor="#dceae5", borderwidth=0)
        panel = tk.Frame(root, bg=BACKGROUND, padx=26, pady=18)
        panel.pack(fill="both", expand=True)
        top = tk.Frame(panel, bg=BACKGROUND)
        top.pack(fill="x")
        self.logo = self.icon.subsample(4)
        tk.Label(top, image=self.logo, bg=BACKGROUND).pack(side="left", padx=(0, 14))
        title = tk.Frame(top, bg=BACKGROUND)
        title.pack(side="left")
        tk.Label(title, text="DDMY X6", font=("Segoe UI", 22, "bold"), fg=INK, bg=BACKGROUND).pack(anchor="w")
        site = json.loads(web.SITE_CONFIG.read_text(encoding="utf-8"))
        self.label(title, f"Windows-Flasher · {site['version']}").pack(anchor="w")
        self.connect_button = self.button(top, "Verbinden", self.toggle_connection, primary=True)
        self.connect_button.pack(side="right")
        self.status = tk.StringVar(value="Bereit · noch kein Gerätezugriff")
        tk.Label(panel, textvariable=self.status, fg=TEAL, bg=BACKGROUND, font=("Segoe UI", 12, "bold"),
                 anchor="w").pack(fill="x", pady=(14, 12))

        self.firmware = tk.StringVar()
        self.backup = tk.StringVar()
        self.backup_before_flash = tk.BooleanVar(value=True)
        firmware = self.section(panel, "Firmware · automatische Bereichserkennung")
        self.file_row(firmware, self.firmware, self.choose_firmware)
        row = tk.Frame(firmware, bg="white")
        row.pack(fill="x", pady=(6, 0))
        self.flash_button = self.button(row, "Auswahl flashen", self.flash, device=True)
        self.flash_button.pack(side="left", padx=(0, 8))
        self.compare_button = self.button(row, "Mit Gerät vergleichen", self.compare, device=True)
        self.compare_button.pack(side="left")
        self.artwork_button = self.button(row, "Bilder bearbeiten", self.open_artwork)
        self.artwork_button.pack(side="left", padx=(8, 0))
        self.backup_checkbox = tk.Checkbutton(firmware, text="Vor dem Flashen sichern",
                                             variable=self.backup_before_flash, bg="white", fg=INK,
                                             activebackground="white", font=("Segoe UI", 9))
        self.backup_checkbox.pack(anchor="w")
        self.controls.append(self.backup_checkbox)

        backup = self.section(panel, "Vollständige Sicherung")
        self.file_row(backup, self.backup, self.choose_backup, self.choose_folder)
        row = tk.Frame(backup, bg="white")
        row.pack(fill="x", pady=(6, 0))
        self.dump_button = self.button(row, "Sicherung erstellen", self.dump, device=True)
        self.dump_button.pack(side="left", padx=(0, 8))
        self.restore_button = self.button(row, "Sicherung wiederherstellen", self.restore, device=True)
        self.restore_button.pack(side="left")
        self.label(backup, "MBREC, beide Bootkopien und LFI · Musik und SD-Karte sind nicht enthalten.",
                   background="white").pack(anchor="w", pady=(5, 0))

        modes = tk.Frame(panel, bg=BACKGROUND)
        modes.pack(fill="x", pady=(6, 0))
        self.enter_button = self.button(modes, "ADFU betreten", lambda: self.mode("enter", "ADFU betreten"))
        self.reset_button = self.button(modes, "ADFU verlassen", lambda: self.mode("leave", "ADFU verlassen"))
        self.poweroff_button = self.button(modes, "Player ausschalten", lambda: self.mode("poweroff", "Player ausschalten"))
        for button in (self.enter_button, self.reset_button, self.poweroff_button):
            button.pack(side="left", padx=(0, 8))
        self.label(panel, "Ausschalten: Nach Startbestätigung USB abziehen.").pack(anchor="w", pady=(4, 6))
        self.activity = tk.StringVar(value="Wähle „Verbinden“, um den X6 zu erkennen.")
        self.activity_label = tk.Label(panel, textvariable=self.activity, bg=BACKGROUND, fg=INK,
                                      font=("Segoe UI", 11), wraplength=680, justify="left", anchor="w")
        self.activity_label.pack(fill="x")
        panel.bind("<Configure>", lambda event: self.activity_label.configure(wraplength=max(300, event.width - 52)))
        # Keep the dedicated flat elements from the optional-backup branch:
        # system progress themes can pulse even without a marquee timer.
        for element in ("trough", "pbar"):
            name = f"X6.Horizontal.Progressbar.{element}"
            if name not in style.element_names():
                style.element_create(name, "from", "clam", f"Horizontal.Progressbar.{element}")
        style.layout("X6.Horizontal.TProgressbar", [("X6.Horizontal.Progressbar.trough", {
            "sticky": "nswe", "children": [("X6.Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
        style.configure("X6.Horizontal.TProgressbar", background=TEAL, troughcolor="#e3f0eb", borderwidth=0)
        self.progress = ttk.Progressbar(panel, mode="determinate", style="X6.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 4))
        self.progress_text = tk.StringVar()
        self.progress_label = tk.Label(panel, textvariable=self.progress_text, bg=BACKGROUND, fg=TEAL, font=("Segoe UI", 9))
        self.progress_label.pack(anchor="w")
        self.pending_button = self.button(panel, "Gelesene Sicherung speichern", self.save_pending)
        bottom = tk.Frame(panel, bg=BACKGROUND)
        bottom.pack(side="bottom", fill="x", pady=(10, 0), before=top)
        self.button(bottom, "Details", self.details).pack(side="left")
        self.button(bottom, "Bericht speichern", self.save_report).pack(side="left", padx=(8, 0))
        self.online_button = self.button(bottom, "Online-Flasher öffnen ↗", self.open_browser)
        self.online_button.pack(side="right")
        if bridge_error:
            self.report.append({"event": "bridge_unavailable", "error": bridge_error})
            self.activity.set("Native App bereit. Online-Kopplung nicht verfügbar; siehe Details.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.update_controls()

    @staticmethod
    def label(parent, text: str, *, background=BACKGROUND):
        return tk.Label(parent, text=text, bg=background, fg="#607771", font=("Segoe UI", 9), justify="left")

    def section(self, parent, title: str):
        frame = tk.Frame(parent, bg="white", padx=15, pady=8)
        frame.pack(fill="x", pady=(0, 10))
        tk.Label(frame, text=title, bg="white", fg=INK, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        return frame

    def button(self, parent, text, command, *, primary=False, device=False):
        button = tk.Button(parent, text=text, command=command, font=("Segoe UI", 10),
                           bg=TEAL if primary else "#e3f0eb", fg="white" if primary else INK,
                           activebackground="#cce6dd", relief="flat", cursor="hand2", padx=12, pady=7)
        self.controls.append(button)
        if device:
            self.device_controls.append(button)
        return button

    def file_row(self, parent, variable, select, folder=None):
        row = tk.Frame(parent, bg="white")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=variable, state="readonly").pack(side="left", fill="x", expand=True, ipady=5)
        self.button(row, "Datei wählen", select).pack(side="left", padx=(8, 0))
        if folder:
            self.button(row, "Ordner", folder).pack(side="left", padx=(8, 0))

    def update_controls(self) -> None:
        for button in self.controls:
            button.configure(state="disabled" if self.busy else "normal")
        for button in self.device_controls:
            button.configure(state="normal" if self.connected and not self.busy else "disabled")
        if self.operations.selection is None:
            self.flash_button.configure(state="disabled")
            self.compare_button.configure(state="disabled")
        if not self.server:
            self.online_button.configure(state="disabled")
        self.connect_button.configure(text="Trennen" if self.connected else "Verbinden")
        if self.operations.pending_backup:
            self.progress.pack_forget()
            self.progress_label.pack_forget()
            self.pending_button.pack(anchor="w", pady=(5, 0))
        else:
            self.pending_button.pack_forget()
            self.progress.pack(fill="x", pady=(10, 4))
            self.progress_label.pack(anchor="w")
        if self.artwork_window:
            self.artwork_window.update_controls()

    def open_artwork(self):
        """Create the offline editor lazily; opening it never probes the device."""
        from x6_artwork_ui import ArtworkWindow

        if self.artwork_window:
            self.artwork_window.window.lift()
        else:
            self.artwork_window = ArtworkWindow(self)
            if self.operations.selection:
                self.artwork_window.load_selection(self.operations.selection)

    def choose_firmware(self):
        selected = filedialog.askopenfilenames(parent=self.root, title="X6-Firmware auswählen (Mehrfachauswahl möglich)",
                                               filetypes=[("X6-Firmware oder Paket", "*.bin *.zip"), ("Alle Dateien", "*.*")])
        if selected:
            sources = tuple(Path(path) for path in selected)
            self.firmware.set("Dateien werden geprüft …")
            self.operations.selection = None
            self.start("Dateien erkennen und prüfen", lambda: self.operations.inspect(sources), device=False)

    def choose_backup(self):
        selected = filedialog.askopenfilename(parent=self.root, title="X6-Sicherung auswählen", filetypes=[("X6-Sicherung", "*.zip")])
        if selected:
            self.backup.set(selected)

    def choose_folder(self):
        selected = filedialog.askdirectory(parent=self.root, title="Toolkit-Sicherungsordner auswählen")
        if selected:
            self.backup.set(selected)

    def toggle_connection(self):
        if self.connected:
            self.connected = False
            self.operations.serial = None
            self.status.set("Getrennt · Player bleibt gegebenenfalls in ADFU")
            self.update_controls()
        else:
            self.start("Verbinden", self.operations.connect)

    def dump(self):
        destination = filedialog.asksaveasfilename(parent=self.root, title="Sicherung speichern", defaultextension=".x6backup.zip",
                                                  initialfile="x6-sicherung.x6backup.zip", filetypes=[("X6-Sicherung", "*.zip")])
        if destination:
            self.start("Sicherung erstellen", lambda: self.operations.dump(Path(destination)))

    def compare(self):
        selection = self.operations.selection
        if selection is None:
            self.choose_firmware()
            return
        self.start("Mit Gerät vergleichen", lambda: self.operations.compare_selection(selection))

    def flash(self, selection=None, changed_labels=None):
        selection = selection or self.operations.selection
        if selection is None:
            self.choose_firmware()
            return
        description = f"Erkannte Bereiche: {selection.label}\n"
        if changed_labels:
            description += "Geänderte Bilder: " + ", ".join(changed_labels) + "\n"
        if "brec" in selection.areas:
            description += "BREC wird in beiden Bootkopien aktualisiert.\n"
        create_backup = self.backup_before_flash.get()
        description += ("Zuerst Rücksicherung speichern, dann diese Bereiche schreiben und vollständig zurücklesen."
                        if create_backup else "Ohne neue Rücksicherung schreiben und anschließend vollständig zurücklesen.")
        if not messagebox.askokcancel("Auswahl flashen", description, parent=self.root):
            return
        destination = None
        if create_backup:
            extension = ".x6backup.zip" if selection.has_boot else ".bin"
            name = filedialog.asksaveasfilename(parent=self.root, title="Rücksicherung vor dem Flashen speichern",
                                               defaultextension=extension, initialfile="x6-vor-flash" + extension,
                                               filetypes=[("X6-Sicherung", "*.zip" if selection.has_boot else "*.bin")])
            if not name:
                return
            destination = Path(name)
            if changed_labels and Path(destination).exists():
                messagebox.showerror("Neuen Namen wählen", "Die Rücksicherung benötigt einen neuen Dateinamen. Vorhandene Dateien bleiben erhalten.", parent=self.root)
                return
        self.start(f"{selection.label} flashen", lambda: self.operations.flash_selection(selection, destination))

    def restore(self):
        if not self.backup.get():
            self.choose_backup()
        emergency = not self.operations.profile.boot_write_mapping_verified
        title = "Notfall-Wiederherstellung" if emergency else "Full Restore"
        description = "MBREC, beide BREC-Kopien und LFI durch diese Sicherung ersetzen und vollständig zurücklesen?"
        if emergency:
            description += "\nDer Boot-Schreibweg ist nicht freigegeben. Ohne Boot-Rücklesung bleibt das Ergebnis unverifiziert."
        if self.backup.get() and messagebox.askokcancel(title, description, parent=self.root):
            source = Path(self.backup.get())
            self.start(title, lambda: self.operations.restore(source, emergency))

    def mode(self, action, title):
        self.start(title, lambda: self.operations.mode(action))

    def save_pending(self):
        source = self.operations.pending_backup
        if source is None:
            return
        extension = ".x6backup.zip" if source.is_dir() else ".bin"
        destination = filedialog.asksaveasfilename(parent=self.root, title="Gelesene Sicherung speichern",
                                                  initialfile="x6-sicherung" + extension, defaultextension=extension)
        if destination:
            self.start("Sicherung speichern", lambda: self.operations.save_pending(Path(destination)))

    def start(self, title: str, action, *, device=True, on_result=None) -> None:
        """Acquire before spawning: double clicks cannot create competing workers."""
        if self.busy:
            return
        try:
            token = self.jobs.acquire("Windows-App") if device else None
        except toolkit.ToolkitError as error:
            messagebox.showinfo("X6 wird verwendet", str(error), parent=self.root)
            return
        self.busy = True
        self.device_job, self.completion, self.job_result = device, on_result, None
        self.report = [{"event": "start", "action": title}]
        self.activity.set(title)
        self.progress.configure(mode="determinate", value=0)
        self.progress_text.set("Bitte warten …")
        self.update_controls()

        def work():
            try:
                self.events.put({"event": "result", **action()})
            except Exception as error:  # noqa: BLE001 - surface every worker failure, never retry.
                event = {"event": "failure", "error": str(error), "type": type(error).__name__}
                if self.operations.log:
                    event["directory"] = str(self.operations.log.directory)
                    try:
                        self.operations.log.event("operation_failed", error=str(error))
                    except OSError as log_error:
                        event["log_error"] = str(log_error)
                self.events.put(event)
            finally:
                if token is not None:
                    self.jobs.release(token)
                self.events.put({"event": "finished"})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.poll_id = self.root.after(UI_POLL_MS, self.poll)

    def poll(self):
        self.poll_id = None
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.report.append(event)
            kind = event["event"]
            if kind == "phase":
                if self.activity.get() != event["label"]:
                    self.activity.set(event["label"])
                    self.progress.configure(mode="determinate", value=0)
                    self.progress_text.set("Bitte warten …")
            elif kind == "backend_output":
                match = re.search(r"write_flash: (\d+)/(\d+) sectors", event["line"])
                if match:
                    done, total = map(int, match.groups())
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=total, value=done)
                    self.progress_text.set(f"{done / total:.0%} · bestätigte Sektoren")
            elif kind == "result":
                self.job_result = event
                self.activity.set(event["message"])
                if event.get("selection_ready"):
                    selected = self.operations.selection
                    self.firmware.set(f"{selected.label} · " + ", ".join(path.name for path in selected.sources))
                if event.get("connected"):
                    self.connected = True
                    self.status.set(event["message"])
                else:
                    self.status.set("Vorgang beendet" if not event.get("warning") else "Vorgang beendet · Hinweis beachten")
            elif kind == "failure":
                if self.device_job:
                    self.connected = False
                self.status.set("Vorgang gestoppt · erneut verbinden" if self.device_job else "Dateivorgang gestoppt")
                self.activity.set(event["error"])
            elif kind == "finished":
                self.busy = False
                self.progress.stop()
                self.progress.configure(value=0)
                self.progress_text.set("")
                self.update_controls()
                callback, completed = self.completion, self.job_result
                self.completion, self.job_result = None, None
                if callback and completed:
                    callback(completed)
        if self.busy and self.poll_id is None:
            self.poll_id = self.root.after(UI_POLL_MS, self.poll)

    def details(self):
        window = tk.Toplevel(self.root)
        window.title("X6 · Details")
        window.geometry("800x500")
        text = tk.Text(window, wrap="word", font=("Consolas", 10))
        text.pack(fill="both", expand=True)
        text.insert("1.0", json.dumps(self.report, ensure_ascii=False, indent=2))
        text.configure(state="disabled")

    def save_report(self):
        target = filedialog.asksaveasfilename(parent=self.root, title="Bericht speichern", defaultextension=".json", initialfile="x6-bericht.json")
        if target:
            try:
                Path(target).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Bericht speichern", str(error), parent=self.root)

    def open_browser(self):
        if self.server:
            try:
                subprocess.Popen([str(web.browser_path()), self.server.launch_url()])
            except (OSError, toolkit.ToolkitError) as error:
                messagebox.showerror("Online-Flasher öffnen", str(error), parent=self.root)

    def close(self):
        if self.busy or self.jobs.owner:
            messagebox.showinfo("X6 wird verwendet", "Bitte den laufenden Auftrag beenden und in GitHub Pages zuerst trennen.", parent=self.root)
            return
        if self.artwork_window and not self.artwork_window.close():
            return
        if self.poll_id:
            self.root.after_cancel(self.poll_id)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.root.destroy()


def self_test(window: HelperWindow, profile: toolkit.Profile, runtime: Path) -> dict:
    """Check resources, real window controls and HTTP, never USB."""
    log = toolkit.new_run("package-self-test")
    toolkit.ActionsBackend(profile, runtime, log)  # hashes only; no process/USB command
    payload = x6_poweroff.load_payload(profile, runtime)
    http_checks = check_http_bridge(window.server)
    root = window.root
    root.geometry("+10000+10000")
    root.deiconify()
    root.update()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    hwnd = user32.GetParent(root.winfo_id())
    icons = [bool(user32.SendMessageW(hwnd, 0x7F, size, 0)) for size in (0, 1)]
    fits = all(button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
               and button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
               and button.winfo_height() >= button.winfo_reqheight()
               and button.winfo_width() >= button.winfo_reqwidth()
               for button in window.controls if button.winfo_ismapped())
    # Exercise bundled Pillow, the shared catalog and the real editor widgets.
    import io

    import x6_artwork as artwork
    import x6_picture as picture
    import x6_screensaver as screensaver
    from PIL import Image

    checked_formats = []
    screen_profile = artwork.catalog()["screensaver"]
    sample = b"\xff\xff" * screen_profile["width"] * screen_profile["height"]
    processed = screensaver.process(sample, screen_profile, 1)
    if processed == sample or screensaver.process(processed, screen_profile, 1) != processed:
        raise toolkit.ToolkitError("Bildschirmschoner-Aufbereitung fehlgeschlagen.")
    for format_name in ("PNG", "JPEG", "WEBP"):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 2), "red").save(buffer, format=format_name)
        path = log.directory / ("image-self-test." + format_name.lower())
        path.write_bytes(buffer.getvalue())
        if picture.import_picture(path).size != (4, 2):
            raise toolkit.ToolkitError("Bilddecoder-Selbsttest fehlgeschlagen.")
        checked_formats.append(format_name)
    window.open_artwork()
    editor = window.artwork_window
    editor.window.geometry("+10000+10000")
    editor.window.update()
    slots = len(editor.tiles)
    editor.close()
    return {"ok": all(icons) and fits, "window_icons": icons, "http_pairing": http_checks,
            "controls": [button.cget("text") for button in window.controls], "buttons_fit": fits,
            "backup_default": window.backup_before_flash.get(),
            "wait_progress_style": window.progress.cget("style"),
            "poweroff_payload_size": len(payload), "backend_hashes_checked": True,
            "device_access": False, "browser_started": False,
            "screensaver_revision": screen_profile["revision"],
            "artwork_slots": slots if len(artwork.catalog()["slots"]) == slots else 0, "artwork_formats": checked_formats,
            "workspace_outside_resources": not toolkit.PRIVATE_ROOT.is_relative_to(toolkit.ROOT)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    toolkit.use_desktop_workspace()
    root = tk.Tk()
    root.withdraw()
    server = None
    try:
        profile, jobs = toolkit.Profile.load(), DeviceJobs()
        bridge_error = ""
        try:
            server = web.WebServer(0 if args.self_test else web.DEFAULT_PORT, profile, toolkit.DEFAULT_RUNTIME, online=True, jobs=jobs)
        except (OSError, toolkit.ToolkitError) as error:
            bridge_error = str(error)
        window = HelperWindow(root, profile, toolkit.DEFAULT_RUNTIME, jobs, server, bridge_error)
        if args.self_test:
            args.self_test.write_text(json.dumps(self_test(window, profile, toolkit.DEFAULT_RUNTIME)), encoding="utf-8")
            window.close()
            return
        root.deiconify()
        root.mainloop()
    except (OSError, ValueError, tk.TclError, toolkit.ToolkitError) as error:
        if args.self_test:
            args.self_test.write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
        else:
            messagebox.showerror("X6-App konnte nicht starten", str(error), parent=root)
        if server:
            server.server_close()
        root.destroy()


if __name__ == "__main__":
    main()
