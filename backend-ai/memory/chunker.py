"""Pure text chunker: ~500-token chunks, 15% overlap, never a tokenizer dependency."""
import re

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_SENT = re.compile(r"(?<=[.!?])\s+")


def est_tokens(s: str) -> int:
    return max(1, (len(s) + 3) // 4)      # ~4 characters per token


def _is_heading(u: str) -> bool:
    return bool(_HEADING.match(u))


def _units(text: str, target: int) -> list[str]:
    """Paragraphs; oversize paragraphs split into sentences, then into word runs."""
    out: list[str] = []
    limit = target * 4
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        para = para.strip()
        if not para:
            continue
        if est_tokens(para) <= target:
            out.append(para)
            continue
        for sent in _SENT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if est_tokens(sent) <= target:
                out.append(sent)
                continue
            cur, n = [], 0
            for w in sent.split():
                cur.append(w)
                n += len(w) + 1
                if n >= limit:
                    out.append(" ".join(cur))
                    cur, n = [], 0
            if cur:
                out.append(" ".join(cur))
    return out


def chunk_text(text: str, target: int = 500, overlap: float = 0.15) -> list[str]:
    pairs, heading = [], ""
    for u in _units(text, target):
        if _is_heading(u):
            heading = u
        pairs.append((heading, u))       # heading in effect for this unit
    chunks: list[str] = []
    cur: list[tuple[str, str]] = []
    cur_tok = 0

    def emit() -> None:
        if not cur:
            return
        body = "\n\n".join(u for _, u in cur)
        first_h, first_u = cur[0]
        # a chunk that starts mid-section gets the section heading as context
        chunks.append(f"{first_h}\n\n{body}" if first_h and not _is_heading(first_u) else body)

    for h, u in pairs:
        t = est_tokens(u)
        if cur and cur_tok + t > target:
            emit()
            carry, ct = [], 0
            for item in reversed(cur):
                it = est_tokens(item[1])
                if ct + it > overlap * target:
                    break
                carry.insert(0, item)
                ct += it
            cur, cur_tok = carry, ct
        cur.append((h, u))
        cur_tok += t
    emit()
    return chunks
