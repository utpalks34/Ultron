"""Manual escape hatch for a stuck Ultron backend. Never auto-invoked.

Run from backend-ai/ with the venv active:  python ..\scripts\kill_stale_backend.py
Reads settings.pid_file, and only terminates the process after you type "yes"."""
import sys
from pathlib import Path

import psutil

from config import settings


def main() -> int:
    path = Path(settings.pid_file)
    if not path.exists():
        print(f"no pid file at {path.resolve()}; nothing to do")
        return 0
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        print(f"{path.resolve()} does not contain a pid; deleting it")
        path.unlink()
        return 0

    try:
        proc = psutil.Process(pid)
        alive = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        name = proc.name()
    except psutil.Error:
        alive, name = False, ""

    if not alive:
        print(f"pid {pid} is not running; deleting the stale pid file {path.resolve()}")
        path.unlink()
        return 0
    if "python" not in name.lower():
        print(f"pid {pid} is running but is '{name}', not a python process; it is probably a "
              f"reused pid. Leaving it alone. Delete {path.resolve()} by hand if it is stale.")
        return 1

    print(f"pid {pid} ({name}) looks like a running Ultron backend.")
    print(f"  command line: {' '.join(proc.cmdline())[:200]}")
    print("It would be terminated (5 s grace, then killed) and the pid file deleted.")
    if input('Type "yes" to continue: ').strip() != "yes":
        print("aborted; nothing was changed")
        return 1

    try:
        proc.terminate()
        try:
            proc.wait(5)
            print("terminated")
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(5)
            print("did not exit within 5 s; killed")
    except psutil.Error as exc:
        print(f"could not stop pid {pid}: {exc}")
        return 1
    path.unlink(missing_ok=True)
    print(f"deleted {path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
