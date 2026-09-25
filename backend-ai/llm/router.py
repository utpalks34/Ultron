"""CPU router: qwen2.5:1.5b-instruct classifies ONE task segment into a worker.
Runs with num_gpu=0 and keep_alive=-1; num_ctx MUST equal the manager's load option
(settings.ctx_router) or Ollama reloads the model on every call."""
import json
import time
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from config import settings

RouteName = Literal["dev_agent", "web_agent", "os_agent", "rag_agent", "NONE"]
_PROMPT = Path(__file__).parent / "prompts" / "router.md"
_llm = None


class Route(BaseModel):
    next: RouteName = Field(description="worker for the task, or NONE if no worker fits")
    reason: str = Field(description="one short clause")


def _router():
    global _llm
    if _llm is None:
        _llm = ChatOllama(model=settings.router_model, base_url=settings.ollama_host,
                          temperature=0, num_gpu=0, num_ctx=settings.ctx_router,
                          keep_alive=-1, num_predict=120).with_structured_output(Route)
    return _llm


def _build_messages(segment: str) -> list:
    text = _PROMPT.read_text(encoding="utf-8")
    system, _, examples = text.partition("## EXAMPLES")
    msgs = [SystemMessage(content=system.strip())]
    for line in examples.splitlines():
        if " -> " not in line:
            continue
        utt, label = (p.strip() for p in line.rsplit(" -> ", 1))
        msgs.append(HumanMessage(content=utt))
        msgs.append(AIMessage(content=json.dumps({"next": label, "reason": "example"})))
    msgs.append(HumanMessage(content=segment))
    return msgs


async def route_segment(segment: str) -> tuple[str | None, str, float]:
    """Returns (worker or None, reason, seconds). Raises when Ollama is unreachable or
    after one retry with the validation error appended."""
    # Few-shot examples are sent as chat turns because a 1.5B model follows turns far
    # better than a list inside the system prompt; the prefix is identical on every call
    # so Ollama can reuse its cache. Re-measure latency (target 1 s or less).
    msgs = _build_messages(segment)
    t0 = time.perf_counter()
    err = None
    for _ in range(2):
        try:
            r = await _router().ainvoke(msgs)
            if isinstance(r, Route):
                return (None if r.next == "NONE" else r.next), r.reason, time.perf_counter() - t0
            err = "no structured result"
        except ConnectionError:
            raise
        except Exception as exc:
            err = f"{exc.__class__.__name__}: {str(exc)[:120]}"
        msgs = msgs + [HumanMessage(content=f"Your last reply was invalid ({err}). "
                                             "Reply with valid JSON only.")]
    raise RuntimeError(f"router gave no valid route after 2 tries ({err})")
