import numpy as np

from sensors.wakeword import above_threshold, apply_gain


def test_above():
    assert above_threshold(0.6, 0.5) is True


def test_below():
    assert above_threshold(0.4, 0.5) is False


def test_boundary_is_inclusive():
    assert above_threshold(0.5, 0.5) is True


def _tone(amplitude: float, n: int = 1280) -> np.ndarray:
    t = np.arange(n) / 16000
    return (np.sin(2 * np.pi * 300 * t) * amplitude * 32768).astype(np.int16)


def _level(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32))))) / 32768.0


def test_gain_lifts_a_quiet_block():
    quiet = _tone(0.02)
    assert _level(apply_gain(quiet)) > _level(quiet) * 2


def test_gain_leaves_room_noise_alone():
    noise = _tone(0.003)
    assert np.array_equal(apply_gain(noise), noise)


def test_gain_leaves_a_loud_block_alone():
    loud = _tone(0.3)
    assert np.array_equal(apply_gain(loud), loud)


def test_gain_is_capped():
    quiet = _tone(0.008)
    assert _level(apply_gain(quiet)) <= _level(quiet) * 6.5


def test_gain_handles_an_empty_block():
    assert len(apply_gain(np.zeros(0, dtype=np.int16))) == 0
