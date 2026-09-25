from typing import Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages

AgentName = Literal["dev_agent", "web_agent", "os_agent", "rag_agent", "FINISH"]
WORKER_NAMES = ("dev_agent", "web_agent", "os_agent", "rag_agent")
# These nodes publish their own tokens (or return one whole answer), and their run nodes call
# CPU models (parsers, graders, step deciders) whose output must never reach the chat;
# LangChain callbacks propagate through contextvars, so the token-stream filter must exclude them.
SELF_PUBLISHING = ("rag_agent", "web_agent", "os_agent", "dev_agent")


class UltronState(TypedDict):
    messages: Annotated[list, add_messages]
    next: AgentName
    run_id: str
    hops: int        # hard loop ceiling
    scratch: dict    # cross-agent handoff (task text, urls, file paths, screenshots)
