"""CRAG pipeline. Every side effect goes through Deps, so it is testable offline.
remember -> save note | scripture ref -> exact lookup | otherwise retrieve -> grade ->
(rewrite -> retrieve -> grade, once) -> generate, or admit the gap."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from config import settings
from memory.refs import parse_remember, parse_verse_ref, pick_collection


@dataclass
class Deps:
    embed_query: Callable[[str], Awaitable[list]]
    search: Callable[[list, str, str], Awaitable[list]]        # (vec, query text, collection)
    get_verse: Callable[[int, int], Awaitable[dict | None]]
    grade: Callable[[str, dict], Awaitable[bool]]              # (question, chunk)
    rewrite: Callable[[str], Awaitable[str]]
    generate: Callable[[str, list, str], Awaitable[str]]       # streams via token(); returns body
    save_note: Callable[[str], Awaitable[None]]
    emit: Callable[[str, str], Awaitable[None]]                # tool_call trace (tool, preview)
    token: Callable[[str], Awaitable[None]]                    # publishes answer text


@dataclass
class RagResult:
    text: str
    kind: str                       # answer | verse | gap | saved
    collection: str = "personal"
    sources: list = field(default_factory=list)


def label(row: dict) -> str:
    md = row.get("metadata") or {}
    if "chapter" in md and "verse" in md:
        return f"Gita {md['chapter']}.{md['verse']}"
    sp = str(row.get("source_path", ""))
    if sp.startswith("memory://"):
        return f"saved note: {str(row.get('title', ''))[:30]}"
    return f"{Path(sp).name} #{row.get('ordinal', 0)}"


def build_context(chunks: list, limit: int) -> str:
    return "\n\n".join(
        f'<source id="{i}" name="{label(c)}">\n{str(c["content"])[:limit]}\n</source>'
        for i, c in enumerate(chunks, 1))


def sources_footer(chunks: list) -> str:
    return "\n\nSources: " + "; ".join(f"[{i}] {label(c)}" for i, c in enumerate(chunks, 1))


def gap_text(collection: str, queries: list) -> str:
    where = {"personal": "your notes", "scripture": "your scripture collection"}[collection]
    tried = " and ".join(repr(q[:80]) for q in dict.fromkeys(queries))
    return (f"I couldn't find that in {where} (searched for {tried}). I would rather say so "
            "than guess - I can try the web if you like.")


async def _grade_all(question: str, cands: list, d: Deps) -> list:
    relevant, graded = [], 0
    for c in cands[: settings.rag_grade_k]:
        graded += 1
        if await d.grade(question, c):
            relevant.append(c)
            if len(relevant) >= settings.rag_max_relevant:
                break
    await d.emit("grade", f"{len(relevant)}/{graded} relevant")
    return relevant


async def rag_answer(question: str, d: Deps) -> RagResult:
    note = parse_remember(question)
    if note:
        await d.save_note(note)
        await d.emit("save_note", note)
        text = f"Saved to your notes: {note}"
        await d.token(text)
        return RagResult(text, "saved")

    collection = pick_collection(question)
    if collection == "scripture":
        ref = parse_verse_ref(question)
        if ref:
            await d.emit("lookup_verse", f"{ref[0]}.{ref[1]}")
            row = await d.get_verse(*ref)
            if row is None:
                text = f"Gita {ref[0]}.{ref[1]} is not in your scripture collection."
                await d.token(text)
                return RagResult(text, "gap", "scripture")
            text = f"{row['content']}\n\nSource: {label(row)}"
            await d.token(text)
            return RagResult(text, "verse", "scripture", [label(row)])

    queries = [question]
    relevant: list = []
    vec = await d.embed_query(question)
    for attempt in (1, 2):
        cands = await d.search(vec, queries[-1], collection)
        await d.emit("retrieve", f"{len(cands)} candidates from {collection} for {queries[-1][:80]!r}")
        relevant = await _grade_all(question, cands, d)
        if relevant or attempt == 2:
            break
        rewritten = (await d.rewrite(question)) or question
        queries.append(rewritten)
        await d.emit("rewrite", rewritten)
        vec = await d.embed_query(rewritten)

    if not relevant:
        text = gap_text(collection, queries)
        await d.token(text)
        return RagResult(text, "gap", collection)

    await d.emit("generate", f"{len(relevant)} sources -> {settings.worker_model}")
    body = await d.generate(question, relevant, collection)
    footer = sources_footer(relevant)
    await d.token(footer)
    return RagResult(body + footer, "answer", collection, [label(c) for c in relevant])
