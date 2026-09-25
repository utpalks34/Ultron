"""RAG worker: wires the pure CRAG pipeline (graph/rag.py) to the real embedder, database,
CPU grader and GPU worker. It publishes its OWN tokens (answer, then the Sources footer) so
order is guaranteed, and marks its AIMessage published=True so the orchestrator records it
without sending it again."""
import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import settings
from graph.agents.style import style_block
from graph.rag import Deps, build_context, rag_answer
from llm.cpu import cpu_chat
from memory import embedder, vectorstore
from tools import registry
from tools.context import ToolContext

PROMPTS = Path(__file__).resolve().parents[2] / "llm" / "prompts"


def _last_human(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def build_deps(pool, bus, ollama, run_id, token) -> Deps:
    async def emit(tool: str, preview: str) -> None:
        evt = {"type": "tool_call", "payload": {"agent": "rag_agent", "tool": tool,
                                                "status": "end", "args_preview": preview[:160]}}
        if run_id:
            evt["run_id"] = run_id
        await bus.publish(evt)

    async def search(vec, text, collection):
        return await vectorstore.search(pool, vec, text, [collection],
                                        k_each=settings.rag_candidates,
                                        top=settings.rag_grade_k * 2)

    async def get_verse(chapter, verse):
        return await vectorstore.get_verse(pool, chapter, verse)

    async def grade(question, chunk) -> bool:
        excerpt = str(chunk["content"])[: settings.rag_grade_chars]
        msgs = [SystemMessage(content=(PROMPTS / "rag_grade.md").read_text(encoding="utf-8")),
                HumanMessage(content=f"Question: {question}\n\nExcerpt:\n{excerpt}\n\n"
                                     "Does the excerpt help answer the question?")]
        r = await asyncio.wait_for(cpu_chat(4).ainvoke(msgs), timeout=settings.router_timeout_s)
        return str(r.content).strip().lower().startswith("yes")

    async def rewrite(question) -> str:
        msgs = [SystemMessage(content=(PROMPTS / "rag_rewrite.md").read_text(encoding="utf-8")),
                HumanMessage(content=question)]
        r = await asyncio.wait_for(cpu_chat(48).ainvoke(msgs), timeout=settings.router_timeout_s)
        return str(r.content).strip().strip("\"'").replace("\n", " ")[:200]

    async def generate(question, chunks, collection) -> str:
        system = (PROMPTS / "rag_agent.md").read_text(encoding="utf-8") + await style_block(pool, question)
        user = (f"<sources>\n{build_context(chunks, settings.rag_context_chars)}\n</sources>\n\n"
                f"Question: {question}")
        parts: list[str] = []
        try:
            async with asyncio.timeout(settings.worker_timeout_s):
                async with ollama.worker(settings.worker_model) as model:
                    llm = ChatOllama(model=model, base_url=settings.ollama_host, temperature=0.1,
                                     num_ctx=settings.ctx_worker, num_predict=settings.worker_max_tokens,
                                     keep_alive="5m")
                    # no config= here: this node streams its own tokens through token()
                    async for chunk in llm.astream([SystemMessage(content=system),
                                                    HumanMessage(content=user)]):
                        t = str(chunk.content)
                        if t:
                            parts.append(t)
                            await token(t)
        except TimeoutError:
            raise RuntimeError(f"rag_agent: no answer within {settings.worker_timeout_s:.0f}s")
        # no .strip(): the returned text must equal the published chunks exactly
        text = "".join(parts)
        if not text:
            text = "(no output)"
            await token(text)
        return text

    async def save_note(note: str) -> None:
        # write risk: logged to episodes, not confirmed
        await registry.gate(ToolContext(bus, pool, run_id, "rag_agent"), "rag_agent", "save_note", note)
        vecs = await embedder.embed_documents([note])
        await vectorstore.add_note(pool, note, vecs[0])

    return Deps(embed_query=embedder.embed_query, search=search, get_verse=get_verse,
                grade=grade, rewrite=rewrite, generate=generate, save_note=save_note,
                emit=emit, token=token)


async def rag_run_worker(name, state, config, bus, ollama, *, pool) -> AIMessage:
    if pool is None:
        raise RuntimeError("database unavailable - see the backend startup warning")
    run_id = state.get("run_id")
    task = state.get("scratch", {}).get("task") or _last_human(state)
    started = False

    async def token(text: str) -> None:
        nonlocal started
        if not text:
            return
        started = True
        evt = {"type": "token", "payload": {"text": text, "role": "assistant", "done": False}}
        if run_id:
            evt["run_id"] = run_id
        await bus.publish(evt)

    try:
        result = await rag_answer(task, build_deps(pool, bus, ollama, run_id, token))
    except BaseException:
        if started:   # close the chat bubble even when the run fails or is cancelled
            evt = {"type": "token", "payload": {"text": "", "role": "assistant", "done": True}}
            if run_id:
                evt["run_id"] = run_id
            await bus.publish(evt)
        raise
    return AIMessage(content=result.text, name=name, additional_kwargs={"published": True})
