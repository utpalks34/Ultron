import llm.chat
from test_graph import run


def test_chat_reply_for_non_task(monkeypatch):
    async def fake_stream(text):
        for piece in ["Hello", ", nice to hear from you."]:
            yield piece

    async def none_route(segment):
        return None, "x", 0.0

    monkeypatch.setattr(llm.chat, "chat_reply_stream", fake_stream)
    order, answers, bus = run("hi there", route_fn=none_route)

    assert order == ["supervisor"]
    tokens = [e for e in bus.events if e["type"] == "token"]
    assert len(tokens) >= 1
    assert "".join(e["payload"]["text"] for e in tokens) == "Hello, nice to hear from you."
    assert answers[-1] == "Hello, nice to hear from you."
