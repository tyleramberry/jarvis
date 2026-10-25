"""Lookup tools.

``web_search`` uses the DuckDuckGo Instant Answer API. It is a summary
endpoint, not a full web index. ``fetch_url`` downloads one http(s) page.
Replace either tool if you want a different search provider.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from jarvis.tools.base import Tool, object_schema

_USER_AGENT = "JarvisRoomAssistant/0.1 (personal local assistant)"
_MAX_BYTES = 150_000


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Look up a short factual summary via DuckDuckGo Instant Answers. "
        "The result may be empty for obscure questions. Say so if it is."
    )
    parameters = object_schema(
        {
            "query": {
                "type": "string",
                "description": "The question or search terms.",
            }
        },
        required=["query"],
    )

    def __init__(self, fetcher=None):
        self._fetcher = fetcher or _fetch_bytes

    def run(self, arguments: dict) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "query is required"})
        query = query[:300]
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        )
        try:
            body = self._fetcher(url, 12)
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception as exc:
            return json.dumps({"error": f"web search failed: {exc}", "query": query})
        return json.dumps(parse_duckduckgo(query, payload))


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Download a public http or https page and return its text. "
        "Use this when a search summary is not enough and you have a specific URL."
    )
    parameters = object_schema(
        {
            "url": {
                "type": "string",
                "description": "An http or https URL.",
            }
        },
        required=["url"],
    )

    def __init__(self, fetcher=None):
        self._fetcher = fetcher or _fetch_bytes

    def run(self, arguments: dict) -> str:
        url = str(arguments.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return json.dumps({"error": "url must be an http or https address"})
        try:
            body, content_type = self._fetcher(url, 12, return_type=True)
        except TypeError:
            try:
                body = self._fetcher(url, 12)
                content_type = ""
            except Exception as exc:
                return json.dumps({"error": f"fetch failed: {exc}", "url": url})
        except Exception as exc:
            return json.dumps({"error": f"fetch failed: {exc}", "url": url})
        text = body.decode("utf-8", errors="replace")
        if "html" in content_type or "<html" in text[:500].lower() or "<body" in text[:800].lower():
            text = strip_html(text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 3500:
            text = text[:3500] + "…"
        return json.dumps({"url": url, "text": text or "(empty page)"})


def parse_duckduckgo(query: str, payload: dict) -> dict:
    parts: list[str] = []
    abstract = (payload.get("AbstractText") or "").strip()
    if abstract:
        source = (payload.get("AbstractSource") or "").strip()
        url = (payload.get("AbstractURL") or "").strip()
        credit = f" ({source})" if source else ""
        link = f" {url}" if url else ""
        parts.append(abstract + credit + link)
    answer = (payload.get("Answer") or "").strip()
    if answer:
        parts.append(answer)
    definition = (payload.get("Definition") or "").strip()
    if definition:
        parts.append(definition)
    related = _related_topics(payload.get("RelatedTopics") or [])
    if related:
        parts.append("Related: " + " | ".join(related[:5]))
    if not parts:
        return {
            "query": query,
            "summary": "No instant answer was found for that query.",
            "source": "DuckDuckGo Instant Answer API",
        }
    return {
        "query": query,
        "summary": " ".join(parts),
        "source": "DuckDuckGo Instant Answer API",
    }


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _related_topics(topics: list) -> list[str]:
    found: list[str] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        if "Topics" in topic:
            found.extend(_related_topics(topic.get("Topics") or []))
            continue
        text = (topic.get("Text") or "").strip()
        if text:
            found.append(text)
    return found


def _fetch_bytes(url: str, timeout: float, return_type: bool = False):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_BYTES)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason if hasattr(exc, "reason") else exc)) from exc
    if return_type:
        return body, content_type
    return body
