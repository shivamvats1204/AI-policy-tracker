"""Build a single-file static webpage (data embedded) from processed pipeline exports.

Run from the repository root:
    python -m src.build_site --input deliverables/dataset --output docs/index.html

The page needs no server: upload the one HTML file to any static host
(GitHub Pages, Netlify, Vercel, tiiny.host).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import PROJECT_ROOT

PAGE_FIELDS = ("event_id", "state", "title", "date", "date_precision", "source_name", "source_url",
               "summary", "category", "status", "source_count", "source_urls", "review_status")


def funnel(articles: list[dict]) -> list[dict]:
    """Count how many collected articles survive each rule, in order."""
    def sig(article, key):
        return article.get("classification_signals", {}).get(key)
    steps, current = [("Articles collected", articles)], articles
    for label, test in (
        ("Mention AI (headline, description or lead)", lambda a: sig(a, "ai")),
        ("Also name a state explicitly", lambda a: sig(a, "state")),
        ("Also show a government signal", lambda a: sig(a, "government")),
        ("Also contain a policy action verb", lambda a: sig(a, "action")),
        ("Also match a policy category", lambda a: a.get("category")),
        ("Pass final checks: events", lambda a: a.get("included")),
    ):
        current = [a for a in current if test(a)]
        steps.append((label, current))
    return [{"label": label, "count": len(items)} for label, items in steps]


def build(input_dir: Path, output: Path) -> dict:
    events = json.loads((input_dir / "events.json").read_text(encoding="utf-8"))
    articles = json.loads((input_dir / "articles.json").read_text(encoding="utf-8"))
    coverage = json.loads((input_dir / "coverage.json").read_text(encoding="utf-8"))
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%d %B %Y"),
        "scope": coverage.get("tracked_states"),
        "events": [{key: event.get(key, "") for key in PAGE_FIELDS} for event in events],
        "articles": len(articles),
        "funnel": funnel(articles),
    }
    # '<' is escaped so scraped text can never close the script element.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    template = (Path(__file__).parent / "site_template.html").read_text(encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__DATA__", data), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "deliverables" / "dataset")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "docs" / "index.html")
    args = parser.parse_args()
    payload = build(args.input, args.output)
    print(f"Wrote {args.output} with {len(payload['events'])} events")
    for step in payload["funnel"]:
        print(f"{step['count']:6d}  {step['label']}")


if __name__ == "__main__":
    main()
