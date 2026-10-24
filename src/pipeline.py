"""Process collector JSONL into auditable event JSON/CSV exports.

Run from the repository root:
    Offline: python -m src.pipeline
    Collect first: python -m src.collect --config src/sources.json

Defaults use the repository's data/raw/articles.jsonl and data/processed paths.
Explicit relative paths are resolved from the caller's working directory.

No LLM, API key, or extra dependencies required for this processing stage.
Outputs are candidates, not manually verified events or exhaustive coverage.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

if __package__:
    from .classify import classify_article
    from .deduplicate import deduplicate
    from .normalize import STATES, normalize_article
    from .settings import TRACKED_STATES
    from .paths import ARTICLES_PATH, PROCESSED_DATA_DIR
    from .validate import valid_url, validate_events
else:  # Support python src/pipeline.py as well as python -m src.pipeline.
    from classify import classify_article
    from deduplicate import deduplicate
    from normalize import STATES, normalize_article
    from settings import TRACKED_STATES
    from paths import ARTICLES_PATH, PROCESSED_DATA_DIR
    from validate import valid_url, validate_events


CSV_FIELDS = ["event_id", "state", "title", "date", "source_name", "source_url", "summary",
              "category", "status", "date_basis", "date_precision", "published_at_raw",
              "event_date_raw", "event_date_evidence", "source_count", "source_urls", "review_status"]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pipeline(input_path: Path, output: Path, tracked_states=TRACKED_STATES) -> dict:
    scope = None if tracked_states is None else frozenset(tracked_states)
    if scope is not None and (not scope or scope - set(STATES)):
        raise ValueError("Scope must contain canonical Indian state names")
    accepted, rejected, processed = [], [], []
    seen_ids = set()
    # Parse the entire file before writing outputs; malformed input is never silently skipped.
    with input_path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                article = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_number}: {exc.msg}") from exc
            if not isinstance(article, dict):
                raise ValueError(f"Line {line_number} must contain an object")
            required = ("title", "source_url", "source_name")
            missing = [key for key in required if not isinstance(article.get(key), str) or not article[key].strip()]
            if missing or not valid_url(article.get("source_url")):
                rejected.append({"line": line_number, "article": article,
                                 "exclusion_reasons": ["invalid_source_record"], "missing": missing})
                continue
            article_id = article.get("article_id") or hashlib.sha256(article["source_url"].encode()).hexdigest()[:20]
            if not isinstance(article_id, str):
                raise ValueError(f"Line {line_number}: article_id must be a string")
            if article_id in seen_ids:
                rejected.append({"line": line_number, "article": article,
                                 "exclusion_reasons": ["duplicate_article_id"]})
                continue
            seen_ids.add(article_id)
            article["article_id"] = article_id
            record = classify_article(normalize_article(article))
            if scope is not None and record["state"] and record["state"] not in scope:
                record["included"] = False
                record["exclusion_reasons"].append("out_of_scope_state")
            processed.append(record)
            (accepted if record["included"] else rejected).append(record)
    events, links = deduplicate(accepted)
    report = validate_events(events)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "validation.json", report)
    write_json(output / "articles.json", processed)
    write_json(output / "excluded.json", rejected)
    write_json(output / "event_sources.json", links)
    write_json(output / "coverage.json", {"tracked_states": sorted(scope) if scope is not None else None})
    # Do not publish invalid records as a successful event export.
    if not report["valid"]:
        raise ValueError(f"Validation failed: inspect {output / 'validation.json'}; existing event exports were not updated")
    write_json(output / "events.json", events)
    with (output / "ai_policy_events.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            row = dict(event)
            row["source_urls"] = json.dumps(row["source_urls"], ensure_ascii=False)
            # Spreadsheet formula injection protection; JSON preserves original text.
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    row[key] = "'" + value
            writer.writerow(row)
    return {"events": len(events), "accepted_articles": len(accepted),
            "excluded_articles": len(rejected), "warnings": len(report["warnings"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ARTICLES_PATH)
    parser.add_argument("--output", type=Path, default=PROCESSED_DATA_DIR)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--states", nargs="+", choices=STATES, help="Override the five-state event scope")
    group.add_argument("--all-states", action="store_true", help="Explicitly allow all Indian states")
    args = parser.parse_args()
    try:
        scope = None if args.all_states else (args.states if args.states is not None else TRACKED_STATES)
        summary = run_pipeline(args.input, args.output, scope)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Pipeline failed: {exc}\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
