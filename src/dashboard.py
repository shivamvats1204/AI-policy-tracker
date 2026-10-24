"""Data loading and actions shared by the Streamlit dashboard and its tests."""

from __future__ import annotations

import csv
import io
import json
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .collect import Collector
from .extract import web_url
from .paths import PROJECT_ROOT
from .pipeline import CSV_FIELDS, run_pipeline
from .settings import TRACKED_STATES
from .validate import validate_events


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def read_json(path: Path, expected_type: type, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"{path.name} must contain a JSON {expected_type.__name__}.")
    if expected_type is list and any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{path.name} must contain a list of objects.")
    return value


def load_dataset(processed: Path) -> dict:
    data = {name: read_json(processed / f"{name}.json", list, [])
            for name in ("events", "articles", "excluded", "event_sources")}
    data["report"] = read_json(processed / "validation.json", dict, {})
    data["coverage"] = read_json(processed / "coverage.json", dict, {})
    data["exists"] = (processed / "events.json").is_file()
    # Recheck the current export instead of trusting an old validation report.
    data["current_report"] = validate_events(data["events"])
    scope = data["coverage"].get("tracked_states")
    if scope is not None:
        for event in data["events"]:
            if event.get("state") not in scope:
                data["current_report"]["errors"].append({"event_id": event.get("event_id", ""),
                                                          "message": "Event state is outside the saved dataset scope"})
        data["current_report"]["valid"] = not data["current_report"]["errors"]
    data["publishable"] = (data["exists"] and data["current_report"]["valid"]
                           and data["report"].get("valid") is True)
    return data


def read_log(raw: Path, limit: int = 100) -> list[dict]:
    path = raw / "fetch_log.jsonl"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as file:
        lines = deque(enumerate(file, 1), maxlen=limit)
    entries = []
    for number, line in lines:
        if line.strip():
            try:
                value = json.loads(line)
                entries.append(value if isinstance(value, dict) else {"error": f"Non-object log entry at line {number}"})
            except ValueError:
                entries.append({"error": f"Invalid JSON in fetch log at line {number}"})
    return entries


def filter_events(events: list[dict], query: str = "", states=(), categories=(), statuses=()) -> list[dict]:
    query = query.strip().casefold()
    return [event for event in events
            if (not states or event.get("state") in states)
            and (not categories or event.get("category") in categories)
            and (not statuses or event.get("status") in statuses)
            and (not query or query in " ".join(str(event.get(key) or "") for key in
                 ("title", "summary", "state", "source_name")).casefold())]


def csv_export(events: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        row = dict(event)
        row["source_urls"] = json.dumps(row.get("source_urls", []), ensure_ascii=False)
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                row[key] = "'" + value
        writer.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


def collect_and_process(config: Path, limit: int, delay: float,
                        runs_root: Path | None = None,
                        tracked_states=TRACKED_STATES) -> tuple[Path, dict]:
    sources = read_json(config, list, [])
    if not sources:
        raise ValueError("Choose a configuration containing a nonempty source array.")
    for source in sources:
        if (not source.get("name") or source.get("kind") not in {"rss", "listing", "article"}
                or not isinstance(source.get("url"), str) or not web_url(source["url"])):
            raise ValueError("Each source needs a name, an HTTP(S) URL, and kind rss, listing, or article.")
    if limit < 1 or delay < 1:
        raise ValueError("Use a positive article limit and a delay of at least one second.")
    # Every collection gets its own folder so failed fetches cannot erase previous data.
    run = (runs_root or PROJECT_ROOT / "data" / "runs") / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    collector = Collector(run / "raw", delay)
    try:
        records = collector.run(sources, limit)
    finally:
        collector.session.close()
    if not records:
        raise ValueError(f"No articles collected. Review {run / 'raw' / 'fetch_log.jsonl'} for errors.")
    summary = run_pipeline(run / "raw" / "articles.jsonl", run / "processed", tracked_states)
    return run, summary
