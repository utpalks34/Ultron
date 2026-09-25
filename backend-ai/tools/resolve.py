"""Registry-backed name resolution: the model never guesses at app, song or contact names."""
import re

from rapidfuzz import fuzz

KINDS = {
    "app": ("registry_apps", ("name", "aliases")),
    "media": ("registry_media", ("title", "artist", "tags")),
    "contact": ("registry_contacts", ("name", "aliases")),
}
CONFIDENT, MARGIN, FLOOR = 85, 8, 60

_LEADING = re.compile(r"^(?:my|the|a)\s+")
_TRAILING = re.compile(r"\s+(?:app|application|program|please)$")


def normalize(s: str) -> str:
    s = " ".join(s.lower().split())
    s = _LEADING.sub("", s)
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING.sub("", s)
    return s


def _strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [v for v in value if isinstance(v, str)]


def rank(rows, phrase: str, kind: str, limit: int = 3) -> list[dict]:
    """PURE: best fuzzy score per row over its searchable fields, best first."""
    fields = KINDS[kind][1]
    needle = normalize(phrase)
    out = []
    for row in rows:
        d = dict(row)
        best = 0.0
        for f in fields:
            for text in _strings(d.get(f)):
                best = max(best, fuzz.WRatio(needle, text.lower().strip()))
        if best >= FLOOR:
            out.append({**d, "score": best})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]


def decide(cands: list[dict]) -> tuple[str, list[dict]]:
    if not cands:
        return "none", []
    top = cands[0]["score"]
    if top >= CONFIDENT and (len(cands) == 1 or top - cands[1]["score"] > MARGIN):
        return "one", [cands[0]]
    return "ambiguous", [c for c in cands if c["score"] >= top - 10][:3]


async def resolve(pool, kind: str, phrase: str, limit: int = 3) -> tuple[str, list[dict]]:
    table = KINDS[kind][0]     # table names come only from KINDS, never from input
    rows = await pool.fetch(f"SELECT * FROM {table}")
    return decide(rank(rows, phrase, kind, limit))
