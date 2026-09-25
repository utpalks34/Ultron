import asyncio

import pytest

from tools import connectivity


class FakeWriter:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.fixture(autouse=True)
def _reset_cache():
    connectivity._cache = None
    yield
    connectivity._cache = None


def _patch(monkeypatch, fake):
    monkeypatch.setattr(connectivity.asyncio, "open_connection", fake)


def test_failure_means_offline(monkeypatch):
    async def fake(host, port):
        raise OSError("unreachable")
    _patch(monkeypatch, fake)
    assert asyncio.run(connectivity.online(force=True)) is False


def test_success_means_online_and_closes_writer(monkeypatch):
    writers = []

    async def fake(host, port):
        w = FakeWriter()
        writers.append(w)
        return object(), w
    _patch(monkeypatch, fake)
    assert asyncio.run(connectivity.online(force=True)) is True
    assert writers[0].closed is True


def test_result_is_cached_and_force_bypasses(monkeypatch):
    calls = []

    async def fake(host, port):
        calls.append(1)
        return object(), FakeWriter()
    _patch(monkeypatch, fake)

    async def main():
        await connectivity.online()
        await connectivity.online()
        assert len(calls) == 1
        await connectivity.online(force=True)
        assert len(calls) == 2
    asyncio.run(main())
