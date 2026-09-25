import numpy as np

from sensors.stt import Endpointer, rms

SPEECH = 0.1
QUIET = 0.0


def make(silence_ms=50, max_s=1.0, min_s=0.01) -> Endpointer:
    # 10 ms blocks: max_s=1.0 -> 100 blocks, silence_ms=50 -> 5 blocks, min_s=0.01 -> 1 block
    return Endpointer(threshold=0.02, silence_ms=silence_ms, block_ms=10, max_s=max_s, min_s=min_s)


def test_silence_only_never_finalizes_and_is_discarded_at_max_length():
    ep = make()
    assert ep.max_blocks == 100
    for _ in range(5):
        assert ep.feed(QUIET) == "continue"
    for _ in range(ep.max_blocks - 6):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "discard"


def test_speech_then_silence_finalizes_exactly_at_silence_blocks():
    ep = make()
    assert ep.silence_blocks == 5
    for _ in range(3):
        assert ep.feed(SPEECH) == "continue"
    for _ in range(ep.silence_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "finalize"


def test_brief_spike_below_confirmation_is_discarded_not_finalized():
    ep = make()
    assert ep.speech_confirm_blocks == 3
    for _ in range(2):
        assert ep.feed(SPEECH) == "continue"       # only 2 blocks: not confirmed as speech
    for _ in range(ep.max_blocks - 3):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "discard"


def test_speech_resets_the_silence_run():
    ep = make()
    for _ in range(ep.speech_confirm_blocks):
        ep.feed(SPEECH)
    for _ in range(ep.silence_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(SPEECH) == "continue"          # the pause ended before the limit
    for _ in range(ep.silence_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "finalize"


def test_min_duration_holds_back_finalize():
    ep = make(min_s=0.2)                           # 20 blocks
    assert ep.min_blocks == 20
    for _ in range(3):
        ep.feed(SPEECH)
    # blocks 4..19: silence_run passes the limit at block 8, but n < min_blocks
    for _ in range(4, ep.min_blocks):
        assert ep.feed(QUIET) == "continue"
    assert ep.n == ep.min_blocks - 1
    assert ep.feed(QUIET) == "finalize"            # block 20


def test_max_length_with_speech_finalizes_not_discards():
    ep = make(max_s=0.3)
    for _ in range(ep.max_blocks - 1):
        assert ep.feed(SPEECH) == "continue"
    assert ep.feed(SPEECH) == "finalize"


def test_reset_after_discard_leaks_no_state():
    ep = make(max_s=0.3)
    for _ in range(ep.max_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "discard"
    ep.reset()
    for _ in range(ep.max_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "discard"


def test_reset_after_finalize_forgets_speech():
    ep = make(max_s=0.3)
    for _ in range(ep.speech_confirm_blocks):
        ep.feed(SPEECH)
    for _ in range(ep.silence_blocks):
        outcome = ep.feed(QUIET)
    assert outcome == "finalize"
    ep.reset()
    assert not ep.heard_speech
    for _ in range(ep.max_blocks - 1):
        assert ep.feed(QUIET) == "continue"
    assert ep.feed(QUIET) == "discard"


def test_rms_of_zeros_is_zero():
    assert rms(np.zeros(10)) == 0.0


def test_rms_of_unit_square_wave_is_one():
    assert rms(np.array([1.0, -1.0, 1.0, -1.0])) == 1.0


def test_rms_of_empty_array_is_zero():
    assert rms(np.array([])) == 0.0
