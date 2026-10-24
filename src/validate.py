"""Validate event exports. Warnings preserve uncertainty without fabricating data."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from urllib.parse import urlsplit

if __package__:
    from .classify import CATEGORIES, STATUSES
    from .normalize import STATES, normalize_date
else:
    from classify import CATEGORIES, STATUSES
    from normalize import STATES, normalize_date


def valid_url(value) -> bool:
    try:
        parts = urlsplit(value or "")
        return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password
    except (ValueError, TypeError):
        return False


def validate_events(events: list[dict], today=None) -> dict:
    if today is None:
        try:
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        except ZoneInfoNotFoundError:
            from datetime import timezone, timedelta
            today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    errors, warnings, seen = [], [], set()
    for index, event in enumerate(events):
        label = event.get("event_id") or f"row_{index + 1}"
        def error(message):
            errors.append({"event_id": label, "message": message})
        def warn(message):
            warnings.append({"event_id": label, "message": message})
        for field in ("event_id", "state", "title", "source_name", "source_url", "summary", "category"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                error(f"Missing or invalid {field}")
        if label in seen:
            error("Duplicate event_id")
        seen.add(label)
        if event.get("state") not in STATES:
            error("Noncanonical state")
        if event.get("category") not in CATEGORIES:
            error("Unknown category")
        if event.get("status") not in STATUSES:
            error("Unknown status")
        if not valid_url(event.get("source_url")):
            error("Invalid source URL")
        sources = event.get("source_urls", [])
        if (not isinstance(sources, list) or not sources
                or any(not valid_url(url) for url in sources)):
            error("Invalid source_urls")
        elif event.get("source_count") != len(set(sources)) or event.get("source_url") not in sources:
            error("Source count or primary URL inconsistent with provenance")
        value = event.get("date", "")
        if not value:
            warn("Missing date; excluded from chronological analysis")
            if event.get("date_basis") != "unknown" or event.get("date_precision") != "unknown":
                error("Missing date must have unknown basis and precision")
        else:
            parsed = normalize_date(value)
            if parsed["value"] != value or parsed["precision"] != event.get("date_precision"):
                error("Invalid normalized date or precision")
            if event.get("date_basis") not in {"event_date", "publication_date", "unverified_time_element"}:
                error("Invalid date basis")
            if event.get("date_basis") == "event_date" and not event.get("event_date_evidence"):
                error("Event date has no evidence")
            if value > today.isoformat()[:len(value)]:
                warn("Future date requires review")
        if event.get("date_basis") == "unverified_time_element":
            warn("HTML time element has unverified meaning")
        if event.get("review_status") != "verified":
            warn("Rule-generated candidate requires human review")
    if not events:
        warnings.append({"event_id": "", "message": "No events collected; inspect exclusions and fetch logs"})
    return {"valid": not errors, "event_count": len(events), "errors": errors, "warnings": warnings}
