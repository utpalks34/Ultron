"""Real worker factory. ONE model (settings.worker_model) plays four roles: only the system
prompt and the tool list differ. The model is acquired through ollama.worker() (exclusive
GPU tenancy); it is evicted when the run ends, not when the node ends."""
import asyncio
from functools import partial
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama

from config import settings
from graph.agents.style import style_block
from graph.state import UltronState, WORKER_NAMES
from tools.registry import tools_for

PROMPTS = Path(__file__).resolve().parents[2] / "llm" / "prompts"


def _last_human(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def _prior_result(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and getattr(m, "name", None) in WORKER_NAMES:
            return str(m.content)[:1500]
    return ""


async def default_run_worker(name: str, state, config: RunnableConfig, bus, ollama, *,
                             pool=None) -> str:
    task = state.get("scratch", {}).get("task") or _last_human(state)
    system = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    system += await style_block(pool, task)
    prior = _prior_result(state)
    user = task if not prior else (
        f"Result of the previous step:\n<previous_result>\n{prior}\n</previous_result>\n\n"
        f"Task: {task}")
    if tools_for(name):
        raise NotImplementedError("tool-using workers arrive in Phase 5/6")
    try:
        async with asyncio.timeout(settings.worker_timeout_s):
            async with ollama.worker(settings.worker_model) as model:
                llm = ChatOllama(model=model, base_url=settings.ollama_host,
                                 temperature=0.1, num_ctx=settings.ctx_worker,
                                 num_predict=settings.worker_max_tokens, keep_alive="5m")
                parts: list[str] = []
                async for chunk in llm.astream(
                        [SystemMessage(content=system), HumanMessage(content=user)],
                        config=config):
                    parts.append(str(chunk.content))
                return "".join(parts).strip() or "(no output)"
    except TimeoutError:
        raise RuntimeError(f"{name}: no answer within {settings.worker_timeout_s:.0f}s")


def make_worker(name: str, bus, ollama, run_worker=None, pool=None):
    if run_worker:
        impl = run_worker
    elif name == "rag_agent":
        from graph.agents.rag_worker import rag_run_worker
        impl = partial(rag_run_worker, pool=pool)
    elif name == "os_agent":
        from graph.agents.os_worker import os_run_worker
        impl = partial(os_run_worker, pool=pool)
    elif name == "web_agent":
        from graph.agents.web_worker import web_run_worker
        impl = partial(web_run_worker, pool=pool)
    elif name == "dev_agent":
        from graph.agents.dev_worker import dev_run_worker
        impl = partial(dev_run_worker, pool=pool)
    else:
        impl = partial(default_run_worker, pool=pool)

    async def node(state: UltronState, config: RunnableConfig) -> dict:
        out = await impl(name, state, config, bus, ollama)
        msg = out if isinstance(out, AIMessage) else AIMessage(content=out, name=name)
        return {"messages": [msg]}

    node.__name__ = name   # LangGraph uses the name for astream_events
    return node
