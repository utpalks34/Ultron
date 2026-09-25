"""parse_os_command is pure: no model, no database, no desktop. Each case is
(utterance, kind, "field=value", ...)."""
import asyncio

import pytest
from langchain_core.messages import HumanMessage

import llm.cpu
from graph.agents.os_worker import OsToolCall, llm_decide_os_action, os_run_worker, parse_os_command
from tools import desktop
from tools import resolve as resolver

CASES = [
    ("open my editor", "open_app", "target=editor"),
    ("open notepad", "open_app", "target=notepad"),
    ("launch calculator", "open_app"),
    ("boot up spotify", "open_app", "target=spotify"),
    ("bring up the task manager", "open_app", "target=task manager"),
    ("close notepad", "close_app", "target=notepad"),
    ("quit chrome", "close_app"),
    ("shut down notepad", "close_app", "target=notepad"),
    ("terminate the zoom app", "close_app", "target=zoom"),
    ("force close chrome", "force_close", "target=chrome"),
    ("kill notepad", "force_close"),
    ("close everything", "close_all"),
    ("close all my windows", "close_all"),
    ("pause the music", "pause"),
    ("resume the music", "resume"),
    ("skip this song", "next"),
    ("next track", "next"),
    ("previous song", "previous"),
    ("play my favourite music", "play"),
    ("play believer on youtube", "play"),
    ("put on some jazz", "play"),
    ("volume up", "volume", "direction=up"),
    ("make it louder", "volume", "direction=up"),
    ("mute the sound", "volume", "direction=mute"),
    ("set volume to 30", "volume", "value=30"),
    ("message Arjun on WhatsApp that I'm running late", "whatsapp",
     "name=Arjun", "message=I'm running late"),
    ("text Priya that dinner is ready", "whatsapp", "name=Priya"),
    ("send a whatsapp to mom saying I will be late", "whatsapp", "name=mom"),
    ("shut down the computer", "unsupported"),
    ("what is the weather", "unknown"),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_parse(case):
    text, kind, *fields = case
    action = parse_os_command(text)
    assert action.kind == kind
    for field in fields:
        key, _, want = field.partition("=")
        assert getattr(action, key) == (int(want) if key == "value" else want)


# ------------------------------------------------------------ LLM decision (mocked cpu_chat)

class _FakeBus:
    async def publish(self, e):
        pass


def _fake_cpu_chat(monkeypatch, call):
    """cpu_chat(n).with_structured_output(schema).ainvoke(msgs) -> the fixed OsToolCall."""
    class Chain:
        async def ainvoke(self, messages):
            return call

    class Chat:
        def with_structured_output(self, schema):
            assert schema is OsToolCall
            return Chain()

    monkeypatch.setattr(llm.cpu, "cpu_chat", lambda max_tokens=200: Chat())


def _run_worker(text):
    state = {"messages": [HumanMessage(content=text)], "scratch": {"task": text}, "run_id": "r1"}
    return asyncio.run(os_run_worker("os_agent", state, {}, _FakeBus(), None, pool=object()))


def test_llm_decide_returns_the_structured_call(monkeypatch):
    call = OsToolCall(action="open_app", target="file manager")
    _fake_cpu_chat(monkeypatch, call)
    assert asyncio.run(llm_decide_os_action("and file manager.")) == call


def test_llm_decide_failure_is_unknown(monkeypatch):
    class Boom:
        def with_structured_output(self, schema):
            raise RuntimeError("ollama is down")

    monkeypatch.setattr(llm.cpu, "cpu_chat", lambda max_tokens=200: Boom())
    assert asyncio.run(llm_decide_os_action("and file manager.")).action == "unknown"


def test_garbled_phrase_reaches_the_same_resolve_call_as_the_regex_path(monkeypatch):
    assert parse_os_command("and file manager.").kind == "unknown"    # the regex fast path misses
    seen, launched = [], []

    async def fake_resolve(pool, kind, phrase, limit=3):
        seen.append((kind, phrase))
        return "one", [{"name": "File Explorer", "launch_cmd": "explorer.exe"}]

    async def fake_launch(cmd, *a, **k):
        launched.append(cmd)

    monkeypatch.setattr(resolver, "resolve", fake_resolve)
    monkeypatch.setattr(desktop, "launch", fake_launch)

    _run_worker("open file manager")                     # regex path
    _fake_cpu_chat(monkeypatch, OsToolCall(action="open_app", target="file manager"))
    answer = _run_worker("and file manager.")            # LLM path

    assert seen == [("app", "file manager"), ("app", "file manager")]
    assert launched == ["explorer.exe", "explorer.exe"]
    assert answer.content == "Opened File Explorer."


def test_llm_unknown_gives_the_cant_do_that_answer(monkeypatch):
    _fake_cpu_chat(monkeypatch, OsToolCall(action="unknown"))
    answer = _run_worker("what is the weather")
    assert answer.content == "I can't do that yet. Nothing in my registry or command list matches."


def test_llm_music_control_maps_to_the_pause_branch(monkeypatch):
    from tools import music
    paused = []
    monkeypatch.setattr(music, "pause", lambda: paused.append(True))
    _fake_cpu_chat(monkeypatch, OsToolCall(action="music_control", control="pause"))
    assert _run_worker("hold the tunes for a second").content == "Paused."
    assert paused == [True]
