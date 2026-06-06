"""Shared helpers for the GIF generators: seed the DB and run a temp server."""
from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_db() -> None:
    if not (ROOT / "demo.db").exists():
        print("seeding demo.db …")
        subprocess.run([sys.executable, "seed.py"], cwd=ROOT, check=True)


@contextlib.contextmanager
def running_server():
    """Start uvicorn on a free port, yield its base URL, and always tear it down."""
    ensure_db()
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            if proc.poll() is not None:  # fail fast if uvicorn died
                raise RuntimeError(f"uvicorn exited prematurely with code {proc.returncode}")
            try:
                urllib.request.urlopen(f"{base}/healthz", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("server did not come up")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
