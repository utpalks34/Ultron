import asyncio
import pytest
from langchain_core.messages import AIMessage, HumanMessage

import graph.supervisor as sup
from config import settings
from graph.builder import build_graph
from graph.supervisor import classify

MULTI = "search flipkart then check my notes then open vscode"


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, e):
        self.events.append(e)


async def keyword_route(segment):      # offline stand-in for the LLM router
    return classify(segment), "keyword", 0.0


async def fake_worker(name, state, config, bus, ollama):
    return f"[fake {name}] handled: {state.get('scratch', {}).get('task', '')}"


def run(text, route_fn=keyword_route, run_worker=fake_worker):
    bus = FakeBus()
    g = build_graph(bus, route_fn=route_fn, run_worker=run_worker)
    order, answers = [], []

    async def go():
        async for chunk in g.astream(
                {"messages": [HumanMessage(content=text)], "run_id": "t", "hops": 0, "scratch": {}},
                {"configurable": {"thread_id": "t"}, "recursion_limit": 40},
                stream_mode="updates"):
            for node, update in chunk.items():
                order.append(node)
                if isinstance(update, dict):
                    for m in update.get("messages") or []:
                        answers.append(m.content)

    asyncio.run(go())
    return order, answers, bus


def test_single_hop():
    order, answers, _ = run("open my browser")
    assert order == ["supervisor", "web_agent", "supervisor"]
    assert len(answers) == 1
    assert "web_agent" in answers[0]


def test_multi_hop():
    order, answers, _ = run(MULTI)
    assert order == ["supervisor", "web_agent", "supervisor", "rag_agent", "supervisor",
                     "dev_agent", "supervisor"]
    assert len(answers) == 3


def _fake_chat(monkeypatch, reply="Hello, I'm here."):
    """A non-task utterance gets a conversational reply from the CPU model; keep it offline."""
    import llm.chat

    async def fake_stream(text):
        yield reply

    monkeypatch.setattr(llm.chat, "chat_reply_stream", fake_stream)
    return reply


def test_unroutable(monkeypatch):
    reply = _fake_chat(monkeypatch)
    order, answers, _ = run("hello there")
    assert order == ["supervisor"]
    assert answers[-1] == reply


def test_router_says_none(monkeypatch):
    reply = _fake_chat(monkeypatch)

    async def none_route(segment):
        return None, "x", 0.0

    # no keyword hits, so the router decides alone (with hits, NONE falls back to priority)
    order, answers, _ = run("hello there", route_fn=none_route)
    assert order == ["supervisor"]
    assert answers[-1] == reply


def test_hop_limit(monkeypatch):
    monkeypatch.setattr(sup, "MAX_HOPS", 1)
    order, answers, _ = run(MULTI)
    assert order == ["supervisor", "web_agent", "supervisor"]
    assert "hop limit" in answers[-1]


def test_crash_enabled(monkeypatch):
    monkeypatch.setattr(settings, "debug_endpoints", True)
    with pytest.raises(RuntimeError):
        run("!crash now")


def test_crash_disabled(monkeypatch):
    monkeypatch.setattr(settings, "debug_endpoints", False)
    run("!crash now")   # must not raise


def test_route_event():
    _, _, bus = run("open my browser")
    assert any(
        e["type"] == "tool_call"
        and e["payload"]["tool"] == "route"
        and "web_agent" in e["payload"]["args_preview"]
        for e in bus.events
    )


def test_router_fallback():
    async def broken_route(segment):
        raise RuntimeError("boom")

    order, _, bus = run("open my browser", route_fn=broken_route)
    assert order == ["supervisor", "web_agent", "supervisor"]
    assert any(
        e["type"] == "error"
        and e["payload"]["severity"] == "warn"
        and e["payload"]["source"] == "router"
        for e in bus.events
    )
    assert any(
        e["type"] == "tool_call"
        and e["payload"]["tool"] == "route"
        and "fallback" in e["payload"]["args_preview"]
        for e in bus.events
    )


def test_single_keyword_hit_skips_model():
    async def must_not_be_called(segment):
        raise AssertionError("route_fn called for a single keyword hit")

    order, _, bus = run("pause the music", route_fn=must_not_be_called)
    assert order == ["supervisor", "os_agent", "supervisor"]
    assert not any(e["type"] == "error" for e in bus.events)


def test_play_override_skips_model():
    async def must_not_be_called(segment):
        raise AssertionError("route_fn called for a play override")

    order, _, bus = run("play my favourite music", route_fn=must_not_be_called)
    assert order == ["supervisor", "os_agent", "supervisor"]
    assert not any(e["type"] == "error" for e in bus.events)   # a raise would show as a fallback


def test_two_hits_llm_inside_hits_wins():
    async def os_route(segment):
        return "os_agent", "x", 0.0

    order, _, _ = run("open my browser", route_fn=os_route)
    assert order == ["supervisor", "os_agent", "supervisor"]


def test_two_hits_llm_outside_hits_uses_priority():
    async def dev_route(segment):
        return "dev_agent", "x", 0.0

    order, _, _ = run("search my notes for the wifi password", route_fn=dev_route)
    assert order == ["supervisor", "rag_agent", "supervisor"]


def test_no_hits_llm_decides():
    async def dev_route(segment):
        return "dev_agent", "x", 0.0

    order, _, _ = run("check why my script throws a KeyError", route_fn=dev_route)
    assert order == ["supervisor", "dev_agent", "supervisor"]


def test_prior_result_reaches_next_hop():
    seen = []

    async def recording_worker(name, state, config, bus, ollama):
        seen.append((name, list(state["messages"])))
        return f"[fake {name}] done"

    run(MULTI, run_worker=recording_worker)
    assert len(seen) == 3
    second_msgs = seen[1][1]
    assert any(isinstance(m, AIMessage) and m.content == "[fake web_agent] done"
               for m in second_msgs)
