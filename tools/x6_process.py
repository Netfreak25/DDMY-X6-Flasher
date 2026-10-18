"""Stream a hidden backend process without retrying a failed command."""

from __future__ import annotations

import queue
import subprocess
import threading
import time


def run_streamed(command: list[str], env: dict[str, str], timeout: float, output) -> subprocess.CompletedProcess:
    """Deliver complete CR/LF lines; kill on timeout and preserve the transcript."""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    chunks = queue.SimpleQueue()

    def read() -> None:
        try:
            while data := process.stdout.read1(4096):
                chunks.put(data)
        finally:
            chunks.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    transcript = bytearray()
    pending = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout, output=bytes(transcript))
            try:
                data = chunks.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue
            if data is None:
                break
            transcript.extend(data)
            for byte in data:
                if byte in (10, 13):
                    if pending:
                        output(pending.decode("utf-8", errors="replace"))
                        pending.clear()
                else:
                    pending.append(byte)
        if pending:
            output(pending.decode("utf-8", errors="replace"))
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, code, transcript.decode("utf-8", errors="replace"), "")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=2)
        process.stdout.close()
