"""Playwright wrapper: one visible persistent Chromium, run on its own thread and event loop.

The pure helpers (allowlist, search URLs, click risk, captcha detection) need no browser. The
Browser class is launched lazily on first use, never at backend startup. The allowlist and the
confirmation gate are enforced by the CALLER (the web agent), not in here.
"""
import asyncio
import re
import sys
import threading
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from config import settings

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".pwprofile"

SITES = {
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
}

ALLOWED_KEYS = {"Enter", "Escape", "Tab", "ArrowDown", "ArrowUp", "PageDown"}

_DESTRUCTIVE = re.compile(r"\b(buy|order|pay|checkout|purchase|delete|remove|confirm)\b", re.I)
_SEND = re.compile(r"\b(log ?in|sign ?in|sign ?up|register|submit|send|subscribe)\b", re.I)
_CAPTCHA = re.compile(
    r"captcha|verify you are (a )?human|unusual traffic|are you a robot", re.I)
_OVERLAY_BUTTON = re.compile(r"^(close|x|✕|×|no thanks|not now|maybe later)$", re.I)


def allowlist_ok(url: str, allowlist: list[str] | None = None) -> bool:
    """True for http(s) URLs whose host is an allowlisted domain or a subdomain of one."""
    # Chromium and Python's urlparse disagree on backslashes, which lets a URL like
    # https://evil.com\@flipkart.com pass the suffix check below but navigate to evil.com.
    # Reject those, and anything with whitespace/control characters or userinfo ("@" before the path).
    if "\\" in url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if "@" in parsed.netloc:
        return False
    if parsed.scheme not in ("http", "https") or not host:
        return False
    domains = settings.web_allowlist if allowlist is None else allowlist
    for domain in domains:
        d = domain.lower().strip().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def search_url(site: str, query: str) -> str:
    return SITES[site].format(q=quote_plus(query))


def click_risk(name: str) -> str | None:
    if _DESTRUCTIVE.search(name):
        return "destructive"
    if _SEND.search(name):
        return "send"
    return None


def is_captcha(text: str) -> bool:
    return bool(_CAPTCHA.search(text))


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " …[clipped]"


class BrowserThread:
    """Playwright needs asyncio subprocesses, which the backend's Selector loop (Part 5.8)
    cannot spawn. Run it on its own Proactor loop in a dedicated thread."""
    def __init__(self):
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    def _run(self):
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ultron-browser", daemon=True)
            self._thread.start()
            self._ready.wait(5)

    async def run(self, coro_fn, *args, timeout=None):
        self.start()
        fut = asyncio.run_coroutine_threadsafe(coro_fn(*args), self._loop)
        # cancelling the awaiting task cancels the future and the coroutine on the browser loop
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)


class BrowserError(RuntimeError):
    pass


class BrowserBlocked(BrowserError):
    """The page is asking for a captcha or human verification."""


class Browser:
    """Every method runs on the browser thread's loop, through browser_call()."""

    def __init__(self):
        self._pw = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._allowlist: list[str] | None = None   # None = settings.web_allowlist
        self._approved_hosts: set[str] = set()     # off-allowlist hosts the caller opened on purpose

    async def set_allowlist(self, domains: list[str] | None = None) -> None:
        """Set once per task by the caller; also forgets hosts approved during the last task."""
        self._allowlist = None if domains is None else list(domains)
        self._approved_hosts = set()

    def _url_ok(self, url: str) -> bool:
        if url == "about:blank" or allowlist_ok(url, self._allowlist):
            return True
        try:
            return (urlparse(url).hostname or "").lower().rstrip(".") in self._approved_hosts
        except ValueError:
            return False

    async def _guard(self, page, *, settle: bool = False) -> None:
        """Halt if the page ended up off the allowed sites (redirect or clicked link)."""
        if settle:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
        if not self._url_ok(page.url):
            raise BrowserBlocked("navigated off the allowed sites - stopped")

    async def _ensure(self):
        async with self._lock:
            if self._context is not None and self._page is not None and not self._page.is_closed():
                return self._page
            if self._context is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                try:
                    self._context = await self._pw.chromium.launch_persistent_context(
                        user_data_dir=str(PROFILE_DIR),
                        headless=False,
                        viewport={"width": 1440, "height": 900},
                        args=["--disable-blink-features=AutomationControlled",
                              "--disable-gpu", "--disable-software-rasterizer"])
                except Exception as exc:
                    await self._pw.stop()
                    self._pw = None
                    raise BrowserError(
                        f"could not start the browser ({exc.__class__.__name__}: "
                        f"{str(exc)[:160]}); run 'playwright install chromium' once") from exc
            try:
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
            except Exception as exc:
                # the user closed the browser window: forget it so the next call relaunches
                self._context = None
                self._page = None
                if self._pw is not None:
                    try:
                        await self._pw.stop()
                    except Exception:
                        pass
                    self._pw = None
                raise BrowserError("the browser window was closed; try again") from exc
            return self._page

    async def goto(self, url: str) -> str:
        page = await self._ensure()
        # the caller has already gated this URL (allowlisted, or confirmed by the user), so its
        # own host stays acceptable for this task; any other host reached by redirect is not
        if not allowlist_ok(url, self._allowlist):
            try:
                self._approved_hosts.add((urlparse(url).hostname or "").lower().rstrip("."))
            except ValueError:
                pass
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=settings.browser_timeout_s * 1000)
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        await self._guard(page)
        await self.dismiss_overlays()
        return page.url

    async def dismiss_overlays(self) -> None:
        page = await self._ensure()
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        for _ in range(3):
            try:
                button = page.get_by_role("button", name=_OVERLAY_BUTTON).first
                if not await button.is_visible():
                    break
                await button.click(timeout=1500)
            except Exception:
                break

    async def read_page(self, limit: int = 5000) -> dict:
        page = await self._ensure()
        text = await page.locator("body").aria_snapshot()
        if is_captcha(text):
            raise BrowserBlocked("the page is asking for a captcha or human verification")
        return {"url": page.url, "title": await page.title(), "text": clip(text, limit)}

    async def click(self, description: str) -> str:
        from playwright.async_api import Error as PlaywrightError   # TimeoutError subclasses it
        page = await self._ensure()
        clicked = None
        try:
            for role in ("button", "link", "textbox"):
                locator = page.get_by_role(role, name=description)
                if await locator.count():
                    await locator.first.click(timeout=5000)
                    clicked = role
                    break
        except PlaywrightError as exc:
            raise LookupError(str(exc)) from exc
        if clicked is None:
            raise LookupError(f"I could not find anything on the page called {description!r}")
        await self._guard(page, settle=True)
        return clicked

    async def type_text(self, description: str | None, text: str) -> None:
        from playwright.async_api import Error as PlaywrightError
        page = await self._ensure()
        try:
            if description is None:
                try:
                    await page.locator(":focus").fill(text, timeout=5000)
                except Exception:
                    await page.keyboard.type(text)
                return
            locator = page.get_by_role("textbox", name=description)
            if not await locator.count():
                raise LookupError(f"I could not find a text box called {description!r}")
            await locator.first.fill(text, timeout=5000)
        except PlaywrightError as exc:
            raise LookupError(str(exc)) from exc

    async def press(self, key: str) -> None:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"key {key!r} is not allowed")
        page = await self._ensure()
        await page.keyboard.press(key)
        await self._guard(page, settle=True)

    async def screenshot(self) -> bytes:
        page = await self._ensure()
        return await page.screenshot(type="png")

    async def close(self) -> None:
        context, pw = self._context, self._pw
        self._context = self._page = self._pw = None
        if context is not None:
            await context.close()
        if pw is not None:
            await pw.stop()


_thread = BrowserThread()
_browser = Browser()
_METHODS = {"set_allowlist", "goto", "dismiss_overlays", "read_page", "click", "type_text", "press", "screenshot"}


async def browser_call(method: str, *args):
    """Run one Browser method on the browser thread, e.g. `await browser_call("goto", url)`."""
    if method not in _METHODS:
        raise ValueError(f"unknown browser method {method!r}")
    return await _thread.run(getattr(_browser, method), *args,
                             timeout=settings.browser_timeout_s + 10)


async def shutdown() -> None:
    if _thread._thread is None:
        return
    try:
        await _thread.run(_browser.close, timeout=5)
    except Exception:
        pass
