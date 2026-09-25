from sensors.tts import strip_for_speech

SUFFIX = " ... I've kept that short; ask me to elaborate if you want more."


def test_drops_trailing_sources_footer():
    assert strip_for_speech("Your locker is 42.\n\nSources: [1] a.md", 900) == "Your locker is 42."


def test_drops_singular_source_footer():
    assert strip_for_speech("Done.\n\nSource: notes.md", 900) == "Done."


def test_strips_bold_italic_and_inline_code():
    out = strip_for_speech("This is **bold**, _italic_ and `code` text.", 900)
    assert out == "This is bold, italic and code text."


def test_link_keeps_its_text_only():
    out = strip_for_speech("See [the docs](https://example.com/a) for more.", 900)
    assert out == "See the docs for more."


def test_fenced_code_block_is_replaced():
    text = "Try this:\n```python\nprint('hi')\n```\nThen run it."
    out = strip_for_speech(text, 900)
    assert "(code omitted)" in out
    assert "print" not in out
    assert out == "Try this: (code omitted) Then run it."


def test_long_text_is_cut_at_a_sentence_boundary_past_halfway():
    sentence = "This is a sentence."
    text = " ".join([sentence] * 10)                     # 199 chars, periods at 18, 38, ..., 98
    out = strip_for_speech(text, 100)
    assert out.endswith(SUFFIX)
    body = out[: -len(SUFFIX)]
    assert body.endswith("sentence.")
    assert 50 < len(body) <= 100
    assert body == " ".join([sentence] * 5)
    assert len(out) <= 100 + len(SUFFIX)


def test_long_text_without_a_boundary_is_hard_cut():
    text = "word " * 60
    out = strip_for_speech(text, 100)
    assert out.endswith(SUFFIX)
    assert len(out) == 100 + len(SUFFIX)


def test_boundary_before_the_halfway_point_is_ignored():
    text = "Hi. " + "x" * 200
    out = strip_for_speech(text, 100)
    assert out.endswith(SUFFIX)
    assert len(out) == 100 + len(SUFFIX)


def test_short_text_is_only_whitespace_collapsed():
    assert strip_for_speech("Hello   there,\n\nworld.", 900) == "Hello there, world."


def test_clean_short_text_is_unchanged():
    assert strip_for_speech("Nothing to change here.", 900) == "Nothing to change here."
