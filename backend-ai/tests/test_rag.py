"""Offline tests for the CRAG pipeline (graph/rag.py): fake Deps, no Ollama, no database."""
import asyncio
from types import SimpleNamespace

import pytest

from config import settings
from graph.rag import Deps, rag_answer


def chunk(i: int = 0) -> dict:
    return {"content": f"note text {i}", "metadata": {}, "source_path": "notes/a.md",
            "ordinal": i, "title": "a"}


def build(searches=None, grades=None, verse=None):
    """searches: one result list per search() call (the last one repeats).
    grades: booleans returned in order (False once exhausted)."""
    searches = searches if searches is not None else [[chunk()]]
    grades = list(grades or [])
    rec = SimpleNamespace(search_cols=[], graded=[], rewrites=[], generated=0, saved=[],
                          verse_calls=[], tokens=[], emitted=[], embedded=[])

    async def embed_query(text):
        rec.embedded.append(text)
        return [0.0]

    async def search(vec, text, collection):
        rec.search_cols.append(collection)
        n = len(rec.search_cols)
        return searches[min(n, len(searches)) - 1]

    async def get_verse(chapter, v):
        rec.verse_calls.append((chapter, v))
        return verse

    async def grade(question, c):
        rec.graded.append(question)
        return grades.pop(0) if grades else False

    async def rewrite(question):
        rec.rewrites.append(question)
        return "rewritten query"

    async def generate(question, chunks, collection):
        rec.generated += 1
        await token("ANSWER")
        return "ANSWER"

    async def save_note(note):
        rec.saved.append(note)

    async def emit(tool, preview):
        rec.emitted.append((tool, preview))

    async def token(text):
        rec.tokens.append(text)

    deps = Deps(embed_query=embed_query, search=search, get_verse=get_verse, grade=grade,
                rewrite=rewrite, generate=generate, save_note=save_note, emit=emit, token=token)
    return deps, rec


def run(question, **kw):
    deps, rec = build(**kw)
    result = asyncio.run(rag_answer(question, deps))
    assert "".join(rec.tokens) == result.text
    return result, rec


def test_first_try_hit():
    result, rec = run("what did my notes say about the boiler", grades=[True])
    assert result.kind == "answer"
    assert rec.generated == 1
    assert rec.rewrites == []
    assert "Sources: [1] a.md #0" in result.text


def test_rewrite_path():
    result, rec = run("what did my notes say about the boiler",
                      searches=[[chunk(0)], [chunk(1)]],
                      grades=[False, True])
    assert result.kind == "answer"
    assert len(rec.rewrites) == 1
    assert any(tool == "rewrite" for tool, _ in rec.emitted)
    assert rec.generated == 1


def test_gap_after_rewrite():
    question = "what did my notes say about the boiler"
    result, rec = run(question, searches=[[chunk(0)], [chunk(1)]])
    assert result.kind == "gap"
    assert rec.generated == 0
    assert "couldn't find" in result.text
    assert question in result.text
    assert "rewritten query" in result.text


def test_grade_cap_when_all_no():
    cands = [chunk(i) for i in range(10)]
    _, rec = run("what did my notes say about the boiler", searches=[cands])
    assert len(rec.graded) == 2 * settings.rag_grade_k


def test_grade_stops_at_max_relevant():
    cands = [chunk(i) for i in range(5)]
    result, rec = run("what did my notes say about the boiler", searches=[cands],
                      grades=[True] * 5)
    assert len(rec.graded) == settings.rag_max_relevant
    assert result.kind == "answer"


def test_verse_found():
    row = {"content": "You have a right to action alone.", "metadata": {"chapter": 2, "verse": 47},
           "source_path": "data/scripture/gita.jsonl", "ordinal": 0, "title": "gita"}
    result, rec = run("what does Gita 2.47 say", verse=row)
    assert result.kind == "verse"
    assert rec.verse_calls == [(2, 47)]
    assert rec.search_cols == []
    assert rec.generated == 0
    assert row["content"] in result.text
    assert "Source: Gita 2.47" in result.text


def test_verse_missing():
    result, rec = run("what does chapter 9 verse 22 say", verse=None)
    assert result.kind == "gap"
    assert "not in your scripture collection" in result.text
    assert rec.search_cols == []
    assert rec.generated == 0


def test_scripture_without_reference_searches_scripture():
    _, rec = run("what does the Gita say about fear")
    assert rec.search_cols
    assert set(rec.search_cols) == {"scripture"}


PERSONAL_UTTERANCES = [
    "what is in my notes about the blueprint",
    "did I ever write down the wifi password",
    "find my meeting minutes from Monday",
    "look through what I saved about the visa",
    "summarise my notes on the project plan",
    "what did my notes say about the meeting",
    "what did i do yesterday",
    "remember that my wifi is on the fridge",
    "do I have anything saved about the router password",
    "what did my notes say about verse 12 of chapter 4",
]


@pytest.mark.parametrize("text", PERSONAL_UTTERANCES)
def test_personal_never_searches_scripture(text):
    if text.startswith("remember that"):
        pytest.skip("saves a note instead of searching")
    _, rec = run(text)
    assert rec.search_cols
    assert set(rec.search_cols) == {"personal"}


def test_remember_saves_note():
    result, rec = run("remember that my umbrella is in the hall cupboard")
    assert result.kind == "saved"
    assert rec.saved == ["my umbrella is in the hall cupboard"]
    assert rec.search_cols == []
