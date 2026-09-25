"""run_os_action with fakes only: no Ollama, no database, no desktop, no mpv, no WhatsApp."""
import asyncio
import os
import urllib.parse

from langchain_core.messages import HumanMessage

from graph.agents import os_worker
from graph.agents.os_worker import OsAction, OsToolCall, run_os_action
from tools import approvals, desktop, music
from tools import resolve as resolver
from tools.context import ToolContext

POOL = object()      # stands in for the asyncpg pool wherever resolve() is faked


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, e):
        self.events.append(e)


class FakePool:
    """Only close_all touches the pool directly: the protected rows of registry_apps."""
    def __init__(self, protected=()):
        self.protected = list(protected)

    async def fetch(self, sql, *args):
        return [{"process_name": p} for p in self.protected]


def _ctx(bus):
    return ToolContext(bus, None, "r1", "os_agent")


async def _wait_pending():
    for _ in range(500):          # close_all reaches the gate after a thread hop: wait in real time
        if approvals.pending_events():
            return approvals.pending_events()[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no confirm_request became pending")


def _forbid_side_effects(monkeypatch, calls):
    async def async_forbidden(*a, **k):
        calls.append("async")
        raise AssertionError("a side effect ran")

    def forbidden(*a, **k):
        calls.append("sync")
        raise AssertionError("a side effect ran")

    monkeypatch.setattr(desktop, "launch", async_forbidden)
    monkeypatch.setattr(desktop, "close_process", async_forbidden)
    monkeypatch.setattr(desktop, "volume_step", forbidden)
    monkeypatch.setattr(desktop, "volume_set", forbidden)
    monkeypatch.setattr(music, "play", async_forbidden)
    for name in ("pause", "resume", "next_track", "previous_track"):
        monkeypatch.setattr(music, name, forbidden)
    monkeypatch.setattr(os, "startfile", forbidden, raising=False)


WINDOWS = [
    {"pid": 10, "name": "notepad.exe", "title": "notes - Notepad", "hwnd": 1},
    {"pid": 11, "name": "calc.exe", "title": "Calculator", "hwnd": 2},
    {"pid": 12, "name": "explorer.exe", "title": "Files", "hwnd": 3},
    {"pid": 13, "name": "ollama.exe", "title": "Ollama", "hwnd": 4},
    {"pid": 14, "name": "python.exe", "title": "backend", "hwnd": 5},
    {"pid": 15, "name": "SecretSvc.exe", "title": "Marked protected in the registry", "hwnd": 6},
]


def _fake_desktop(monkeypatch, closed):
    monkeypatch.setattr(desktop, "visible_windows", lambda: list(WINDOWS))
    monkeypatch.setattr(desktop, "ancestor_pids", lambda: set())

    async def close_process(name, force=False):
        closed.append(name)
        return {"closed": name != "notepad.exe", "detail": ""}

    monkeypatch.setattr(desktop, "close_process", close_process)


def _run_close_all(approve):
    async def main():
        bus = FakeBus()
        pool = FakePool(protected=["secretsvc.exe"])
        task = asyncio.create_task(run_os_action(_ctx(bus), pool, OsAction(kind="close_all")))
        evt = await _wait_pending()
        await approvals.resolve(evt["payload"]["action_id"], approve)
        return evt, await task, bus
    return asyncio.run(main())


def test_unsupported_makes_no_side_effect(monkeypatch):
    calls = []
    _forbid_side_effects(monkeypatch, calls)
    bus = FakeBus()
    answer = asyncio.run(run_os_action(_ctx(bus), POOL, OsAction(kind="unsupported")))
    assert "can't shut down" in answer
    assert calls == []
    assert all(e["type"] == "tool_call" for e in bus.events)


def test_close_all_denied_closes_nothing(monkeypatch):
    closed = []
    _fake_desktop(monkeypatch, closed)
    evt, answer, bus = _run_close_all(approve=False)
    assert evt["type"] == "confirm_request"
    assert evt["payload"]["risk"] == "destructive"
    assert evt["run_id"] == "r1"
    assert closed == []
    assert "closed nothing" in answer


def test_close_all_lists_only_unprotected_apps(monkeypatch):
    closed = []
    _fake_desktop(monkeypatch, closed)
    evt, answer, bus = _run_close_all(approve=True)
    description = evt["payload"]["description"].lower()
    assert description.startswith("close 2 apps")
    assert "notepad.exe" in description and "calc.exe" in description
    for protected in ("explorer", "ollama", "python", "secretsvc"):
        assert protected not in description
    assert sorted(closed) == ["calc.exe", "notepad.exe"]     # nothing protected was touched
    assert "Closed 1 of 2" in answer and "notepad.exe" in answer    # the one still running


def test_whatsapp_composes_only_with_an_encoded_url(monkeypatch):
    calls = []
    _forbid_side_effects(monkeypatch, calls)      # nothing but os.startfile may run, and only once
    opened = []
    monkeypatch.setattr(os, "startfile", lambda url: opened.append(url), raising=False)

    async def fake_resolve(pool, kind, phrase, limit=3):
        assert kind == "contact"
        return "one", [{"name": "Arjun", "phone": "+91 98765-43210"}]

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    message = "I'm running late & need 10/15 mins"
    bus = FakeBus()
    answer = asyncio.run(run_os_action(
        _ctx(bus), POOL, OsAction(kind="whatsapp", name="arjun", message=message)))

    assert calls == []
    assert len(opened) == 1
    url = opened[0]
    assert url.startswith("whatsapp://send?phone=919876543210&text=")
    assert "+" not in url.split("&text=")[0]
    encoded = url.split("&text=", 1)[1]
    assert encoded == urllib.parse.quote(message, safe="")
    assert " " not in encoded and "&" not in encoded
    assert urllib.parse.unquote(encoded) == message
    assert "press Enter to send" in answer
    assert not [e for e in bus.events if e["type"] == "confirm_request"]


def test_ambiguous_app_asks_and_does_not_launch(monkeypatch):
    calls = []
    _forbid_side_effects(monkeypatch, calls)

    async def fake_resolve(pool, kind, phrase, limit=3):
        return "ambiguous", [{"name": "Visual Studio Code"}, {"name": "Visual Studio"},
                             {"name": "VSCodium"}]

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    answer = asyncio.run(run_os_action(_ctx(FakeBus()), POOL,
                                       OsAction(kind="open_app", target="visual")))
    assert answer == "Did you mean Visual Studio Code, Visual Studio or VSCodium? " \
                     "Say the exact name."
    assert calls == []


def test_unparsed_phrase_asks_the_llm_once_before_any_execution(monkeypatch):
    order, decided = [], []
    text = "and file manager."
    assert os_worker.parse_os_command(text).kind == "unknown"

    async def fake_decide(t):
        decided.append(t)
        order.append("decide")
        return OsToolCall(action="open_app", target="file manager")

    async def fake_resolve(pool, kind, phrase, limit=3):
        order.append("resolve")
        return "one", [{"name": "File Explorer", "launch_cmd": "explorer.exe"}]

    async def fake_launch(cmd, *a, **k):
        order.append("launch")

    monkeypatch.setattr(os_worker, "llm_decide_os_action", fake_decide)
    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    monkeypatch.setattr(desktop, "launch", fake_launch)
    state = {"messages": [HumanMessage(content=text)], "scratch": {"task": text}, "run_id": "r1"}
    answer = asyncio.run(os_worker.os_run_worker("os_agent", state, {}, FakeBus(), None, pool=POOL))

    assert decided == [text]                         # asked exactly once
    assert order == ["decide", "resolve", "launch"]  # and before any execution branch
    assert answer.content == "Opened File Explorer."


def test_clean_phrase_skips_the_llm(monkeypatch):
    async def fake_decide(t):
        raise AssertionError("the fast path should not call the LLM")

    async def fake_resolve(pool, kind, phrase, limit=3):
        return "one", [{"name": "Notepad", "launch_cmd": "notepad.exe"}]

    async def fake_launch(cmd, *a, **k):
        pass

    monkeypatch.setattr(os_worker, "llm_decide_os_action", fake_decide)
    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    monkeypatch.setattr(desktop, "launch", fake_launch)
    state = {"messages": [], "scratch": {"task": "open notepad"}, "run_id": "r1"}
    answer = asyncio.run(os_worker.os_run_worker("os_agent", state, {}, FakeBus(), None, pool=POOL))
    assert answer.content == "Opened Notepad."


def test_no_match_names_the_registry_table(monkeypatch):
    calls = []
    _forbid_side_effects(monkeypatch, calls)

    async def fake_resolve(pool, kind, phrase, limit=3):
        return "none", []

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    answer = asyncio.run(run_os_action(_ctx(FakeBus()), POOL,
                                       OsAction(kind="open_app", target="frobnicator")))
    assert "I don't have 'frobnicator' in my registry" in answer
    assert "registry_apps" in answer
    assert calls == []
