import pytest

from tools.music import PlayRequest, parse_play


def test_favourites():
    req = parse_play("play my favourite music")
    assert req.mode == "favourites"
    assert req.source == "auto"


def test_query_on_youtube():
    assert parse_play("play believer by imagine dragons on youtube") == PlayRequest(
        mode="query", query="believer by imagine dragons", source="online")


def test_put_on_some_jazz():
    assert parse_play("put on some jazz") == PlayRequest(mode="query", query="jazz", source="auto")


def test_mood_query_keeps_something():
    assert parse_play("play something calm") == PlayRequest(
        mode="query", query="something calm", source="auto")


def test_play_some_music_is_any():
    req = parse_play("play some music")
    assert req.mode == "any"
    assert req.query == ""


def test_from_my_library_is_local():
    assert parse_play("play kesariya from my library") == PlayRequest(
        mode="query", query="kesariya", source="local")


def test_youtube_music_without_a_query():
    req = parse_play("play music on youtube music")
    assert req.source == "online"
    assert req.mode == "any"


@pytest.mark.parametrize("text", ["queue kesariya", "start playing kesariya please",
                                  "Play the song Kesariya!"])
def test_verbs_and_fillers_are_stripped(text):
    assert parse_play(text).query == "kesariya"
