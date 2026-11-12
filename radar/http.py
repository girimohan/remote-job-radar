from __future__ import annotations

import threading
import time

import requests

USER_AGENT = "remote-job-radar/0.1 (+https://github.com/girimohan/remote-job-radar)"
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
        _local.s.headers["Accept"] = "application/json, application/rss+xml;q=0.9, */*;q=0.5"
    return _local.s


class FetchError(Exception):
    pass


def get(url: str, *, as_json: bool = True, tries: int = 3, timeout=(6, 25)):
    """GET with polite retries. Returns None for 404/410 (board gone); raises FetchError otherwise."""
    last = "no attempt"
    for attempt in range(tries):
        try:
            r = session().get(url, timeout=timeout)
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (404, 410):
            return None
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(4 * (attempt + 1))
            continue
        if not r.ok:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        try:
            return r.json() if as_json else r.text
        except ValueError as e:
            raise FetchError(f"bad JSON from {url}") from e
    raise FetchError(f"{last} for {url}")
