"""Collection-scoping guards and helpers of memory.vectorstore: no database."""
import asyncio

import pytest

from memory.vectorstore import search, vec_literal


def test_search_refuses_dialogue():
    with pytest.raises(ValueError, match="dialogue"):
        asyncio.run(search(None, [0.1], "x", ["dialogue"]))


def test_search_refuses_no_collections():
    with pytest.raises(ValueError):
        asyncio.run(search(None, [0.1], "x", []))


def test_search_refuses_dialogue_mixed_in():
    with pytest.raises(ValueError):
        asyncio.run(search(None, [0.1], "x", ["personal", "dialogue"]))


def test_vec_literal():
    assert vec_literal([0.5, 1]) == "[0.5,1.0]"
