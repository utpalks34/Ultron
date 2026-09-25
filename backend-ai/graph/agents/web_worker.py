"""Web worker. Deterministic fast paths (site search, open a site) cover the common requests;
a JSON-step loop handles everything else on a best-effort basis. Page text is data: it is always
wrapped in <page_content> and never followed as instructions. It publishes its OWN tokens and
marks its AIMessage published=True, like the RAG worker."""
import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel

from config import settings
from graph.agents.publish import TokenPublisher
from tools import registry
from tools.browser import (ALLOWED_KEYS, SITES, BrowserBlocked, allowlist_ok, browser_call,
                           click_risk, clip, search_url)
from tools.context import ToolContext

AGENT = "web_agent"
PROMPTS = Path(__file__).resolve().parents[2] / "llm" / "prompts"
CAPTCHA_MSG = ("The site is showing a CAPTCHA or bot check - solve it in the browser window "
               "and ask again")
CIRCLES_MSG = "I'm going in circles - I stopped."


# ---------------------------------------------------------------- pure parsing

@dataclass
class WebTask:
    kind: str                    # "site_search" | "open_site" | "generic"
    site: str = ""
    query: str = ""              # the search query, or the whole request for "generic"
    url: str = ""


_SITE = "|".join(SITES)
_SITE_RE = rf"(?P<site>{_SITE})(?:\.(?:com|in|org))?"
_ON = re.compile(rf"\b(?:search|look\s*up|find)\s+(?:for\s+)?(?P<q>.+?)\s+on\s+{_SITE_RE}\b", re.I)
_SITE_FIRST = re.compile(rf"\b{_SITE_RE}\s+search\s+(?P<q>.+)", re.I)
_SEARCH_SITE_FOR = re.compile(
    rf"\b(?:search|look\s*up|find)\s+{_SITE_RE}\s+for\s+(?P<q>.+)", re.I)
_SEARCH_FOR = re.compile(r"\bsearch\s+(?:google\s+)?for\s+(?P<q>.+)", re.I)
_GOOGLE = re.compile(r"\bgoogle\s+(?P<q>.+)", re.I)
_MUSIC = re.compile(r"\bopen\s+youtube\s+music\b|music\.youtube\.com", re.I)
_BROWSER = re.compile(r"\bopen\s+(?:my\s+|the\s+)?(?:web\s+)?browser\b", re.I)
_OPEN = re.compile(r"^\W*(?:please\s+)?(?:open|go\s+to|visit)\s+(?:the\s+)?(?P<t>\S+?)[\s.!?]*$",
                   re.I)
_DOMAIN = re.compile(r"^(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:[/?#]\S*)?$", re.I)


def _home(site: str) -> str:
    parsed = urlparse(SITES[site])
    return f"{parsed.scheme}://{parsed.netloc}"


def _clean_query(q: str) -> str:
    q = re.sub(r"\s+please\W*$", "", q.strip(), flags=re.I)
    return q.strip().strip("\"'").rstrip(".!?").strip()


def parse_web_task(text: str) -> WebTask:
    text = " ".join(text.split())
    for pattern, fixed_site in ((_ON, None), (_SITE_FIRST, None), (_SEARCH_SITE_FOR, None),
                                (_SEARCH_FOR, "google"), (_GOOGLE, "google")):
        m = pattern.search(text)
        if m:
            query = _clean_query(m.group("q"))
            if query:
                site = fixed_site or m.group("site").lower()
                return WebTask("site_search", site=site, query=query)
    if _MUSIC.search(text):
        return WebTask("open_site", url="https://music.youtube.com")
    if _BROWSER.search(text):
        return WebTask("open_site", site="google", url="https://www.google.com")
    m = _OPEN.match(text)
    if m:
        target = m.group("t")
        if target.lower() in SITES:
            return WebTask("open_site", site=target.lower(), url=_home(target.lower()))
        if _DOMAIN.match(target):
            url = target if re.match(r"https?://", target, re.I) else "https://" + target
            return WebTask("open_site", url=url)
    return WebTask("generic", query=text)


# ---------------------------------------------------------------- generic loop types

class WebStep(BaseModel):
    tool: Literal["goto", "read_page", "click", "type", "press", "finish"]
    target: str = ""
    text: str = ""
    answer: str = ""


@dataclass
class WebDeps:
    browser_call: Callable[..., Awaitable[Any]]
    summarize: Callable[[str], AsyncIterator[str]]
    decide: Callable[[str], Awaitable[WebStep]]


class _Stop(Exception):
    """Ends the task with a plain-language answer."""
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _wrap(text: str) -> str:
    text = text.replace("<page_content>", "").replace("</page_content>", "")
    return f"<page_content>\n{text}\n</page_content>"


@asynccontextmanager
async def _tool(ctx, tool: str, preview: str):
    await ctx.emit(tool, "start", preview)
    try:
        yield
    finally:
        await ctx.emit(tool, "end", preview)


async def _goto(ctx, deps, url: str, *, tool: str = "goto", gate: bool = True) -> str | None:
    """Open a URL; None when the user denied it. An off-allowlist host ALWAYS needs a click."""
    if not allowlist_ok(url):
        host = urlparse(url).hostname or url
        if not await registry.gate(ctx, AGENT, "goto", f"Open {host} (not on the allowlist)",
                                   risk="send"):
            return None
    elif gate:
        if not await registry.gate(ctx, AGENT, "goto", f"Open {url}"):
            return None
    async with _tool(ctx, tool, url):
        return await deps.browser_call("goto", url)


async def _read(ctx, deps) -> dict:
    if not await registry.gate(ctx, AGENT, "read_page", "Read the page"):
        raise _Stop("Okay, I did not read the page.")
    async with _tool(ctx, "read_page", ""):
        try:
            return await deps.browser_call("read_page")
        except BrowserBlocked:
            raise _Stop(CAPTCHA_MSG)


# ---------------------------------------------------------------- flows

async def _site_search(ctx, deps, say, task: WebTask) -> None:
    url = search_url(task.site, task.query)
    if not await registry.gate(ctx, AGENT, "search_site", f"Search {task.site} for {task.query}"):
        raise _Stop("Okay, I did not search.")
    if await _goto(ctx, deps, url, tool="search_site", gate=False) is None:
        raise _Stop("Okay, I did not open that site.")
    page = await _read(ctx, deps)
    prompt = (f"{_wrap(page['text'])}\n\nTask: list the top 3 results with title and price "
              "(and rating if shown). Use only the page content.")
    got = False
    async for chunk in deps.summarize(prompt):
        if chunk:
            got = True
            await say(chunk)
    if not got:
        await say("I could not read any results from the page.")
    await say(f"\n\nSource: {page['url']}")


async def _open_site(ctx, deps, say, task: WebTask) -> None:
    final = await _goto(ctx, deps, task.url)
    if final is None:
        raise _Stop("Okay, I did not open that site.")
    await say(f"Opened {final}.")


def _step_prompt(task: str, history: list[str], obs: str) -> str:
    done = "\n".join(history) or "(none)"
    return (f"Task: {task}\n\nSteps so far:\n{done}\n\nLast observation:\n{obs}\n\n"
            "Choose the next step.")


async def _execute(ctx, deps, step: WebStep, memo: dict) -> str:
    """Run one step and return the observation. Raises _Stop to end the task."""
    target = step.target.strip()
    if step.tool == "goto":
        if not target:
            return "Error: goto needs a URL in target."
        url = target if re.match(r"https?://", target, re.I) else "https://" + target
        final = await _goto(ctx, deps, url)
        if final is None:
            raise _Stop("Okay, I did not open that site.")
        return f"Opened {final}."
    if step.tool == "read_page":
        page = await _read(ctx, deps)
        memo["page"] = page
        # url and title are page-controlled too, so they sit inside the data wrapper
        return _wrap(f"URL: {page['url']}\nTitle: {page['title']}\n\n{page['text']}")
    if step.tool == "click":
        if not target:
            return "Error: click needs the visible name of the element in target."
        if not await registry.gate(ctx, AGENT, "find_and_click", f"Click '{target}'",
                                   risk=click_risk(target)):
            raise _Stop("Okay, I did not click that.")
        async with _tool(ctx, "find_and_click", target):
            try:
                await deps.browser_call("click", target)
            except LookupError as exc:
                return f"Error: {exc}"
        return f"Clicked {target!r}. Read the page to see the result."
    if step.tool == "type":
        if not step.text:
            return "Error: type needs the text to type in text."
        where = target or "the focused field"
        if not await registry.gate(ctx, AGENT, "type_text",
                                   f"Type '{clip(step.text, 80)}' into {where}"):
            raise _Stop("Okay, I did not type that.")
        async with _tool(ctx, "type_text", where):
            try:
                await deps.browser_call("type_text", target or None, step.text)
            except LookupError as exc:
                return f"Error: {exc}"
        return f"Typed into {where}."
    if step.tool == "press":
        if target not in ALLOWED_KEYS:
            return f"Error: key must be one of {', '.join(sorted(ALLOWED_KEYS))}."
        # Enter commonly submits a form, so it is a "send"; every other key keeps its normal risk
        if not await registry.gate(ctx, AGENT, "press_key", f"Press {target}",
                                   risk="send" if target == "Enter" else None):
            raise _Stop("Okay, I did not press that key.")
        async with _tool(ctx, "press_key", target):
            await deps.browser_call("press", target)
        return f"Pressed {target}."
    return f"Error: unknown tool {step.tool!r}."


async def _generic(ctx, deps, say, task: WebTask) -> None:
    limit = settings.browser_step_limit
    history: list[str] = []
    seen: set = set()
    memo: dict = {}
    obs = "(nothing yet)"
    for _ in range(limit):
        step = await deps.decide(_step_prompt(task.query, history, obs))
        if step.tool == "finish":
            await say(step.answer.strip() or "Done.")
            return
        # the same step on the same observation means the model is looping
        key = (step.tool, step.target, step.text, obs)
        if key in seen:
            await say(CIRCLES_MSG)
            return
        seen.add(key)
        history.append(f"{len(history) + 1}. {step.tool} {step.target} {step.text}".rstrip())
        obs = await _execute(ctx, deps, step, memo)
    page = memo.get("page")
    found = (f"{page['title']} ({page['url']}): {clip(page['text'], 400)}" if page
             else "nothing useful yet")
    await say(f"I couldn't finish within {limit} steps; here is what I found: {found}")


async def run_web_task(ctx, pub, task: WebTask, deps: WebDeps) -> str:
    """Runs the task, publishing every token through `pub`; returns exactly what was published."""
    parts: list[str] = []

    async def say(text: str) -> None:
        if text:
            parts.append(text)
            await pub.token(text)

    try:
        if task.kind == "site_search":
            await _site_search(ctx, deps, say, task)
        elif task.kind == "open_site":
            await _open_site(ctx, deps, say, task)
        else:
            await _generic(ctx, deps, say, task)
    except _Stop as stop:
        await say(stop.text)
    return "".join(parts)


# ---------------------------------------------------------------- real dependencies

def _llm(model: str) -> ChatOllama:
    # the same worker options as worker.py, so Ollama does not reload the model
    return ChatOllama(model=model, base_url=settings.ollama_host, temperature=0.1,
                      num_ctx=settings.ctx_worker, num_predict=settings.worker_max_tokens,
                      keep_alive="5m")


def _make_deps(ollama, model: str | None = None) -> WebDeps:
    """With `model` the caller already holds the GPU (generic loop); without it, summarize()
    acquires the worker itself, so the GPU stays free until a page has been read."""
    async def stream(m: str, messages) -> AsyncIterator[str]:
        # no config= here: this node streams its own tokens through the publisher
        async for chunk in _llm(m).astream(messages):
            text = str(chunk.content)
            if text:
                yield text

    async def summarize(prompt_text: str) -> AsyncIterator[str]:
        system = (PROMPTS / "web_agent.md").read_text(encoding="utf-8")
        messages = [SystemMessage(content=system), HumanMessage(content=prompt_text)]
        if model is not None:
            async for text in stream(model, messages):
                yield text
        else:
            async with ollama.worker(settings.worker_model) as m:
                async for text in stream(m, messages):
                    yield text

    async def decide(step_prompt: str) -> WebStep:
        system = (PROMPTS / "web_step.md").read_text(encoding="utf-8")
        messages = [SystemMessage(content=system), HumanMessage(content=step_prompt)]
        llm = _llm(model).with_structured_output(WebStep)
        last: Exception | None = None
        for _ in range(2):
            try:
                out = await llm.ainvoke(messages)
                if isinstance(out, WebStep):
                    return out
                if isinstance(out, dict):
                    return WebStep(**out)
                raise ValueError("no structured output")
            except Exception as exc:
                last = exc
                messages = messages + [HumanMessage(
                    content=f"Your last reply was invalid ({str(exc)[:200]}). "
                            "Reply with valid JSON only.")]
        raise RuntimeError(f"web_agent: could not decide the next step "
                           f"({last.__class__.__name__})")

    return WebDeps(browser_call=browser_call, summarize=summarize, decide=decide)


def _last_human(state) -> str:
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


async def web_run_worker(name, state, config, bus, ollama, *, pool) -> AIMessage:
    run_id = state.get("run_id")
    text = (state.get("scratch") or {}).get("task") or _last_human(state)
    ctx = ToolContext(bus, pool, run_id, AGENT)
    pub = TokenPublisher(bus, run_id)
    task = parse_web_task(text)
    cm = asyncio.timeout(settings.web_timeout_s)
    try:
        async with cm:
            if task.kind == "generic":
                # the decisions need the GPU for the whole loop
                async with ollama.worker(settings.worker_model) as model:
                    deps = _make_deps(ollama, model)
                    await deps.browser_call("set_allowlist", list(settings.web_allowlist))
                    out = await run_web_task(ctx, pub, task, deps)
            else:
                deps = _make_deps(ollama)
                await deps.browser_call("set_allowlist", list(settings.web_allowlist))
                out = await run_web_task(ctx, pub, task, deps)
    except TimeoutError as exc:
        await pub.close()
        if cm.expired():
            raise RuntimeError(f"web_agent: no answer within {settings.web_timeout_s:.0f}s") from None
        raise RuntimeError("web_agent: a browser step timed out") from exc
    except BaseException:
        await pub.close()   # close the chat bubble even when the run fails or is cancelled
        raise
    return AIMessage(content=out, name=name, additional_kwargs={"published": True})
