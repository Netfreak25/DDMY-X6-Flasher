"""Serve the local X6 WebUSB UI and its small Windows ADFU bridge."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

import x6_toolkit as toolkit

WEB_ROOT = toolkit.ROOT
SITE_CONFIG = WEB_ROOT / "site.json"
DEFAULT_PORT = urlsplit(json.loads(SITE_CONFIG.read_text(encoding="utf-8"))["helper_origin"]).port
PAYLOAD_NAMES = ("adfus.bin", "fwsc.bin", "nandhwsc.bin", "nandinfo.bin", "nandread.bin", "vram_last.bin")
DEFAULT_PAYLOADS = WEB_ROOT / "runtime" if getattr(sys, "frozen", False) else toolkit.DEFAULT_RUNTIME


class WebServer(ThreadingHTTPServer):
    """Same-origin loopback service; never exposes firmware or arbitrary commands."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int, profile: toolkit.Profile, runtime: Path = DEFAULT_PAYLOADS, online: bool = False) -> None:
        self.profile = profile
        self.runtime = runtime.resolve()
        self.token = secrets.token_urlsafe(32)
        self.switch_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        site_config = json.loads(SITE_CONFIG.read_text(encoding="utf-8"))
        self.pages_url = site_config["pages_url"]
        self.version = site_config["version"]
        self.allowed_origins = {self.origin}
        if online:
            site = urlsplit(self.pages_url)
            if site.scheme != "https" or not site.hostname or site.username or site.password:
                raise toolkit.ToolkitError("Ungültige HTTPS-Adresse in site.json.")
            self.allowed_origins.add(f"{site.scheme}://{site.netloc}")

    def server_bind(self) -> None:
        """Own the loopback port exclusively, including against legacy helpers.

        HTTPServer enables SO_REUSEADDR by default. On Windows this allows
        multiple listeners on one port and makes request routing indeterminate.
        SO_EXCLUSIVEADDRUSE must be set before bind to prevent that collision.
        """
        try:
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
        except OSError as error:
            raise toolkit.ToolkitError(
                f"Der lokale X6-Port {self.server_address[1]} ist nicht verfügbar. "
                "Bitte bereits laufende X6-Apps oder alte X6-Webstarter schließen. "
                f"Die App wurde nicht gestartet. Details: {error}"
            ) from error

    def launch_url(self, online: bool) -> str:
        """Pass credentials in the URL fragment, never to the public web server."""
        base = self.pages_url if online else f"{self.origin}/"
        return base + "#" + urlencode({"session": self.token, "bridge": self.origin})

    def payload(self, name: str) -> bytes:
        """Serve only the six release-approved generic RAM helpers, hash checked."""
        if name not in PAYLOAD_NAMES:
            raise toolkit.ToolkitError("Unbekanntes Hilfsprogramm.")
        data = (self.runtime / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != self.profile.runtime_sha256.get(name):
            raise toolkit.ToolkitError(f"Prüfsumme des Hilfsprogramms stimmt nicht: {name}")
        return data


class Handler(BaseHTTPRequestHandler):
    """Serve static assets plus the authenticated, deliberately small bridge API."""

    server: WebServer

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep the starter quiet; the UI offers operation reports separately."""

    def reply(self, status: int, data: bytes, content_type: str = "application/json") -> None:
        """Send local-only content with browser isolation headers."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Permissions-Policy", "usb=(self)")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                         "img-src 'self' data:; worker-src 'self' blob:; frame-ancestors 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json_reply(self, status: int, value: object) -> None:
        self.reply(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def authorized(self) -> bool:
        """Reject cross-origin requests and require the per-launch session secret."""
        return (
            self.headers.get("Host") == urlsplit(self.server.origin).netloc
            and self.headers.get("Origin", self.server.origin) in self.server.allowed_origins
            and (self.headers.get("Origin") in self.server.allowed_origins
                 or self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none"))
            and secrets.compare_digest(self.headers.get("X-X6-Session", ""), self.server.token)
        )

    def do_OPTIONS(self) -> None:
        """Preflight either origin-checked pairing or the session-protected API."""
        headers = {item.strip().lower() for item in self.headers.get("Access-Control-Request-Headers", "").split(",") if item.strip()}
        pairing = self.path == "/api/pair"
        expected_headers = {"x-x6-pair"} if pairing else {"x-x6-session"}
        methods = ("POST",) if pairing else ("GET", "POST")
        if (self.headers.get("Host") != urlsplit(self.server.origin).netloc
                or self.headers.get("Origin") not in self.server.allowed_origins
                or not self.path.startswith("/api/")
                or self.headers.get("Access-Control-Request-Method") not in methods
                or headers != expected_headers):
            self.json_reply(403, {"error": "Nicht freigegebene Website oder Anfrage."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", ", ".join(methods))
        self.send_header("Access-Control-Allow-Headers", ", ".join(expected_headers))
        self.end_headers()

    def pair(self) -> None:
        """Give the configured website a session without touching the player.

        The mandatory custom header forces browser preflight. Both preflight
        and the POST require the exact configured origin and loopback Host;
        an ordinary form or an unrelated website cannot obtain a session.
        All hardware endpoints continue to require the random session token.
        """
        if (self.headers.get("Host") != urlsplit(self.server.origin).netloc
                or self.headers.get("Origin") not in self.server.allowed_origins
                or self.headers.get("X-X6-Pair") != "1"):
            self.json_reply(403, {"error": "Diese Website darf sich nicht mit der App koppeln."})
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
            self.json_reply(400, {"error": "Die Kopplung nimmt keine Nutzdaten an."})
            return
        self.json_reply(200, {"origin": self.server.origin, "token": self.server.token})

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self.json_reply(403, {"error": "Ungültiger lokaler Host."})
            return
        if path.startswith("/api/"):
            if not self.authorized():
                self.json_reply(403, {"error": "Sitzung abgelaufen. Bitte den X6-Starter erneut öffnen."})
                return
            try:
                if path == "/api/status":
                    self.json_reply(200, dataclasses.asdict(toolkit.probe_device(self.server.profile)))
                elif path == "/api/profile":
                    profile = dataclasses.asdict(self.server.profile)
                    profile["nand_id"] = self.server.profile.nand_id.hex()
                    profile["runtime_sha256"] = {name: profile["runtime_sha256"][name] for name in PAYLOAD_NAMES}
                    self.json_reply(200, profile)
                elif path.startswith("/api/payloads/"):
                    self.reply(200, self.server.payload(path.removeprefix("/api/payloads/")), "application/octet-stream")
                else:
                    self.json_reply(404, {"error": "Unbekannte Funktion."})
            except (OSError, toolkit.ToolkitError) as exc:
                self.json_reply(409, {"error": str(exc)})
            return
        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        allowed = relative in ("index.html", "styles.css", "app.js", "site.json", "tools/x6_profile.json") or relative.startswith(("lib/", "vendor/", "assets/"))
        if not allowed or not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
            self.json_reply(404, {"error": "Datei nicht gefunden."})
            return
        content_type = "text/javascript" if target.suffix == ".js" else mimetypes.guess_type(target.name)[0]
        self.reply(200, target.read_bytes(), (content_type or "application/octet-stream") + "; charset=utf-8")

    def do_POST(self) -> None:
        if self.path == "/api/pair":
            self.pair()
            return
        if not self.authorized():
            self.json_reply(403, {"error": "Keine gültige lokale Sitzung."})
            return
        if self.path != "/api/enter-adfu":
            self.json_reply(404, {"error": "Unbekannte Funktion."})
            return
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
            self.json_reply(400, {"error": "Diese Funktion nimmt keine Nutzdaten an."})
            return
        if not self.server.switch_lock.acquire(blocking=False):
            self.json_reply(409, {"error": "Der Wechsel nach ADFU läuft bereits."})
            return
        try:
            self.json_reply(200, dataclasses.asdict(toolkit.enter_adfu(self.server.profile)))
        except (OSError, toolkit.ToolkitError) as exc:
            self.json_reply(409, {"error": str(exc)})
        finally:
            self.server.switch_lock.release()


def browser_path() -> Path:
    """Prefer Chrome; use Edge only when Chrome is not installed."""
    import winreg

    for name in ("chrome.exe", "msedge.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}",
                                        0, winreg.KEY_READ | view) as key:
                        candidate = Path(winreg.QueryValue(key, None).strip('"'))
                except OSError:
                    continue
                if candidate.is_file():
                    return candidate
    raise toolkit.ToolkitError("Bitte Chrome oder Microsoft Edge installieren.")


def main() -> None:
    """Start the loopback website using the existing private runtime directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("X6_WEB_PORT", DEFAULT_PORT)))
    parser.add_argument("--online", action="store_true", help="Open the configured GitHub Pages website")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    with WebServer(args.port, toolkit.Profile.load(), online=args.online) as server:
        if not args.no_browser:
            subprocess.Popen([str(browser_path()), server.launch_url(args.online)])
        print(f"X6 Web Toolkit: {server.origin} (zum Beenden Strg+C)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
