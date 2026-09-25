"""Embeddings via the CPU-resident nomic-embed-text. Options MUST equal the manager's load
options (num_ctx=ctx_router, num_gpu=0, keep_alive=-1) or Ollama reloads the model per call.
nomic-embed-text expects task prefixes: 'search_document: ' for stored text and
'search_query: ' for questions."""
import httpx

from config import settings

DIM = 768
DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbedError(RuntimeError):
    pass


async def _embed(texts: list[str], prefix: str) -> list[list[float]]:
    body = {"model": settings.embed_model, "input": [prefix + t for t in texts],
            "keep_alive": -1, "options": {"num_ctx": settings.ctx_router, "num_gpu": 0}}
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_host,
                                     timeout=httpx.Timeout(120.0, connect=5.0)) as c:
            r = await c.post("/api/embed", json=body)
    except httpx.HTTPError as exc:
        raise EmbedError(f"Ollama unreachable at {settings.ollama_host} "
                         f"({exc.__class__.__name__}) - is it running?") from exc
    if r.status_code >= 400:
        hint = " - run scripts\\pull_models.py" if r.status_code == 404 else ""
        raise EmbedError(f"{settings.embed_model}: HTTP {r.status_code}{hint}")
    vecs = r.json().get("embeddings") or []
    if len(vecs) != len(texts) or any(len(v) != DIM for v in vecs):
        raise EmbedError(f"unexpected embedding shape (expected {len(texts)} x {DIM})")
    return vecs


async def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), settings.embed_batch):
        out.extend(await _embed(texts[i:i + settings.embed_batch], DOC_PREFIX))
    return out


async def embed_query(text: str) -> list[float]:
    return (await _embed([text], QUERY_PREFIX))[0]
