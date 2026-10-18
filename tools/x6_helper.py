"""Small Windows GUI for the local X6 app; starting it never probes hardware."""

from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import os
import queue
import subprocess
import threading
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox
from urllib.parse import urlsplit

import x6_poweroff
import x6_reset
import x6_toolkit as toolkit
import x6_web as web

APP_ID = "Netfreak25.DDMYX6.Flasher.Helper"
BACKGROUND = "#f3f8f6"
INK = "#173b36"
TEAL = "#147d6c"
HTTP_SELF_TEST_TIMEOUT_S = 5
UI_POLL_MS = 100
DEVICE_ACTIONS = {
    "enter": ("ADFU betreten", "X6 wechselt nach ADFU …", "ADFU-Wechsel nicht bestätigt"),
    "leave": ("ADFU verlassen", "X6 wird neu gestartet …", "Neustart nicht bestätigt"),
    "poweroff": ("Player ausschalten", "Ausschaltcode wird geladen und geprüft …", "Ausschaltaufruf nicht bestätigt"),
}


def check_http_bridge(server: web.WebServer) -> dict[str, int]:
    """Exercise packaged HTTP preflight and pairing without accessing any USB API."""
    site = urlsplit(server.pages_url)
    origin = f"{site.scheme}://{site.netloc}"
    checks = {}
    requests = (
        ("OPTIONS", {"Origin": origin, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "x-x6-pair"}, 204, "preflight_status"),
        ("POST", {"Origin": origin, "X-X6-Pair": "1"}, 200, "pair_status"),
    )
    for method, headers, expected_status, label in requests:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=HTTP_SELF_TEST_TIMEOUT_S)
        try:
            connection.request(method, "/api/pair", headers=headers)
            response = connection.getresponse()
            body = response.read()
            if response.status != expected_status or response.getheader("Access-Control-Allow-Origin") != origin:
                raise toolkit.ToolkitError(f"HTTP-Selbsttest fehlgeschlagen: {method} /api/pair, Status {response.status}.")
            if method == "POST" and json.loads(body) != {"origin": server.origin, "token": server.token}:
                raise toolkit.ToolkitError("HTTP-Selbsttest: unpassende lokale Sitzung.")
            checks[label] = response.status
        finally:
            connection.close()
    return checks


class HelperWindow:
    """Own the app lifetime and present its launch/stop controls."""

    def __init__(self, root: tk.Tk, server: web.WebServer) -> None:
        self.root = root
        self.server = server
        self.switch_running = False
        self.switch_results = queue.SimpleQueue()
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()
        root.title("DDMY X6 · Flasher")
        root.geometry("500x540")
        root.resizable(False, False)
        root.configure(background=BACKGROUND)
        self.icon = tk.PhotoImage(file=str(toolkit.ROOT / "assets" / "x6-icon.png"))
        root.iconphoto(True, self.icon)
        root.iconbitmap(str(toolkit.ROOT / "assets" / "x6-icon.ico"))
        panel = tk.Frame(root, bg=BACKGROUND, padx=30, pady=26)
        panel.pack(fill="both", expand=True)
        top = tk.Frame(panel, bg=BACKGROUND)
        top.pack(fill="x")
        self.logo = self.icon.subsample(4)
        tk.Label(top, image=self.logo, bg=BACKGROUND).pack(side="left", padx=(0, 16))
        text = tk.Frame(top, bg=BACKGROUND)
        text.pack(side="left")
        tk.Label(text, text="DDMY X6", font=("Segoe UI", 21, "bold"), fg=INK, bg=BACKGROUND).pack(anchor="w")
        tk.Label(text, text=f"Windows-Helfer · {server.version}", font=("Segoe UI", 11), fg="#607771", bg=BACKGROUND).pack(anchor="w")
        self.status = tk.StringVar(value="●  Bereit für den Flasher")
        tk.Label(panel, textvariable=self.status, font=("Segoe UI", 12, "bold"),
                 fg=TEAL, bg=BACKGROUND, wraplength=440, justify="left").pack(anchor="w", pady=(25, 10))
        tk.Label(panel, text="Dieses Fenster während des Flashens geöffnet lassen.\n"
                 "Deine Dateien bleiben auf deinem Rechner.\nGerätezugriff erst mit „Verbinden“ im Flasher\n"
                 "oder mit den Geräteaktionen in dieser App.",
                 justify="left", font=("Segoe UI", 10), fg=INK, bg=BACKGROUND).pack(anchor="w")
        actions = tk.Frame(panel, bg=BACKGROUND)
        actions.pack(fill="x", pady=(25, 0))
        tk.Button(actions, text="Flasher öffnen  ↗", command=lambda: self.open_browser(True),
                  font=("Segoe UI", 11, "bold"), bg=TEAL, fg="white", activebackground="#106454",
                  activeforeground="white", relief="flat", cursor="hand2", padx=20, pady=10).pack(side="left")
        tk.Button(actions, text="Offline öffnen", command=lambda: self.open_browser(False),
                  font=("Segoe UI", 10), bg=BACKGROUND, fg=TEAL, relief="flat", cursor="hand2",
                  padx=15, pady=10).pack(side="left", padx=(8, 0))
        modes = tk.Frame(panel, bg=BACKGROUND)
        modes.pack(fill="x", pady=(15, 0))
        self.enter_button = tk.Button(modes, text="ADFU betreten", command=self.enter_adfu,
                                      font=("Segoe UI", 11, "bold"), bg="white", fg=TEAL,
                                      activebackground="#e2efea", relief="flat", cursor="hand2", padx=20, pady=10)
        self.enter_button.pack(side="left", padx=(0, 10))
        self.reset_button = tk.Button(modes, text="ADFU verlassen", command=self.leave_adfu,
                                      font=("Segoe UI", 11, "bold"), bg="white", fg=TEAL,
                                      activebackground="#e2efea", relief="flat", cursor="hand2", padx=20, pady=10)
        self.reset_button.pack(side="left")
        self.poweroff_button = tk.Button(panel, text="Player ausschalten", command=self.power_off,
                                         font=("Segoe UI", 11, "bold"), bg="white", fg=INK,
                                         activebackground="#e2efea", relief="flat", cursor="hand2", padx=20, pady=10)
        self.poweroff_button.pack(anchor="w", pady=(15, 0))
        tk.Label(panel, text="RAM-Funktion · USB kann das Gerät weiter versorgen.",
                 font=("Segoe UI", 9), fg="#607771", bg=BACKGROUND).pack(anchor="w", pady=(6, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)

    def open_browser(self, online: bool) -> None:
        """Open only the configured Pages URL or this instance's loopback site."""
        try:
            subprocess.Popen([str(web.browser_path()), self.server.launch_url(online)])
        except (OSError, toolkit.ToolkitError) as error:
            messagebox.showerror("Browser öffnen", str(error), parent=self.root)

    def enter_adfu(self) -> None:
        """Use the existing Windows SCSI switch without opening a website."""
        self._start_switch("enter")

    def leave_adfu(self) -> None:
        """Use the native WinUSB reset without opening a website."""
        self._start_switch("leave")

    def power_off(self) -> None:
        """Load, verify and execute the RAM power-off routine on explicit click."""
        self._start_switch("poweroff")

    def _start_switch(self, action: str) -> None:
        """Serialize all device buttons with each other and the web bridge switch."""
        if self.switch_running:
            return
        if not self.server.switch_lock.acquire(blocking=False):
            messagebox.showinfo("X6 wird verwendet", "Ein Geräteauftrag läuft bereits. Bitte dessen Ende abwarten.", parent=self.root)
            return
        self.switch_running = True
        self.switch_title, progress, self.switch_error = DEVICE_ACTIONS[action]
        for button in (self.enter_button, self.reset_button, self.poweroff_button):
            button.configure(state="disabled")
        self.status.set(progress)
        self.switch_worker = threading.Thread(target=self._switch_device, args=(action,), daemon=True)
        self.switch_worker.start()
        self.root.after(UI_POLL_MS, self._poll_switch)

    def _switch_device(self, action: str) -> None:
        """Only the worker touches USB; all widget updates stay on the Tk thread."""
        try:
            if action == "enter":
                device = toolkit.enter_adfu(self.server.profile)
                if device.mode != "adfu":
                    raise toolkit.ToolkitError("Der X6 wurde nicht im ADFU-Modus erkannt.")
                result = "X6 im ADFU-Modus erkannt."
            elif action == "poweroff":
                result = x6_poweroff.power_off(self.server.profile, self.server.runtime)
            else:
                result = x6_reset.leave_adfu(self.server.profile, self.server.payload("adfus.bin"))
            self.switch_results.put((True, result))
        except Exception as error:  # noqa: BLE001 - deliver worker failures to the GUI, never discard them.
            # Report unexpected worker errors too, so the UI never silently hangs.
            self.switch_results.put((False, str(error)))
        finally:
            self.server.switch_lock.release()

    def _poll_switch(self) -> None:
        try:
            success, message = self.switch_results.get_nowait()
        except queue.Empty:
            self.root.after(UI_POLL_MS, self._poll_switch)
            return
        self.switch_running = False
        for button in (self.enter_button, self.reset_button, self.poweroff_button):
            button.configure(state="normal")
        self.status.set(message if success else self.switch_error)
        if not success:
            messagebox.showerror(self.switch_title, message, parent=self.root)

    def close(self) -> None:
        if self.switch_running:
            messagebox.showinfo("Geräteauftrag läuft", "Bitte das Ergebnis des laufenden Geräteauftrags abwarten.", parent=self.root)
            return
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.root.destroy()


def main() -> None:
    """Start without a console; self-test checks packaged resources without USB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    root = tk.Tk()
    root.withdraw()
    try:
        # A known loopback port lets a directly opened Pages tab pair on click.
        # Packaging tests use an ephemeral port and never contact a running app.
        server = web.WebServer(0 if args.self_test else web.DEFAULT_PORT, toolkit.Profile.load(), online=True)
        for name in web.PAYLOAD_NAMES:
            server.payload(name)
        poweroff_payload = x6_poweroff.load_payload(server.profile, server.runtime)
        window = HelperWindow(root, server)
        root.update_idletasks()
        if args.self_test:
            http_checks = check_http_bridge(server)
            # Windows attaches window icons when the window is mapped. Map it
            # outside the visible desktop for this hardware-free packaging test.
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
            reset_button = window.reset_button.cget("text") == "ADFU verlassen" and window.reset_button.cget("state") == "normal"
            enter_button = window.enter_button.cget("text") == "ADFU betreten" and window.enter_button.cget("state") == "normal"
            poweroff_button = window.poweroff_button.cget("text") == "Player ausschalten" and window.poweroff_button.cget("state") == "normal"
            buttons_fit = all(button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
                              and button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
                              for button in (window.enter_button, window.reset_button, window.poweroff_button))
            args.self_test.write_text(json.dumps({"ok": all(icons) and reset_button and enter_button and poweroff_button and buttons_fit,
                                                 "helpers": list(web.PAYLOAD_NAMES),
                                                 "window_icons": icons, "http_pairing": http_checks,
                                                 "reset_button": reset_button,
                                                 "enter_button": enter_button,
                                                 "poweroff_button": poweroff_button,
                                                 "poweroff_payload_size": len(poweroff_payload),
                                                 "buttons_fit": buttons_fit,
                                                 "device_access": False}), encoding="utf-8")
            window.close()
            return
        root.deiconify()
        if not args.no_browser:
            root.after(250, lambda: window.open_browser(True))
        root.mainloop()
    except (OSError, ValueError, tk.TclError, toolkit.ToolkitError) as error:
        if args.self_test:
            args.self_test.write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
        else:
            messagebox.showerror("X6-Helfer konnte nicht starten", str(error), parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
