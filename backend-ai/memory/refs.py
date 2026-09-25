"""Pure routing helpers for the RAG agent: which collection, which verse, is it a note."""
import re

_SCRIPTURE = re.compile(r"\b(gita|geeta|bhagavad|bhagwad|shlok\w*|slok\w*|verse|krishna)\b", re.I)
_MINE = re.compile(r"\b(my notes?|my documents?|my files?|my journal|in my notes?|"
                   r"i (wrote|saved|noted|stored))\b", re.I)
_CH_V = re.compile(r"\bchapter\s*(\d{1,2})\s*[,;]?\s*(?:and\s+)?verse\s*(\d{1,3})\b", re.I)
_V_CH = re.compile(r"\bverse\s*(\d{1,3})\s*(?:of|in|from)\s*chapter\s*(\d{1,2})\b", re.I)
_DOT = re.compile(r"\b(\d{1,2})\s*[.:]\s*(\d{1,3})\b")
_REMEMBER = re.compile(r"^\s*(?:please\s+)?remember\s+(?:that\s+)?(.+?)\s*$", re.I | re.S)
_QWORD = re.compile(r"^(what|when|where|who|whom|whose|how|why|which|do|did|does|is|are|can|"
                    r"could|will|would)\b", re.I)


def pick_collection(text: str) -> str:
    """Exactly one collection per question, never both. 'my notes' wins over scripture words."""
    if _MINE.search(text):
        return "personal"
    if _SCRIPTURE.search(text):
        return "scripture"
    return "personal"


def parse_verse_ref(text: str) -> tuple[int, int] | None:
    m = _CH_V.search(text)
    if m:
        c, v = int(m[1]), int(m[2])
    else:
        m = _V_CH.search(text)
        if m:
            v, c = int(m[1]), int(m[2])
        elif _SCRIPTURE.search(text) or re.search(r"\bbg\b", text, re.I):
            m = _DOT.search(text)
            if not m:
                return None
            c, v = int(m[1]), int(m[2])
        else:
            return None
    return (c, v) if 1 <= c <= 18 and v >= 1 else None


def parse_remember(text: str) -> str | None:
    m = _REMEMBER.match(text)
    if not m:
        return None
    note = m.group(1).strip().rstrip(".")
    if len(note) < 3 or note.lower() == "that" or _QWORD.match(note):
        return None
    return note
