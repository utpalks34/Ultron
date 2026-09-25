import asyncio

import pytest

from graph.supervisor import classify, decide, keyword_hits, split_segments
from routing_cases import CASES


async def must_not_be_called(segment):
    raise AssertionError("route_fn called for an override")


@pytest.mark.parametrize("utterance,expected", CASES)
def test_classify(utterance, expected):
    assert classify(utterance) == expected


def test_keyword_hits_two_workers():
    assert keyword_hits("open my browser") == ["web_agent", "os_agent"]


def test_keyword_hits_none():
    assert keyword_hits("hello there") == []


def test_decide_play_on_youtube_is_os_override():
    assert asyncio.run(decide("play believer on youtube", must_not_be_called)) == (
        "os_agent", "override")


def test_decide_youtube_music_is_web_override():
    assert asyncio.run(decide("open youtube music", must_not_be_called)) == (
        "web_agent", "override")


def test_decide_please_play_is_os_override():
    route, source = asyncio.run(decide("please play some jazz", must_not_be_called))
    assert (route, source) == ("os_agent", "override")


def test_split_segments():
    text = "search flipkart then check my notes and then open vscode"
    assert split_segments(text) == ["search flipkart", "check my notes", "open vscode"]
