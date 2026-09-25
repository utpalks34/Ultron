"""Per-run handle passed to tools: where to publish, where to audit, who is calling."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from memory import episodes

log = logging.getLogger("ultron.tools")


@dataclass
class ToolContext:
    bus: Any
    pool: Any
    run_id: str | None
    agent: str

    async def emit(self, tool: str, status: str, preview: str = "") -> None:
        evt = {"type": "tool_call", "payload": {
            "agent": self.agent, "tool": tool, "status": status,
            "args_preview": preview[:160]}}
        if self.run_id:
            evt["run_id"] = self.run_id
        await self.bus.publish(evt)

    async def audit(self, tool: str, summary: str, outcome: str = "ok") -> None:
        if self.pool is None:
            return
        try:
            await asyncio.wait_for(
                episodes.log_episode(self.pool, self.run_id or "-", self.agent,
                                     f"tool {tool}: {summary}"[:900], outcome), 5)
        except Exception as exc:   # auditing must never break the action; no summary in logs
            log.warning("audit of %s failed: %s", tool, type(exc).__name__)
