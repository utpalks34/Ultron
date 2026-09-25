"""Router-shaped CPU chat client for the grader and the query rewriter. Options MUST equal
the manager's CPU load options (num_gpu=0, num_ctx=ctx_router, keep_alive=-1) or Ollama
reloads the model on every call. Only num_predict varies (not a load-time option)."""
from langchain_ollama import ChatOllama

from config import settings


def cpu_chat(num_predict: int) -> ChatOllama:
    return ChatOllama(model=settings.router_model, base_url=settings.ollama_host,
                      temperature=0, num_gpu=0, num_ctx=settings.ctx_router,
                      keep_alive=-1, num_predict=num_predict)
