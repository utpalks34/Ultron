"""Confirmation gate. Approval arrives only over the WebSocket from a human click, so an
injected instruction cannot approve its own action. Silence is refusal."""
import asyncio
import secrets

from config import settings

# action_id -> (future, event, absolute loop-time deadline)
_pending: dict[str, tuple[asyncio.Future, dict, float]] = {}


async def require(bus, description: str, risk: str, *, run_id: str | None = None,
                  timeout: float | None = None) -> bool:
    timeout = timeout or settings.approval_timeout_s
    aid = secrets.token_hex(6)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    deadline = loop.time() + timeout
    evt = {"type": "confirm_request", "payload": {
        "action_id": aid, "description": description[:4000], "risk": risk, "timeout_s": timeout}}
    if run_id:
        evt["run_id"] = run_id
    _pending[aid] = (fut, evt, deadline)
    await bus.publish(evt)
    try:
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        return False          # silence is refusal, never consent
    finally:
        _pending.pop(aid, None)


async def resolve(action_id: str, approved: bool) -> bool:
    """True only if a pending request was resolved; unknown or repeated ids are ignored."""
    entry = _pending.get(action_id)
    if not entry or entry[0].done():
        return False
    entry[0].set_result(bool(approved))
    return True


def pending_events() -> list[dict]:
    """Copies of the still-pending requests, with timeout_s set to the time actually left."""
    now = asyncio.get_running_loop().time()
    out = []
    for fut, evt, deadline in _pending.values():
        if fut.done():
            continue
        copy = {**evt, "payload": {**evt["payload"], "timeout_s": round(max(0.0, deadline - now), 1)}}
        out.append(copy)
    return out


def deny_all() -> int:
    n = 0
    for fut, _evt, _deadline in list(_pending.values()):
        if not fut.done():
            fut.set_result(False)
            n += 1
    return n
