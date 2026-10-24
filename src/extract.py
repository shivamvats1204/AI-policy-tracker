"""Extract source facts without inferring policy events or normalizing dates.

Dependency: beautifulsoup4. Called by collect.py; also usable independently.
"""

from __future__ import annotations

import json
from html import unescape
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


AI_PATTERN = re.compile(r"\b(?:AI|GenAI|artificial intelligence|machine learning|LLMs?)\b", re.I)


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def web_url(value: str, base: str = "") -> str:
    """Resolve a web URL and remove its fragment, preserving query parameters."""
    try:
        parts = urlsplit(urljoin(base, value.strip()))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        if parts.username or parts.password:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def _article_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _article_nodes(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if any(t in {"Article", "NewsArticle", "ReportageNewsArticle", "BlogPosting"}
               for t in types if isinstance(t, str)):
            yield value
        yield from _article_nodes(value.get("@graph", []))


def extract_article(html: str | bytes, final_url: str) -> dict:
    """Extract publication metadata and available body text; dates stay verbatim."""
    soup = BeautifulSoup(html, "html.parser")

    def meta(selector: str) -> str:
        node = soup.select_one(selector)
        return clean(node.get("content", "")) if node else ""

    structured = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            nodes = list(_article_nodes(json.loads(script.get_text())))
            if nodes:
                structured = nodes[0]
                break
        except (ValueError, TypeError):
            continue

    h1 = soup.find("h1")
    title = (clean(structured.get("headline"))
             or meta('meta[property="og:title"]')
             or (clean(h1.get_text(" ", strip=True)) if h1 else "")
             or (clean(soup.title.get_text()) if soup.title else ""))
    published = (clean(structured.get("datePublished"))
                 or meta('meta[property="article:published_time"]')
                 or meta('meta[itemprop="datePublished"]'))
    date_origin = "publication_metadata" if published else ""
    if not published:
        time_node = soup.select_one("article time[datetime], main time[datetime]")
        if time_node:
            published = clean(time_node.get("datetime"))
            date_origin = "time_element_unverified"

    canonical_node = soup.select_one('link[rel="canonical"]')
    canonical = web_url(canonical_node.get("href", ""), final_url) if canonical_node else ""
    description = (clean(structured.get("description"))
                   or meta('meta[property="og:description"]')
                   or meta('meta[name="description"]'))
    container = (soup.select_one('[itemprop="articleBody"], #pcl-full-content, .story_details, .article-content')
                 or soup.find("article") or soup.find("main"))
    body = ""
    if container:
        for node in container.select("script, style, nav, footer, aside, .paywall, [hidden], [aria-hidden='true'], .related-articles"):
            node.decompose()
        body = "\n".join(clean(p.get_text(" ", strip=True))
                         for p in container.find_all("p") if clean(p.get_text()))
    if not body and isinstance(structured.get("articleBody"), str):
        body = structured["articleBody"].strip()
    return {
        "title": title,
        "published_at_raw": published,
        "date_origin": date_origin,
        "modified_at_raw": clean(structured.get("dateModified")),
        "source_url": final_url,
        "canonical_url": canonical or final_url,
        "description": description,
        "body_text": body,
    }


def extract_feed(xml: bytes, feed_url: str) -> list[dict]:
    """Read RSS 2.0 or Atom. Feed links may be aggregator redirect URLs."""
    # Do not allow DTD/entity declarations in untrusted feeds.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
        raise ValueError("Feed contains a DTD or entity declaration")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid RSS/Atom XML: {exc}") from exc

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    records = []
    for item in root.iter():
        if local(item.tag) not in {"item", "entry"}:
            continue
        fields = {}
        link = ""
        source_name = ""
        for child in item:
            name = local(child.tag)
            value = clean(" ".join(child.itertext()))
            fields.setdefault(name, value)
            if name == "source":
                source_name = value
            if name == "link":
                if child.get("href") and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                elif not child.get("href") and value:
                    link = value
        url = web_url(link, feed_url)
        if not url:
            continue
        description = fields.get("description") or fields.get("summary") or ""
        description = clean(BeautifulSoup(description, "html.parser").get_text(" "))
        published = fields.get("pubDate") or fields.get("published") or fields.get("date") or ""
        records.append({
            "title": fields.get("title", ""),
            "source_url": url,
            "canonical_url": url,
            "source_name": source_name,
            "published_at_raw": published,
            "date_origin": "feed_publication" if published else "",
            "modified_at_raw": fields.get("updated", ""),
            "description": description,
            "body_text": "",
        })
    return records


def discover_links(html: str | bytes, page_url: str, selector: str = "main a[href], article a[href]",
                   same_host: bool = True) -> list[dict]:
    """Discover AI-related candidates, without asserting policy relevance."""
    soup = BeautifulSoup(html, "html.parser")
    records, seen = [], set()
    for link in soup.select(selector):
        title = clean(link.get_text(" ", strip=True))
        url = web_url(link.get("href", ""), page_url)
        if not url or url in seen or not AI_PATTERN.search(title):
            continue
        if same_host and urlsplit(url).hostname != urlsplit(page_url).hostname:
            continue
        seen.add(url)
        records.append({"title": title, "source_url": url})
    return records
