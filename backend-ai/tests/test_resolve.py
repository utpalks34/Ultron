import pytest

from tools.resolve import decide, normalize, rank

ROWS = [
    {"id": 1, "name": "VS Code", "aliases": ["vscode", "code", "editor"]},
    {"id": 2, "name": "Google Chrome", "aliases": ["chrome", "browser"]},
    {"id": 3, "name": "Notepad", "aliases": ["notepad"]},
]


def _resolve(phrase, rows=ROWS):
    return decide(rank(rows, phrase, "app"))


@pytest.mark.parametrize("phrase", ["vscode", "editor", "code"])
def test_vscode_phrases_resolve_to_one(phrase):
    decision, rows = _resolve(phrase)
    assert decision == "one"
    assert [r["name"] for r in rows] == ["VS Code"]


def test_filler_words_are_ignored():
    decision, rows = _resolve("the chrome app")
    assert decision == "one"
    assert rows[0]["name"] == "Google Chrome"


def test_nonsense_matches_nothing():
    assert _resolve("zzqxv") == ("none", [])


def test_identical_names_are_ambiguous():
    rows = [{"id": 1, "name": "Player", "aliases": []},
            {"id": 2, "name": "Player", "aliases": []}]
    decision, found = _resolve("player", rows)
    assert decision == "ambiguous"
    assert len(found) == 2


def test_normalize():
    assert normalize("my Editor App") == "editor"
