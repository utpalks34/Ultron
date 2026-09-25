import asyncio
import subprocess

import pytest

from tools import desktop
from tools.desktop import (PROTECTED_NAMES, DesktopError, classify_launch, close_process,
                           filter_targets)


def test_classify_launch():
    assert classify_launch("whatsapp://") == "uri"
    assert classify_launch("code") == "command"
    assert classify_launch(r"C:\Windows\notepad.exe") == "path"


@pytest.mark.parametrize("name", ["explorer.exe", "ollama.exe", "postgres.exe", "ultron.exe",
                                  "windowsterminal.exe"])
def test_protected_names_contain(name):
    assert name in PROTECTED_NAMES


def _w(pid, name, title):
    return {"pid": pid, "name": name, "title": title, "hwnd": pid}


def test_filter_targets_drops_protected_and_groups():
    windows = [
        _w(1, "Explorer.EXE", "Documents"),          # protected name, different case
        _w(99, "mystery.exe", "Some window"),        # protected pid
        _w(5, "notepad.exe", "b.txt"),
        _w(2, "code.exe", "main.py - Visual Studio Code"),
        _w(2, "code.exe", "notes.md - Visual Studio Code"),
    ]
    targets = filter_targets(windows, PROTECTED_NAMES, {99})
    assert [t["name"] for t in targets] == ["code.exe", "notepad.exe"]
    code = targets[0]
    assert code["pid"] == 2
    assert code["titles"] == ["main.py - Visual Studio Code", "notes.md - Visual Studio Code"]


def test_close_process_rejects_injection_before_running_anything(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(desktop.subprocess, "run", boom)
    with pytest.raises(DesktopError):
        asyncio.run(close_process("evil.exe & calc"))
