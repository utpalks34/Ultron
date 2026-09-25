"""Memory queries. Collection scoping is enforced HERE, at the query level, never by prompt:
- search() reads only the collections it is given and refuses 'dialogue' outright.
- nearest_dialogues() is the ONLY function that reads the dialogue collection (style examples).
- get_verse() is a metadata lookup (jsonb containment), not a similarity guess."""
import hashlib
import uuid

from memory.chunker import est_tokens
from memory.fusion import build_or_query, rrf

SEARCHABLE = ("personal", "scripture")
_COLS = "c.id, c.content, c.ordinal, c.metadata, c.collection, d.source_path, d.title"

_VEC_SQL = f"""
SELECT {_COLS}, 1 - (c.embedding <=> $1::text::vector) AS s
FROM chunks c JOIN documents d ON d.id = c.document_id
WHERE c.collection = ANY($2::text[])
ORDER BY c.embedding <=> $1::text::vector
LIMIT $3"""

_LEX_SQL = f"""
SELECT {_COLS}, ts_rank_cd(to_tsvector('english', c.content), q) AS s
FROM chunks c JOIN documents d ON d.id = c.document_id, to_tsquery('english', $1) q
WHERE c.collection = ANY($2::text[]) AND to_tsvector('english', c.content) @@ q
ORDER BY s DESC
LIMIT $3"""

_VERSE_SQL = f"""
SELECT {_COLS} FROM chunks c JOIN documents d ON d.id = c.document_id
WHERE c.collection = 'scripture' AND c.metadata @> $1::jsonb LIMIT 1"""

_DIALOGUE_SQL = """
SELECT content FROM chunks WHERE collection = 'dialogue'
ORDER BY embedding <=> $1::text::vector LIMIT $2"""


def vec_literal(v) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


async def search(pool, query_vec, query_text: str, collections, *, k_each: int = 20,
                 top: int = 5) -> list[dict]:
    """Hybrid retrieval: vector top-k + full-text top-k, fused with RRF (k=60).
    Each hit: id, content, ordinal, metadata, collection, source_path, title, vec_score
    (None if lexical-only), lex_score, score (the fused RRF score)."""
    cols = list(collections)
    if not cols:
        raise ValueError("search() needs at least one collection")
    bad = [c for c in cols if c not in SEARCHABLE]
    if bad:
        raise ValueError(f"collection(s) {bad} are not searchable (dialogue is style-only)")
    vec_rows = await pool.fetch(_VEC_SQL, vec_literal(query_vec), cols, k_each)
    tsq = build_or_query(query_text)
    lex_rows = await pool.fetch(_LEX_SQL, tsq, cols, k_each) if tsq else []
    by_id: dict[int, dict] = {}
    for r in vec_rows:
        by_id[r["id"]] = {**dict(r), "vec_score": float(r["s"]), "lex_score": 0.0}
    for r in lex_rows:
        d = by_id.setdefault(r["id"], {**dict(r), "vec_score": None, "lex_score": 0.0})
        d["lex_score"] = float(r["s"])
    fused = rrf([[r["id"] for r in vec_rows], [r["id"] for r in lex_rows]])
    out = []
    for cid, score in fused[:top]:
        d = dict(by_id[cid])
        d.pop("s", None)
        d["score"] = score
        out.append(d)
    return out


async def get_verse(pool, chapter: int, verse: int) -> dict | None:
    row = await pool.fetchrow(_VERSE_SQL, {"chapter": chapter, "verse": verse})
    return dict(row) if row else None


async def nearest_dialogues(pool, query_vec, k: int = 3) -> list[str]:
    rows = await pool.fetch(_DIALOGUE_SQL, vec_literal(query_vec), k)
    return [r["content"] for r in rows]


async def document_state(pool, source_path: str) -> tuple[str, str] | None:
    row = await pool.fetchrow("SELECT sha256, collection FROM documents WHERE source_path = $1",
                              source_path)
    return (row["sha256"], row["collection"]) if row else None


async def replace_document(pool, *, source_path: str, title: str, mime: str, sha256: str,
                           collection: str, chunks: list[dict]) -> int:
    """chunks: [{content, token_count, metadata, embedding}]. Atomically replaces any
    document with the same source_path. Returns the new document id."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM documents WHERE source_path = $1", source_path)
            doc_id = await conn.fetchval(
                "INSERT INTO documents (source_path, title, mime, sha256, collection) "
                "VALUES ($1,$2,$3,$4,$5) RETURNING id",
                source_path, title, mime, sha256, collection)
            await conn.executemany(
                "INSERT INTO chunks (document_id, ordinal, content, token_count, embedding, "
                "collection, metadata) VALUES ($1,$2,$3,$4,$5::text::vector,$6,$7::jsonb)",
                [(doc_id, i, c["content"], c["token_count"], vec_literal(c["embedding"]),
                  collection, c.get("metadata") or {}) for i, c in enumerate(chunks)])
    return doc_id


async def add_note(pool, text: str, embedding) -> int:
    """Write-local, automatic, logged: one personal chunk from 'remember that ...'."""
    return await replace_document(
        pool, source_path=f"memory://note/{uuid.uuid4().hex}", title=text[:60],
        mime="text/plain", sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        collection="personal",
        chunks=[{"content": text, "token_count": est_tokens(text),
                 "metadata": {"kind": "saved_note"}, "embedding": embedding}])


async def stats(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT c.collection, count(DISTINCT c.document_id) AS documents, count(*) AS chunks, "
        "COALESCE(avg(c.token_count), 0)::int AS avg_tokens FROM chunks c "
        "GROUP BY c.collection ORDER BY c.collection")
    return [dict(r) for r in rows]
