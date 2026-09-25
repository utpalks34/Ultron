"""Windows desktop control: window enumeration, app launch and close, volume keys.

ctypes and psutil only (no pyautogui, no pygetwindow). Nothing here decides risk or asks for
confirmation: callers go through tools.registry.gate. The pure helpers (classify_launch,
filter_targets) are unit-tested offline; the Win32 calls are checked with scripts/list_windows.py.
"""
import asyncio
import ctypes
import functools
import os
import re
import subprocess
import time
from ctypes import wintypes

import psutil

# Hard-coded on purpose, never a database column: "close everything" must not be able to kill the
# system's own dependencies. The terminals are protected because closing them would kill the
# terminal hosting this backend.
PROTECTED_NAMES = frozenset({
    "explorer.exe", "ollama.exe", "ollama app.exe", "postgres.exe",
    "python.exe", "pythonw.exe", "ultron.exe", "msedgewebview2.exe", "windowsterminal.exe",
    "wt.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe", "openconsole.exe",
    "applicationframehost.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "shellexperiencehost.exe", "dwm.exe", "taskmgr.exe",
})

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_CLOAKED = 14

VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
KEYEVENTF_KEYUP = 0x0002
VOLUME_STEP_PERCENT = 2       # one media-key press moves the system volume by 2%

_EXE_NAME = re.compile(r"[\w .\-()+]+\.exe", re.IGNORECASE)   # no shell metacharacters, no "/"

try:                           # per-monitor DPI awareness, once
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


class DesktopError(RuntimeError):
    pass


# --------------------------------------------------------------------------- windows

@functools.lru_cache(maxsize=1)
def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [proto, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.restype = None
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    return user32, dwm, proto


def ancestor_pids() -> set[int]:
    """This process and all its parents: protects the terminal or IDE that launched the backend,
    whatever it is called."""
    pids = {os.getpid()}
    try:
        pids.update(p.pid for p in psutil.Process().parents())
    except psutil.Error:
        pass
    return pids


def _inspect(user32, dwm, hwnd) -> dict | None:
    if not user32.IsWindowVisible(hwnd):
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return None
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip()
    if not title:
        return None
    if user32.GetWindow(hwnd, GW_OWNER):                       # owned popup or dialog
        return None
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:      # tool window
        return None
    cloaked = wintypes.DWORD(0)                                # suspended UWP windows
    hr = dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked),
                                   ctypes.sizeof(cloaked))
    if hr == 0 and cloaked.value != 0:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return {"pid": pid.value, "name": name, "title": title, "hwnd": int(hwnd or 0)}


def visible_windows() -> list[dict]:
    """Top-level windows a user would see in the taskbar: dict(pid, name, title, hwnd)."""
    user32, dwm, proto = _api()
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        try:
            w = _inspect(user32, dwm, hwnd)
            if w:
                out.append(w)
        except Exception:
            pass                                               # never let one window stop the walk
        return True

    cb = proto(_cb)                                            # keep a reference during the call
    user32.EnumWindows(cb, 0)
    return out


def filter_targets(windows, protected_names, protected_pids) -> list[dict]:
    """PURE: drop protected names (case-insensitive) and pids, group by process name, sort by name.
    Returns [{name, titles, pid}]."""
    names = {n.lower() for n in protected_names}
    pids = set(protected_pids)
    groups: dict[str, dict] = {}
    for w in windows:
        if w["name"].lower() in names or w["pid"] in pids:
            continue
        g = groups.setdefault(w["name"].lower(),
                              {"name": w["name"], "titles": [], "pid": w["pid"]})
        g["titles"].append(w["title"])
    return sorted(groups.values(), key=lambda g: g["name"].lower())


# --------------------------------------------------------------------------- launch / close

def classify_launch(cmd: str) -> str:
    """PURE: 'uri' (whatsapp://...), 'path' (absolute path) or 'command' (resolved by the shell)."""
    if "://" in cmd:
        return "uri"
    if os.path.isabs(cmd):
        return "path"
    return "command"


def _start_command(cmd: str) -> None:
    proc = subprocess.Popen(f'start "" {cmd}', shell=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        code = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return                                                 # still running: treat as launched
    if code != 0:
        raise DesktopError(
            f"could not start {cmd!r} (exit code {code}); if it is a command such as 'code', "
            "make sure it is on PATH (for VS Code, enable Add to PATH in the installer)")


async def launch(cmd: str) -> None:
    """Start an application or protocol handler. cmd comes from the user's own registry, never
    from the model or the utterance."""
    kind = classify_launch(cmd)
    try:
        if kind == "command":
            await asyncio.to_thread(_start_command, cmd)
        else:
            await asyncio.to_thread(os.startfile, cmd)
    except DesktopError:
        raise
    except OSError as exc:
        raise DesktopError(f"could not open {cmd!r}: {exc}") from exc


def _running(name: str) -> bool:
    target = name.lower()
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == target:
            return True
    return False


async def close_process(name: str, force: bool = False) -> dict:
    """taskkill by image name; graceful unless force. Returns {closed, detail}."""
    if not _EXE_NAME.fullmatch(name):
        raise DesktopError(f"refusing to close {name!r}: not a plain process name")
    args = ["taskkill", "/IM", name] + (["/F"] if force else [])
    try:
        res = await asyncio.to_thread(subprocess.run, args, capture_output=True, text=True,
                                      errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopError(f"taskkill failed for {name!r}: {exc}") from exc
    await asyncio.sleep(1.5)
    still = await asyncio.to_thread(_running, name)
    detail = " ".join((res.stderr or res.stdout or "").split())[:160]
    return {"closed": not still, "detail": detail}


# --------------------------------------------------------------------------- volume

# These change the SYSTEM volume (media keys), not mpv's. Blocking: call through asyncio.to_thread.

def _press(vk: int) -> None:
    user32 = _api()[0]
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.01)


def volume_step(direction: str, presses: int = 5) -> None:
    vk = {"up": VK_VOLUME_UP, "down": VK_VOLUME_DOWN, "mute": VK_VOLUME_MUTE}.get(direction)
    if vk is None:
        raise DesktopError(f"unknown volume direction {direction!r}")
    for _ in range(1 if direction == "mute" else max(0, presses)):
        _press(vk)


def volume_set(percent: int) -> None:
    """Absolute level: 50 presses down (to 0), then round(percent / 2) up; each press is 2%."""
    percent = max(0, min(100, int(percent)))
    volume_step("down", 100 // VOLUME_STEP_PERCENT)
    volume_step("up", round(percent / VOLUME_STEP_PERCENT))
