"""Terminal reading for the dev agent: find and raise a window, capture it, OCR it.

Windows only (ctypes, no pywin32). There is no vision model: the terminal panel is read with
OCR (RapidOCR on onnxruntime) and the text goes to the GPU worker. Nothing here decides risk or
asks for confirmation: callers go through tools.registry.gate. The pure helpers (terminal_crop,
to_png_b64, tail_text) are unit-tested offline; the Win32 calls are blocking, so async callers
wrap them in asyncio.to_thread.
"""
import asyncio
import base64
import ctypes
import functools
import io
import subprocess
import threading
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

from config import settings
from tools import desktop   # also switches the process to per-monitor DPI awareness

SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
PW_RENDERFULLCONTENT = 2
BI_RGB = 0
DIB_RGB_COLORS = 0
BLACK_MAX = 8            # an image whose brightest channel value is below this is "effectively black"
TERMINAL_FRACTION = 0.35

_OCR_HINT = "OCR is not installed. Run: pip install rapidocr-onnxruntime onnxruntime"


class VisionError(RuntimeError):
    pass


# --------------------------------------------------------------------------- Win32

class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


@functools.lru_cache(maxsize=1)
def _api():
    # own library objects: the argtypes set here do not touch the ones in tools.desktop
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.restype = None
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    return user32, gdi32


def _rect(user32, hwnd) -> tuple[int, int, int, int] | None:
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def find_window(title_contains: str, process_name: str | None = None) -> dict | None:
    """First visible window whose title contains the text (case-insensitive): dict(hwnd, title,
    rect) with rect = (left, top, right, bottom) in physical pixels. `process_name` narrows the
    match (e.g. "Code.exe"), so a browser tab titled "... Visual Studio Code" is not mistaken
    for the editor."""
    user32 = _api()[0]
    needle = title_contains.lower()
    proc = process_name.lower() if process_name else None
    for w in desktop.visible_windows():
        if needle not in w["title"].lower():
            continue
        if proc and w["name"].lower() != proc:
            continue
        rect = _rect(user32, w["hwnd"])
        if rect is not None:
            return {"hwnd": w["hwnd"], "title": w["title"], "rect": rect}
    return None


def bring_to_front(hwnd: int) -> bool:
    """Restore if minimized, lift Windows' foreground lock with a dummy Alt press, raise the
    window, and VERIFY it is now the foreground window before the caller acts on it."""
    user32 = _api()[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def capture_window(hwnd: int) -> Image.Image | None:
    """PrintWindow(PW_RENDERFULLCONTENT) into a bitmap: captures the window even when another
    window (the always-on-top HUD) overlaps it. None when it fails or comes back effectively
    black (GPU-composited windows can do that); the caller falls back to grab_region()."""
    user32, gdi32 = _api()
    rect = _rect(user32, hwnd)
    if rect is None:
        return None
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= 0 or height <= 0:
        return None
    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        return None
    hdc_mem = bmp = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
        bmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
        if not hdc_mem or not bmp:
            return None
        old = gdi32.SelectObject(hdc_mem, bmp)
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        gdi32.SelectObject(hdc_mem, old)          # GetDIBits needs the bitmap out of the DC
        if not ok:
            return None
        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth, header.biHeight = width, -height        # negative: top-down rows
        header.biPlanes, header.biBitCount, header.biCompression = 1, 32, BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(hdc_win, bmp, 0, height, buf, ctypes.byref(header), DIB_RGB_COLORS)
        if lines != height:
            return None
        img = Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1)
    finally:
        if bmp:
            gdi32.DeleteObject(bmp)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)
    if max(band_max for _, band_max in img.getextrema()) < BLACK_MAX:
        return None
    return img


def grab_region(rect) -> Image.Image:
    """Fallback: a plain screen grab of the window's rectangle. It shows whatever is on top, so
    the Ultron HUD may overlap the result; the caller says so."""
    return ImageGrab.grab(bbox=tuple(rect), all_screens=True)


# --------------------------------------------------------------------------- pure image / text

def terminal_crop(img: Image.Image) -> Image.Image:
    """PURE: the bottom 35% of the image, where VS Code keeps the terminal panel."""
    w, h = img.size
    keep = max(1, round(h * TERMINAL_FRACTION))
    return img.crop((0, h - keep, w, h))


def to_png_b64(img: Image.Image, max_w: int = 1280) -> str:
    """PURE: downscale (keeping the ratio) to at most max_w wide, PNG, base64 text."""
    if img.width > max_w:
        img = img.resize((max_w, max(1, round(img.height * max_w / img.width))), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def tail_text(text: str, max_chars: int) -> str:
    """PURE: the LAST max_chars characters, starting at a line boundary (errors sit at the
    bottom, so the top is what gets cut)."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    kept = text[-max_chars:]
    if text[-max_chars - 1] != "\n":            # the cut landed inside a line: drop that piece
        nl = kept.find("\n")
        if nl != -1:
            kept = kept[nl + 1:]
    return kept


# --------------------------------------------------------------------------- OCR

_engine = None
_engine_lock = threading.Lock()


def _load_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError as exc:
                    raise VisionError(_OCR_HINT) from exc
            _engine = RapidOCR()
        return _engine


def _order_lines(items) -> str:
    """PURE: [(y, x, text)] -> the text lines ordered top to bottom, then left to right."""
    return "\n".join(t for _, _, t in sorted(items, key=lambda i: (i[0], i[1])) if t.strip())


def _adapt(result) -> str:
    """PURE: turns either RapidOCR result shape into ordered text. New package: an object with
    .txts and .boxes. Old package: (list of [box, text, score], elapsed)."""
    items = []
    if isinstance(result, tuple):
        for entry in (result[0] or []):
            box, text = entry[0], str(entry[1])
            items.append((min(p[1] for p in box), min(p[0] for p in box), text))
    else:
        txts = getattr(result, "txts", None)
        boxes = getattr(result, "boxes", None)
        if txts is None:
            return ""
        for i, text in enumerate(txts):
            if boxes is not None and i < len(boxes):
                box = boxes[i]
                items.append((float(min(p[1] for p in box)), float(min(p[0] for p in box)),
                              str(text)))
            else:
                items.append((float(i), 0.0, str(text)))
    return _order_lines(items)


def ocr(img: Image.Image) -> str:
    """Text of the image, one line per detected line, top to bottom. Slow (seconds) and
    blocking: call it through asyncio.to_thread. Raises VisionError when OCR is missing."""
    import numpy as np
    engine = _load_engine()
    try:
        bgr = np.ascontiguousarray(np.asarray(img.convert("RGB"))[:, :, ::-1])
        return _adapt(engine(bgr))
    except VisionError:
        raise
    except Exception as exc:
        raise VisionError(f"OCR failed: {type(exc).__name__}: {str(exc)[:160]}") from exc


# --------------------------------------------------------------------------- clipboard

def _get_clipboard() -> str:
    # UTF-8 on the pipe, otherwise PowerShell encodes in the console's OEM code page
    cmd = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard"
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VisionError(f"could not read the clipboard: {exc}") from exc
    if res.returncode != 0:
        raise VisionError("could not read the clipboard (PowerShell failed)")
    return res.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()


async def read_clipboard(max_chars: int | None = None) -> str:
    """The clipboard text (the last max_chars characters, default ocr_max_chars); '' when empty
    or when it does not hold text."""
    text = await asyncio.to_thread(_get_clipboard)
    return tail_text(text, max_chars if max_chars is not None else settings.ocr_max_chars)
