"""Pure memory helpers: no database, no Ollama."""
import re

import pytest

from memory.chunker import chunk_text, est_tokens
from memory.fusion import build_or_query, rrf
from memory.refs import parse_remember, parse_verse_ref, pick_collection

PARAS = [f"P{i:03d} " + "lorem ipsum dolor sit amet " * 6 for i in range(200)]


# ---------------------------------------------------------------- chunker

def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("  \n\n ") == []


def test_chunk_short():
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_heading_stays_with_text():
    assert chunk_text("# Title\n\nSome text here.") == ["# Title\n\nSome text here."]


def test_chunk_long_text():
    chunks = chunk_text("\n\n".join(PARAS))
    assert len(chunks) > 1
    assert all(est_tokens(c) <= 625 for c in chunks)
    assert chunks[0].split("\n\n")[-1] in chunks[1]           # overlap
    for p in PARAS:
        assert any(p.strip() in c for c in chunks)


def test_chunk_mid_section_gets_heading():
    chunks = chunk_text("## Section\n\n" + "\n\n".join(PARAS))
    assert chunks[1].startswith("## Section")


def test_chunk_huge_paragraph():
    text = " ".join(["word"] * 10000)
    chunks = chunk_text(text)
    assert all(est_tokens(c) <= 625 for c in chunks)
    assert sum(len(c.split()) for c in chunks) >= 10000


# ---------------------------------------------------------------- refs

COLLECTION_CASES = [
    ("what is in my notes about the blueprint", "personal"),
    ("did I ever write down the wifi password", "personal"),
    ("find my meeting minutes from Monday", "personal"),
    ("look through what I saved about the visa", "personal"),
    ("summarise my notes on the project plan", "personal"),
    ("what did my notes say about the meeting", "personal"),
    ("what did i do yesterday", "personal"),
    ("remember that my wifi is on the fridge", "personal"),
    ("do I have anything saved about the router password", "personal"),
    ("what did my notes say about verse 12 of chapter 4", "personal"),
    ("what does Gita 2.47 say", "scripture"),
    ("what does chapter 2 verse 47 say", "scripture"),
    ("read me verse 12 of chapter 4", "scripture"),
    ("what does Krishna tell Arjun about duty", "scripture"),
    ("explain the Bhagavad Gita on karma", "scripture"),
    ("quote a shloka about detachment", "scripture"),
    ("what is chapter 2 verse 48", "scripture"),
    ("gita verse on action without attachment", "scripture"),
    ("what does the Gita say about fear", "scripture"),
    ("tell me a verse about duty", "scripture"),
]


@pytest.mark.parametrize("text,expected", COLLECTION_CASES)
def test_pick_collection(text, expected):
    assert pick_collection(text) == expected


VERSE_CASES = [
    ("what does Gita 2.47 say", (2, 47)),
    ("what does chapter 2 verse 47 say", (2, 47)),
    ("read me verse 12 of chapter 4", (4, 12)),
    ("gita 2:47", (2, 47)),
    ("BG 2.47", (2, 47)),
    ("what does Krishna tell Arjun about duty", None),
    ("chapter 19 verse 1", None),
    ("my notes 2.47", None),
    ("what did i pay 2.5 for", None),
    ("tell me a verse about duty", None),
]


@pytest.mark.parametrize("text,expected", VERSE_CASES)
def test_parse_verse_ref(text, expected):
    assert parse_verse_ref(text) == expected


REMEMBER_CASES = [
    ("remember that my wifi is on the fridge", "my wifi is on the fridge"),
    ("please remember Arjun's birthday is 14 March", "Arjun's birthday is 14 March"),
    ("remember what I said about the visa", None),
    ("do you remember the visa", None),
]


@pytest.mark.parametrize("text,expected", REMEMBER_CASES)
def test_parse_remember(text, expected):
    assert parse_remember(text) == expected


# ---------------------------------------------------------------- fusion

def test_rrf_shared_id_first():
    assert rrf([[1, 2, 3], [3, 4]])[0][0] == 3


def test_rrf_ties_ordered_by_id():
    assert [i for i, _ in rrf([[5], [9]])] == [5, 9]


def test_build_or_query_keeps_distinctive_words():
    q = build_or_query("did I ever write down the wifi password")
    terms = q.split(" | ")
    assert "wifi" in terms and "password" in terms
    assert "did" not in terms


def test_build_or_query_all_stopwords():
    assert build_or_query("the and for") is None


def test_build_or_query_charset():
    q = build_or_query("what's in my notes about O'Brien's café; DROP TABLE chunks --")
    assert q is not None
    assert re.fullmatch(r"[a-z0-9_ |]+", q)
