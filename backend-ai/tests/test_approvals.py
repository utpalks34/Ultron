import asyncio

from tools import approvals


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


def test_approve_returns_true():
    async def main():
        bus = FakeBus()
        task = asyncio.create_task(approvals.require(bus, "close x", "destructive", run_id="r1"))
        evt = await _wait_pending()
        assert await approvals.resolve(evt["payload"]["action_id"], True) is True
        return await task, bus.events
    ok, events = asyncio.run(main())
    assert ok is True
    assert events[0]["type"] == "confirm_request"
    assert events[0]["run_id"] == "r1"


def test_deny_returns_false():
    async def main():
        task = asyncio.create_task(approvals.require(FakeBus(), "close x", "destructive"))
        evt = await _wait_pending()
        await approvals.resolve(evt["payload"]["action_id"], False)
        return await task
    assert asyncio.run(main()) is False


def test_timeout_is_refusal_and_clears_pending():
    async def main():
        ok = await approvals.require(FakeBus(), "close x", "destructive", timeout=0.05)
        return ok, approvals.pending_events()
    ok, pending = asyncio.run(main())
    assert ok is False
    assert pending == []


def test_unknown_id_is_ignored():
    assert asyncio.run(approvals.resolve("nope", True)) is False


def test_second_resolve_is_ignored():
    async def main():
        task = asyncio.create_task(approvals.require(FakeBus(), "close x", "destructive"))
        aid = (await _wait_pending())["payload"]["action_id"]
        first = await approvals.resolve(aid, True)
        second = await approvals.resolve(aid, False)
        return first, second, await task
    first, second, ok = asyncio.run(main())
    assert first is True
    assert second is False
    assert ok is True          # the first answer stands


def test_deny_all_denies_pending():
    async def main():
        task = asyncio.create_task(approvals.require(FakeBus(), "close x", "destructive"))
        await _wait_pending()
        n = approvals.deny_all()
        return n, await task
    n, ok = asyncio.run(main())
    assert n == 1
    assert ok is False


def test_pending_events_carry_timeout():
    async def main():
        task = asyncio.create_task(
            approvals.require(FakeBus(), "close x", "destructive", timeout=5))
        evt = await _wait_pending()
        seen = dict(evt)
        await approvals.resolve(evt["payload"]["action_id"], False)
        await task
        return seen
    evt = asyncio.run(main())
    assert evt["type"] == "confirm_request"
    assert evt["payload"]["timeout_s"] == 5
    assert evt["payload"]["risk"] == "destructive"
