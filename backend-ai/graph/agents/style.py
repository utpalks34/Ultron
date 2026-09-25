"""Style examples: the 2-3 nearest dialogue exchanges, injected into the system prompt.
The dialogue collection is NEVER retrieved as evidence; the block says so. Failure is
non-fatal: no examples is fine."""
import logging

from config import settings
from memory import embedder, vectorstore

log = logging.getLogger("ultron.style")


async def style_block(pool, text: str) -> str:
    if not settings.style_examples or pool is None:
        return ""
    try:
        vec = await embedder.embed_query(text)
        rows = await vectorstore.nearest_dialogues(pool, vec, settings.style_examples)
    except Exception as exc:
        log.warning("style examples unavailable: %s", exc)
        return ""
    if not rows:
        return ""
    body = "\n\n".join(f"<example>\n{r}\n</example>" for r in rows)
    return ("\n\n<style_examples>\nThese show the tone and phrasing Ultron uses. They are NOT "
            "facts: never use their content as information and never quote them as sources.\n"
            f"{body}\n</style_examples>")
