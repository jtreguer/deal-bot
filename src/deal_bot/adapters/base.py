"""Adapter contract and a polite HTTP client shared by all adapters."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterable
from typing import Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from deal_bot.config import Target
from deal_bot.models import RawListing

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


class Adapter(Protocol):
    name: str
    tier: str  # A: API/feed, B: plain HTML, C: real browser, M: manual

    def search(self, target: Target, http: Http) -> Iterable[RawListing]: ...


class Blocked(Exception):
    """The site refused us (403, captcha, bot wall). The run records it and moves on."""


class Http:
    def __init__(self, min_interval: float = 5.0, respect_robots: bool = True, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
            timeout=30,
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last.get(host, 0) + self.min_interval - now
            self._last[host] = now + max(delay, 0)
        if delay > 0:
            time.sleep(delay)

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                resp = self.client.get(f"{origin}/robots.txt")
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                self._robots[origin] = rp
            except httpx.HTTPError:
                self._robots[origin] = None
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, **params) -> httpx.Response:
        if self.respect_robots and not self._allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        self._wait(urlsplit(url).netloc)
        resp = self.client.get(url, params=params or None)
        if resp.status_code in (401, 403, 429) or "captcha" in resp.text[:5000].lower():
            raise Blocked(f"{resp.status_code} from {urlsplit(url).netloc}")
        resp.raise_for_status()
        return resp


def json_ld(html: str) -> list:
    """All JSON-LD objects in a page, flattened one level."""
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out
