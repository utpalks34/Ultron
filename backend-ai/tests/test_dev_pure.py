"""Pure helpers of the dev agent: no window, no OCR engine, no Ollama."""
import base64
import io

from PIL import Image

from graph.agents.dev_worker import extract_error_text
from tools.vision import tail_text, terminal_crop, to_png_b64

TRACEBACK = ("Traceback (most recent call last):\n"
             '  File "app.py", line 3, in <module>\n'
             "ZeroDivisionError: division by zero")


def test_tail_text_keeps_the_last_lines_and_cuts_at_a_line_boundary():
    text = "\n".join(f"line{i:02d}" for i in range(20))       # 7 characters per line + newline
    out = tail_text(text, 30)
    assert len(out) <= 30
    assert out.endswith("line19")
    assert all(ln.startswith("line") and len(ln) == 6 for ln in out.splitlines())
    assert out.splitlines()[0] != "line00"


def test_tail_text_short_text_is_unchanged():
    assert tail_text("a\nb", 100) == "a\nb"


def test_terminal_crop_is_the_bottom_35_percent():
    assert terminal_crop(Image.new("RGB", (100, 200), "white")).size == (100, 70)


def test_extract_error_text_multiline_traceback():
    assert extract_error_text(TRACEBACK) == TRACEBACK


def test_extract_error_text_plain_question_is_none():
    assert extract_error_text("why is my terminal erroring") is None


def test_extract_error_text_single_line_error_marker():
    assert extract_error_text("TypeError: x is not a function") == "TypeError: x is not a function"


def test_to_png_b64_decodes_to_a_png_no_wider_than_1280():
    b64 = to_png_b64(Image.new("RGB", (3000, 400), "white"))
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "PNG"
    assert img.width <= 1280
    assert img.height == round(400 * 1280 / 3000)
