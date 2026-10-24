"""Conservative state/date normalization. Unknown values remain unknown."""

from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime


STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
)
ALIASES = {"up": "Uttar Pradesh", "u p": "Uttar Pradesh", "tn": "Tamil Nadu",
           "mp": "Madhya Pradesh", "ap": "Andhra Pradesh", "orissa": "Odisha",
           "uttaranchal": "Uttarakhand", "chattisgarh": "Chhattisgarh"}


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_.]", " ", text.casefold())).strip()


def normalize_state(value: str) -> str:
    lookup = {_key(state): state for state in STATES}
    lookup.update(ALIASES)
    return lookup.get(_key(value or ""), "")


def detect_states(text: str) -> list[str]:
    """Detect explicit names; city names never establish the event's state."""
    found = {state for state in STATES
             if re.search(r"\b" + re.escape(state).replace(r"\ ", r"[\s-]+") + r"\b", text, re.I)}
    for alias, state in ALIASES.items():
        # Short aliases are case-sensitive: ordinary 'up' is not Uttar Pradesh.
        pattern = alias.upper() if len(alias) <= 3 else alias
        flags = 0 if len(alias) <= 3 else re.I
        if re.search(r"\b" + re.escape(pattern) + r"\b", text, flags):
            found.add(state)
    return sorted(found)


def normalize_date(value: str) -> dict:
    """Parse supported dates without guessing ambiguous day/month ordering.

    Timestamp dates retain their source calendar day, without UTC conversion.
    Month/year precision is preserved, never padded to an invented day.
    """
    raw = str(value or "").strip()
    result = {"value": "", "precision": "unknown", "raw": raw, "issue": ""}
    if not raw:
        result["issue"] = "missing_date"
        return result
    try:
        if re.fullmatch(r"\d{4}", raw):
            date(int(raw), 1, 1)
            return {**result, "value": raw, "precision": "year"}
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            date(int(raw[:4]), int(raw[5:]), 1)
            return {**result, "value": raw, "precision": "month"}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed = date.fromisoformat(raw)
        elif re.match(r"^\d{4}-\d{2}-\d{2}T", raw):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        elif re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{4}", raw):
            first, second, year = map(int, re.split(r"[/.-]", raw))
            if first <= 12 and second <= 12 and first != second:
                return {**result, "issue": "ambiguous_numeric_date"}
            day, month = (first, second) if first > 12 or first == second else (second, first)
            parsed = date(year, month, day)
        else:
            parsed = None
            for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    pass
            if parsed is None:
                # RFC 2822 publication timestamps from RSS.
                parsed = parsedate_to_datetime(raw).date()
        return {**result, "value": parsed.isoformat(), "precision": "day"}
    except (ValueError, TypeError, OverflowError):
        return {**result, "issue": "unparseable_date"}


def normalize_article(article: dict) -> dict:
    record = dict(article)
    context = " ".join(str(article.get(k) or "") for k in ("title", "description"))
    states = detect_states(context)
    publication = normalize_date(article.get("published_at_raw", ""))
    event = normalize_date(article.get("event_date_raw", ""))
    # An explicit event date must have an evidence excerpt supplied by an extractor/reviewer.
    use_event = bool(article.get("event_date_evidence") and event["value"])
    chosen = event if use_event else publication
    basis = "event_date" if use_event else ("publication_date" if publication["value"] else "unknown")
    if not use_event and article.get("date_origin") == "time_element_unverified":
        basis = "unverified_time_element" if chosen["value"] else "unknown"
    issues = []
    if len(states) != 1:
        issues.append("multiple_states" if states else "no_explicit_state")
    if chosen["issue"]:
        issues.append(chosen["issue"])
    if article.get("event_date_raw") and not use_event:
        issues.append("unsupported_event_date")
    record.update({"state": states[0] if len(states) == 1 else "",
                   "state_candidate": normalize_state(article.get("state_candidate", "")),
                   "date": chosen["value"], "date_precision": chosen["precision"],
                   "date_basis": basis, "published_date": publication["value"],
                   "event_date": event["value"] if use_event else "",
                   "normalization_issues": issues})
    return record