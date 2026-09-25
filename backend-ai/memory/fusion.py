"""Pure retrieval helpers: reciprocal rank fusion and the lexical query builder."""
import re

_STOP = frozenset("""the and for are but not you all can had her was one our out has have how why
what when where who does did about with from that this they them then than into your say says
said tell show give find look""".split())


def rrf(rank_lists: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal rank fusion. Returns (id, score) best first; ties broken by id."""
    scores: dict[int, float] = {}
    for lst in rank_lists:
        for rank, cid in enumerate(lst, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def build_or_query(text: str, max_terms: int = 12) -> str | None:
    """OR-ed tsquery from the question's distinctive words. Tokens are [a-z0-9_] only, so
    the string is safe for to_tsquery. Returns None when nothing distinctive is left."""
    seen: set[str] = set()
    out: list[str] = []
    for t in re.findall(r"[A-Za-z0-9_]{3,}", text.lower()):
        if t in _STOP or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_terms:
            break
    return " | ".join(out) or None
