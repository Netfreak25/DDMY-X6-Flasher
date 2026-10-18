"""One explicit device owner shared by the desktop and the Pages bridge."""

from __future__ import annotations

import secrets
import threading
from contextlib import contextmanager

import x6_toolkit as toolkit
from x6_toolkit import ToolkitError


class DeviceJobs:
    """A browser owns the device until disconnect; leases never expire mid-write."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self.owner: str | None = None
        self._file = None

    def acquire(self, owner: str) -> str:
        with self._lock:
            if self._token:
                raise ToolkitError(f"X6 wird bereits verwendet: {self.owner}. Bitte dort zuerst trennen.")
            # Windows byte locks also serialize separately launched app instances.
            import msvcrt

            toolkit.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
            handle = (toolkit.PRIVATE_ROOT / "device.lock").open("a+b")
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                handle.close()
                raise ToolkitError("Eine andere X6-App verwendet das Gerät bereits.") from error
            self._file = handle
            self._token = secrets.token_urlsafe(32)
            self.owner = owner
            return self._token

    def check(self, token: str) -> None:
        with self._lock:
            if not token or not self._token or not secrets.compare_digest(token, self._token):
                raise ToolkitError("Keine gültige Gerätefreigabe. Bitte erneut verbinden.")

    def release(self, token: str) -> None:
        with self._lock:
            if not token or not self._token or not secrets.compare_digest(token, self._token):
                raise ToolkitError("Die Gerätefreigabe gehört zu einer anderen Sitzung.")
            self._token = None
            self.owner = None
            self._file.close()
            self._file = None

    @contextmanager
    def native(self):
        """Hold ownership for the entire operation, including final readback."""
        token = self.acquire("Windows-App")
        try:
            yield
        finally:
            self.release(token)
