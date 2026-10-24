"""Bounded article context shared by state detection and classification."""

import re


def context_parts(article: dict) -> list[tuple[str, str]]:
    parts = [(field, str(article.get(field) or "").strip())
             for field in ("title", "description")]
    seen = {text.casefold() for _, text in parts if text}
    # Use the lead, not the full page: later related stories often discuss other events.
    paragraphs = str(article.get("body_text") or "")[:6000].splitlines()
    count = 0
    for paragraph in paragraphs:
        text = re.sub(r"\s+", " ", paragraph).strip()
        if (not text or text.casefold() in seen
                or re.match(r"^(?:also read|related|read more|advertisement|subscribe)\b", text, re.I)):
            continue
        parts.append(("article_lead", text[:1500]))
        seen.add(text.casefold())
        count += 1
        if count == 3:
            break
    return [(field, text) for field, text in parts if text]
