import asyncio
from urllib.parse import urlparse

import pytest

from tools.browser import (SITES, BrowserThread, allowlist_ok, click_risk, is_captcha,
                           search_url)


def test_allowlist_accepts_listed_hosts_and_subdomains():
    assert allowlist_ok("https://www.flipkart.com/search?q=x")
    assert allowlist_ok("https://en.wikipedia.org/wiki/X")


@pytest.mark.parametrize("url", [
    "https://flipkart.com.evil.io/",
    "https://notflipkart.com/",
    "ftp://flipkart.com/",
    "https://evil.example.com",
])
def test_allowlist_rejects_lookalikes_and_other_schemes(url):
    assert not allowlist_ok(url)


def test_search_url_encodes_the_query():
    assert "flipkart.com/search?q=Samsung+S26" in search_url("flipkart", "Samsung S26")


def test_search_url_unknown_site_raises():
    with pytest.raises(KeyError):
        search_url("nosuchsite", "x")


def test_every_site_host_is_allowlisted_by_default():
    for name, template in SITES.items():
        assert allowlist_ok(template.format(q="x")), name


def test_click_risk():
    assert click_risk("Place order") == "destructive"
    assert click_risk("Buy now") == "destructive"
    assert click_risk("Sign in") == "send"
    assert click_risk("Samsung S26 case") is None


def test_is_captcha():
    assert is_captcha("Please verify you are human") is True
    assert is_captcha("Samsung S26 5G") is False


def test_browser_thread_runs_a_coroutine_on_its_own_loop():
    thread = BrowserThread()

    async def add(a, b):
        return a + b

    assert asyncio.run(thread.run(add, 41, 1, timeout=5)) == 42
