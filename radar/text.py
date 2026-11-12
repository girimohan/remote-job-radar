from __future__ import annotations

import html
import re
from functools import lru_cache

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    """HTML (possibly double-escaped, as Greenhouse sends it) to plain text."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    return _WS.sub(" ", _TAG.sub(" ", s)).strip()


@lru_cache(maxsize=4096)
def keyword_re(keyword: str) -> re.Pattern:
    """Match a keyword only as a whole word or phrase.

    Plain substring matching is how "RN" ends up matching "intern" and "iOS"
    matches "biostatistics"; the lookarounds stop that.
    """
    body = re.escape(keyword.lower().strip()).replace(r"\ ", r"[\s\-/]+")
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def has_keyword(text: str, keyword: str) -> bool:
    return bool(keyword_re(keyword).search(text))


def first_keyword(text: str, keywords) -> str | None:
    for k in keywords:
        if has_keyword(text, k):
            return k
    return None
