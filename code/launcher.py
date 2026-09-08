from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
ROOT = CODE_DIR.parent
STORAGE = ROOT / "storage"
DB_DIR = STORAGE / "database"
LOG_DIR = STORAGE / "logs"
SERVER_PID = DB_DIR / "server.pid"
OVERLAY_PID = DB_DIR / "overlay.pid"
SERVER_URL = "http://127.0.0.1:8765"
STATE_URL = SERVER_URL + "/api/state"


def ensure_dirs() -> None:
    for p in (STORAGE / "data", DB_DIR, STORAGE / "exports", LOG_DIR):
        p.mkdir(parents=True, exist_ok=True)


def server_alive(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(STATE_URL, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_file_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        if pid_exists(pid):
            return True
    except Exception:
        pass
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def spawn_hidden(args: list[str], *, log_file=None) -> subprocess.Popen:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        args,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log_file if log_file is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if log_file is not None else subprocess.DEVNULL,
        close_fds=True,
        **kwargs,
    )


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, "Search", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> int:
    ensure_dirs()

    # The launcher is normally started with pythonw.exe. Reuse that executable
    # so the server and overlay also remain console-free.
    python_exe = Path(sys.executable)
    if python_exe.name.lower() == "python.exe":
        candidate = python_exe.with_name("pythonw.exe")
        if candidate.exists():
            python_exe = candidate

    if not server_alive():
        log_path = LOG_DIR / "server.log"
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        log.write("\n--- Search server start ---\n")
        try:
            spawn_hidden([str(python_exe), str(CODE_DIR / "app.py")], log_file=log)
        except Exception as exc:
            log.close()
            show_error(f"Could not start Search server.\n\n{exc}\n\nLog: {log_path}")
            return 1

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if server_alive():
                break
            time.sleep(0.2)
        else:
            log.close()
            show_error(
                "Search server did not become ready.\n\n"
                f"Check the log at:\n{log_path}"
            )
            return 1
        log.close()

    # Only one overlay instance. The overlay owns this pid file and removes it
    # on normal shutdown; stale pid files are cleaned automatically.
    if not pid_file_alive(OVERLAY_PID):
        try:
            spawn_hidden([str(python_exe), str(CODE_DIR / "overlay.py")])
        except Exception as exc:
            show_error(f"The Search server started, but the overlay could not start.\n\n{exc}")
            return 1

        # Give the overlay a moment to create its pid file. This is not required
        # for correctness, but prevents rapid double-clicks from racing.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if pid_file_alive(OVERLAY_PID):
                break
            time.sleep(0.1)

    webbrowser.open(SERVER_URL, new=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
