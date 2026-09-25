"""Capability allowlist and risk table: isolation as data, not discipline (Architecture P4).
Every side-effecting tool calls gate() first; nothing else decides what needs a human."""
import asyncio

from tools import approvals

RISKS = ("read", "navigate", "write", "send", "destructive")
CONFIRM = ("send", "destructive")          # need a human click
LOGGED = ("write", "send", "destructive")  # audited to episodes

RISK = {
    "open_app": "navigate", "close_app": "write", "force_close_app": "destructive",
    "close_all": "destructive", "play_music": "navigate", "music_control": "navigate",
    "set_volume": "navigate", "whatsapp_compose": "write",
    "goto": "navigate", "read_page": "read", "find_and_click": "navigate",
    "type_text": "write", "press_key": "write", "browser_screenshot": "read",
    "search_site": "navigate",
    "capture_terminal": "read", "read_clipboard": "read",
    "save_note": "write",
}
ALLOWED = {
    "os_agent": ("open_app", "close_app", "force_close_app", "close_all", "play_music",
                 "music_control", "set_volume", "whatsapp_compose"),
    "web_agent": ("goto", "read_page", "find_and_click", "type_text", "press_key",
                  "browser_screenshot", "search_site"),
    "dev_agent": ("capture_terminal", "read_clipboard", "open_app"),
    "rag_agent": ("save_note",),
}


def tools_for(agent: str) -> list:
    if agent not in ALLOWED:
        raise KeyError(f"unknown agent {agent!r}")
    return []


def check(agent: str, tool: str) -> None:
    if agent not in ALLOWED:
        raise KeyError(f"unknown agent {agent!r}")
    if tool not in ALLOWED[agent]:
        raise PermissionError(f"{agent} may not use {tool}")


async def gate(ctx, agent: str, tool: str, description: str, *, risk: str | None = None) -> bool:
    """True when the action may proceed. `risk` lets a caller ESCALATE a specific call
    (e.g. a click on "Place order")."""
    check(agent, tool)
    if risk is not None and risk not in RISKS:
        raise ValueError(f"unknown risk {risk!r}")
    # escalation can only raise the risk, never lower it below the tool's own
    r = max(RISK[tool], risk or RISK[tool], key=RISKS.index)
    if r in CONFIRM:
        try:
            ok = await approvals.require(ctx.bus, description, r, run_id=ctx.run_id)
        except asyncio.CancelledError:
            await asyncio.shield(ctx.audit(tool, "CANCELLED " + description, "cancelled"))
            raise
        await ctx.audit(tool, ("APPROVED " if ok else "DENIED ") + description,
                        "ok" if ok else "cancelled")
        return ok
    if r in LOGGED:
        await ctx.audit(tool, description)
    return True
