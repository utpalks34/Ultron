import asyncio

import pytest

from tools import approvals, registry
from tools.context import ToolContext


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, e):
        self.events.append(e)


async def _wait_pending():
    for _ in range(200):
        if approvals.pending_events():
            return approvals.pending_events()[0]
        await asyncio.sleep(0)
    raise AssertionError("no confirm_request became pending")


def _ctx(agent, bus=None):
    return ToolContext(bus=bus or FakeBus(), pool=None, run_id="r1", agent=agent)


def test_check_refuses_tools_outside_the_allowlist():
    with pytest.raises(PermissionError):
        registry.check("os_agent", "goto")
    with pytest.raises(PermissionError):
        registry.check("web_agent", "close_all")


def test_check_unknown_agent():
    with pytest.raises(KeyError):
        registry.check("nobody", "x")


def test_check_allows_listed_tool():
    registry.check("os_agent", "open_app")
    registry.check("dev_agent", "open_app")


def test_read_tool_passes_without_events():
    bus = FakeBus()
    ok = asyncio.run(registry.gate(_ctx("web_agent", bus), "web_agent", "read_page", "read"))
    assert ok is True
    assert bus.events == []


def _gate_with_answer(tool, approve, **kw):
    async def main():
        bus = FakeBus()
        task = asyncio.create_task(
            registry.gate(_ctx("os_agent", bus), "os_agent", tool, "do it", **kw))
        evt = await _wait_pending()
        await approvals.resolve(evt["payload"]["action_id"], approve)
        return await task, bus.events
    return asyncio.run(main())


def test_destructive_tool_needs_confirmation_denied():
    ok, events = _gate_with_answer("close_all", False)
    assert ok is False
    assert events[0]["type"] == "confirm_request"
    assert events[0]["payload"]["risk"] == "destructive"


def test_destructive_tool_needs_confirmation_approved():
    ok, _events = _gate_with_answer("close_all", True)
    assert ok is True


def test_risk_can_be_escalated():
    ok, events = _gate_with_answer("open_app", True, risk="send")
    assert ok is True
    assert events[0]["payload"]["risk"] == "send"


def test_unknown_risk_is_rejected():
    with pytest.raises(ValueError):
        asyncio.run(registry.gate(_ctx("os_agent"), "os_agent", "open_app", "x", risk="bogus"))


def test_gate_checks_allowlist_first():
    with pytest.raises(PermissionError):
        asyncio.run(registry.gate(_ctx("web_agent"), "web_agent", "close_all", "x"))
