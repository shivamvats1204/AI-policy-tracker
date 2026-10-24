"""Conservative event grouping with article-level source provenance.

Only matching normalized headlines with the same state, category, status and
known day/date basis are merged. Paraphrased headlines and uncertain dates stay
separate for human review; a shared URL alone does not establish event identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable

if __package__:
    from .normalize import normalize_date
else:
    from normalize import normalize_date


def _headline(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", text))


def _match_key(article: dict) -> tuple | None:
    fields = ("state", "category", "status", "date", "date_basis", "title")
    if any(not isinstance(article.get(field), str) or not article[field].strip()
           for field in fields):
        return None
    if article["status"] == "unknown":
        return None
    parsed = normalize_date(article["date"])
    if (article.get("date_precision") != "day" or parsed["precision"] != "day"
            or parsed["value"] != article["date"]
            or article["date_basis"] not in {"event_date", "publication_date"}):
        return None
    if article["date_basis"] == "event_date" and not article.get("event_date_evidence"):
        return None
    headline = _headline(article["title"])
    if not headline:
        return None
    # Conflicting amounts or years in descriptions also prevent a merge.
    numbers = tuple(sorted(re.findall(
        r"\d+(?:[.,]\d+)*",
        unicodedata.normalize("NFKC", article["title"] + " " + str(article.get("description") or "")),
    )))
    return (*(article[field] for field in fields[:-1]), headline, numbers)


def same_event(first: dict, second: dict) -> bool:
    """Return whether two classified articles provide a conservative match."""
    key = _match_key(first)
    return key is not None and key == _match_key(second)


def deduplicate(articles: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Build candidate events and one provenance link per contributing article.

    Inputs are not mutated. Stable sorting makes primary-source selection and
    event IDs independent of input order. Source counts count distinct URLs,
    while provenance links retain individual article IDs and raw snapshots.
    """
    records = sorted(
        (article for article in articles if article.get("included") is not False),
        key=lambda article: (str(article.get("source_url") or ""),
                             str(article.get("article_id") or ""),
                             json.dumps(article, sort_keys=True, ensure_ascii=False)),
    )
    groups: dict[tuple, list[dict]] = {}
    for index, article in enumerate(records):
        key = _match_key(article)
        # Unknown dates and other incomplete identities remain separate records.
        identity = ("matched", key) if key is not None else ("unmatched", index)
        groups.setdefault(identity, []).append(article)

    events, links = [], []
    for identity, members in groups.items():
        primary = members[0]
        seed = identity if identity[0] == "matched" else (
            identity, primary.get("article_id"), primary.get("source_url"),
            primary.get("title"), primary.get("date"),
        )
        event_id = hashlib.sha256(
            json.dumps(seed, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        urls = sorted({member["source_url"] for member in members})
        event = dict(primary)
        event.update({
            "event_id": event_id,
            "summary": (str(primary.get("description") or "").strip()
                        or str(primary.get("title") or "").strip()),
            "source_count": len(urls),
            "source_urls": urls,
            "review_status": "needs_review",
        })
        events.append(event)
        for member in members:
            links.append({
                "event_id": event_id,
                "article_id": member.get("article_id", ""),
                "source_name": member.get("source_name", ""),
                "source_url": member["source_url"],
                "published_at_raw": member.get("published_at_raw", ""),
                "raw_snapshot": member.get("raw_snapshot", ""),
                "rss_snapshot": member.get("rss_snapshot", ""),
                "feed_source_url": member.get("feed_source_url", ""),
                "enrichment_status": member.get("enrichment_status", ""),
            })
    return events, links
