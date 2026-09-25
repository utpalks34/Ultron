"""Casual conversation for utterances the router decided are not a task. Runs on the
CPU-resident router model (fast, no GPU load) so small talk stays snappy in the voice loop."""
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from llm.cpu import cpu_chat

_PROMPT = Path(__file__).parent / "prompts" / "chat.md"


async def chat_reply_stream(text: str):
    """Async generator of text chunks."""
    llm = cpu_chat(200)
    msgs = [SystemMessage(content=_PROMPT.read_text(encoding="utf-8")),
            HumanMessage(content=text)]
    async for chunk in llm.astream(msgs):
        c = str(chunk.content)
        if c:
            yield c
