"""Web worker with fakes only: no Ollama, no Playwright, no network."""
import asyncio
import itertools

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from config import settings
from graph.agents import web_worker
from graph.agents.publish import TokenPublisher
from graph.agents.web_worker import WebDeps, WebStep, parse_web_task, run_web_task
from tools import approvals
from tools.browser import BrowserBlocked
from tools.context import ToolContext

FLIPKART_URL = "https://www.flipkart.com/search?q=Samsung+S26"
PAGE = {"url": FLIPKART_URL, "title": "Samsung S26 - Flipkart",
        "text": "- link 'Samsung S26 5G' Rs 79,999 4.5 stars"}


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, e):
        self.events.append(e)

    def tokens(self):
        return [e["payload"]["text"] for e in self.events
                if e["type"] == "token" and not e["payload"]["done"]]


class FakeBrowser:
    def __init__(self, page=PAGE, blocked=False):
        self.calls = []
        self.page = page
        self.blocked = blocked

    async def call(self, method, *args):
        self.calls.append((method, *args))
        if method == "goto":
            return args[0]
        if method == "read_page":
            if self.blocked:
                raise BrowserBlocked("captcha")
            return dict(self.page)
        return None


def _summarize(prompts=None):
    async def summarize(prompt_text):
        if prompts is not None:
            prompts.append(prompt_text)
        yield "1. Samsung S26 5G - Rs 79,999. "
        yield "2. Samsung S26 case - Rs 499."
    return summarize


def _forbidden_summarize():
    async def summarize(prompt_text):
        raise AssertionError("summarize must not run")
        yield ""
    return summarize


def _scripted(steps, prompts=None):
    it = iter(steps)

    async def decide(step_prompt):
        if prompts is not None:
            prompts.append(step_prompt)
        return next(it)
    return decide


def _deps(browser, summarize=None, decide=None):
    return WebDeps(browser_call=browser.call, summarize=summarize or _forbidden_summarize(),
                   decide=decide or _scripted([]))


def _setup(bus):
    return ToolContext(bus, None, "r1", "web_agent"), TokenPublisher(bus, "r1")


def _run(text, deps, bus):
    ctx, pub = _setup(bus)
    return asyncio.run(run_web_task(ctx, pub, parse_web_task(text), deps))


async def _wait_pending():
    for _ in range(500):
        if approvals.pending_events():
            return approvals.pending_events()[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no confirm_request became pending")


def _run_with_answer(text, deps, bus, approve):
    """Runs a task that must stop at the confirmation gate, and answers it."""
    async def main():
        ctx, pub = _setup(bus)
        run = asyncio.create_task(run_web_task(ctx, pub, parse_web_task(text), deps))
        evt = await _wait_pending()
        seen_before = list(deps.browser_call.__self__.calls)
        await approvals.resolve(evt["payload"]["action_id"], approve)
        return evt, seen_before, await run
    return asyncio.run(main())


# ---------------------------------------------------------------- parse_web_task

def test_parse_site_search_on_site():
    t = parse_web_task("search for Samsung S26 on Flipkart")
    assert (t.kind, t.site, t.query) == ("site_search", "flipkart", "Samsung S26")


def test_parse_look_up_on_google():
    t = parse_web_task("look up pixel 9 reviews on google")
    assert (t.kind, t.site, t.query) == ("site_search", "google", "pixel 9 reviews")


def test_parse_search_google_for():
    t = parse_web_task("search google for pizza")
    assert (t.kind, t.site, t.query) == ("site_search", "google", "pizza")


def test_parse_site_first_and_bare_google():
    assert parse_web_task("amazon search usb hub").site == "amazon"
    t = parse_web_task("google best pizza in Delhi")
    assert (t.kind, t.site, t.query) == ("site_search", "google", "best pizza in Delhi")


def test_parse_open_youtube_music():
    t = parse_web_task("open youtube music")
    assert t.kind == "open_site" and "music.youtube.com" in t.url


def test_parse_open_browser():
    t = parse_web_task("open my browser")
    assert t.kind == "open_site" and t.url == "https://www.google.com"


def test_parse_go_to_domain():
    t = parse_web_task("go to flipkart.com")
    assert t.kind == "open_site" and "flipkart.com" in t.url


def test_parse_open_known_site_name():
    t = parse_web_task("open amazon")
    assert t.kind == "open_site" and t.url == "https://www.amazon.in"


def test_parse_unknown_site_is_generic():
    t = parse_web_task("check the price of a PS5 on Croma")
    assert t.kind == "generic" and t.query == "check the price of a PS5 on Croma"


# ---------------------------------------------------------------- site_search

def test_site_search_reads_then_summarises_and_cites_the_source():
    bus, browser, prompts = FakeBus(), FakeBrowser(), []
    out = _run("search for Samsung S26 on Flipkart", _deps(browser, _summarize(prompts)), bus)
    assert browser.calls == [("goto", FLIPKART_URL), ("read_page",)]
    assert "".join(bus.tokens()) == out
    assert out.endswith(f"Source: {FLIPKART_URL}")
    assert "<page_content>" in prompts[0] and "Rs 79,999" in prompts[0]
    assert "Use only the page content" in prompts[0]


def test_site_search_captcha_answers_and_skips_the_model():
    bus, browser = FakeBus(), FakeBrowser(blocked=True)
    out = _run("search for Samsung S26 on Flipkart", _deps(browser), bus)
    assert "CAPTCHA" in out and "solve it in the browser window" in out
    assert "".join(bus.tokens()) == out


def test_site_search_emits_tool_events_for_each_step():
    bus, browser = FakeBus(), FakeBrowser()
    _run("search for Samsung S26 on Flipkart", _deps(browser, _summarize()), bus)
    started = [e["payload"]["tool"] for e in bus.events
               if e["type"] == "tool_call" and e["payload"]["status"] == "start"]
    ended = [e["payload"]["tool"] for e in bus.events
             if e["type"] == "tool_call" and e["payload"]["status"] == "end"]
    assert started == ["search_site", "read_page"] and ended == started


# ---------------------------------------------------------------- open_site

def test_open_site_on_the_allowlist_opens_without_a_confirmation():
    bus, browser = FakeBus(), FakeBrowser()
    out = _run("open youtube music", _deps(browser), bus)
    assert browser.calls == [("goto", "https://music.youtube.com")]
    assert out == "Opened https://music.youtube.com."
    assert not [e for e in bus.events if e["type"] == "confirm_request"]


def test_open_site_off_the_allowlist_needs_confirmation_and_denial_stops_it():
    bus, browser = FakeBus(), FakeBrowser()
    evt, before, out = _run_with_answer("go to evil.example.com", _deps(browser), bus, False)
    assert evt["payload"]["risk"] == "send"
    assert "evil.example.com" in evt["payload"]["description"]
    assert [e for e in bus.events if e["type"] == "confirm_request"]
    assert before == [] and browser.calls == []
    assert "did not open" in out


def test_open_site_off_the_allowlist_opens_when_approved():
    bus, browser = FakeBus(), FakeBrowser()
    _evt, _before, out = _run_with_answer("go to evil.example.com", _deps(browser), bus, True)
    assert browser.calls == [("goto", "https://evil.example.com")]
    assert out == "Opened https://evil.example.com."


# ---------------------------------------------------------------- generic loop

def test_generic_loop_goto_read_finish_stops_after_three_steps():
    bus, browser, prompts = FakeBus(), FakeBrowser(), []
    steps = [WebStep(tool="goto", target="https://www.flipkart.com"),
             WebStep(tool="read_page"),
             WebStep(tool="finish", answer="The S26 costs Rs 79,999.")]
    out = _run("check the price of a PS5 on Croma",
               _deps(browser, decide=_scripted(steps, prompts)), bus)
    assert len(prompts) == 3
    assert browser.calls == [("goto", "https://www.flipkart.com"), ("read_page",)]
    assert out == "The S26 costs Rs 79,999." == "".join(bus.tokens())
    assert "<page_content>" in prompts[2] and "Rs 79,999" in prompts[2]


def test_generic_loop_stops_on_a_repeated_step():
    bus, browser = FakeBus(), FakeBrowser()
    decide = _scripted(itertools.repeat(WebStep(tool="read_page")))
    out = _run("check the price of a PS5 on Croma", _deps(browser, decide=decide), bus)
    assert out == "I'm going in circles - I stopped."
    assert len(browser.calls) < settings.browser_step_limit


def test_generic_loop_stops_at_the_step_limit(monkeypatch):
    monkeypatch.setattr(settings, "browser_step_limit", 3)
    bus, browser = FakeBus(), FakeBrowser()
    steps = [WebStep(tool="read_page"), WebStep(tool="goto", target="https://www.google.com/a"),
             WebStep(tool="goto", target="https://www.google.com/b"),
             WebStep(tool="finish", answer="never reached")]
    out = _run("check the price of a PS5 on Croma",
               _deps(browser, decide=_scripted(steps)), bus)
    assert out.startswith("I couldn't finish within 3 steps; here is what I found:")
    assert "Samsung S26" in out


def test_generic_click_on_place_order_asks_for_a_destructive_confirmation_first():
    bus, browser = FakeBus(), FakeBrowser()
    steps = [WebStep(tool="click", target="Place order"), WebStep(tool="finish", answer="ok")]
    evt, before, out = _run_with_answer(
        "check the price of a PS5 on Croma",
        _deps(browser, decide=_scripted(steps)), bus, False)
    assert evt["payload"]["risk"] == "destructive"
    assert "Place order" in evt["payload"]["description"]
    assert before == [] and browser.calls == []
    assert "did not click" in out


def test_generic_click_on_a_harmless_element_needs_no_confirmation():
    bus, browser = FakeBus(), FakeBrowser()
    steps = [WebStep(tool="click", target="Samsung S26 case"),
             WebStep(tool="finish", answer="done")]
    out = _run("check the price of a PS5 on Croma",
               _deps(browser, decide=_scripted(steps)), bus)
    assert browser.calls == [("click", "Samsung S26 case")] and out == "done"
    assert not [e for e in bus.events if e["type"] == "confirm_request"]


# ---------------------------------------------------------------- web_run_worker

def test_web_run_worker_returns_a_published_message_equal_to_the_streamed_tokens(monkeypatch):
    bus, browser = FakeBus(), FakeBrowser()
    monkeypatch.setattr(web_worker, "_make_deps",
                        lambda ollama, model=None: _deps(browser, _summarize()))
    state = {"run_id": "r1", "scratch": {},
             "messages": [HumanMessage(content="search for Samsung S26 on Flipkart")]}
    msg = asyncio.run(web_worker.web_run_worker("web_agent", state, {}, bus, None, pool=None))
    assert isinstance(msg, AIMessage) and msg.name == "web_agent"
    assert msg.additional_kwargs == {"published": True}
    assert msg.content == "".join(bus.tokens())
    assert msg.content.endswith(f"Source: {FLIPKART_URL}")


def test_web_run_worker_closes_the_bubble_when_the_task_fails(monkeypatch):
    bus = FakeBus()

    async def summarize(prompt_text):
        yield "partial"
        raise RuntimeError("model exploded")

    monkeypatch.setattr(web_worker, "_make_deps",
                        lambda ollama, model=None: _deps(FakeBrowser(), summarize))
    state = {"run_id": "r1", "scratch": {},
             "messages": [HumanMessage(content="search for Samsung S26 on Flipkart")]}
    with pytest.raises(RuntimeError, match="model exploded"):
        asyncio.run(web_worker.web_run_worker("web_agent", state, {}, bus, None, pool=None))
    tokens = [e["payload"] for e in bus.events if e["type"] == "token"]
    assert tokens[0]["text"] == "partial" and tokens[-1] == {
        "text": "", "role": "assistant", "done": True}
