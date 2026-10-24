"""Synthetic collection-to-export checks; no live network requests."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.collect import Collector
from src.paths import PROJECT_ROOT
from src.pipeline import run_pipeline


BASE = "https://example.org"
TITLE = "Karnataka government approves Bengaluru AI mission"


def fixture_sources():
    return [{"name": "Synthetic publisher", "state": "Karnataka", "kind": kind,
             "url": BASE + path}
            for kind, path in (("rss", "/feed"), ("rss", "/atom"),
                               ("listing", "/listing"), ("article", "/direct"))]


def fixture_responses():
    def response(body, content_type):
        return 200, {"Content-Type": content_type}, body.encode("utf-8")

    def html(title):
        return response(f'''<html><head>
            <meta property="article:published_time" content="2026-10-01">
            <meta name="description" content="Synthetic AI mission coverage.">
            </head><body><article><h1>{title}</h1>
            <p>Synthetic government policy coverage for testing only.</p>
            </article></body></html>''', "text/html")

    return {
        BASE + "/robots.txt": response("User-agent: *\nAllow: /\n", "text/plain"),
        BASE + "/feed": response(f'''<rss version="2.0"><channel>
            <item><title>{TITLE}</title><link>{BASE}/rss-article</link>
            <pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate>
            <description>Synthetic AI mission coverage.</description></item>
            <item><title>Private gadget review</title><link>{BASE}/gadget</link>
            <pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate></item>
            </channel></rss>''', "application/rss+xml"),
        BASE + "/atom": response(f'''<feed xmlns="http://www.w3.org/2005/Atom">
            <entry><title>{TITLE}</title><link href="/atom-article"/>
            <published>2026-10-01T12:00:00+05:30</published>
            <summary>Synthetic AI mission coverage.</summary></entry>
            </feed>''', "application/atom+xml"),
        BASE + "/listing": response('''<main>
            <a href="/launched">Karnataka government launches Bengaluru AI mission</a>
            <a href="/unrelated">Weather update</a></main>''', "text/html"),
        BASE + "/launched": html("Karnataka government launches Bengaluru AI mission"),
        BASE + "/direct": html(TITLE),
    }


class CollectionTests(unittest.TestCase):
    def test_all_source_kinds_through_pipeline_and_exports(self):
        responses = fixture_responses()
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            collector = Collector(root / "raw")
            try:
                with patch.object(collector, "_request", side_effect=lambda url: responses[url]):
                    records = collector.run(fixture_sources())
            finally:
                collector.session.close()
            self.assertEqual(len(records), 5)
            summary = run_pipeline(root / "raw" / "articles.jsonl", root / "processed")
            self.assertEqual(summary, {"events": 2, "accepted_articles": 4,
                                       "excluded_articles": 1, "warnings": 2})
            events = json.loads((root / "processed" / "events.json").read_text(encoding="utf-8"))
            approved = next(event for event in events if event["status"] == "approved")
            self.assertEqual(approved["source_count"], 3)
            self.assertEqual({event["status"] for event in events}, {"approved", "launched"})
            links = json.loads((root / "processed" / "event_sources.json").read_text(encoding="utf-8"))
            self.assertEqual(len(links), 4)
            for link in links:
                snapshot = root / "raw" / link["raw_snapshot"]
                self.assertEqual(snapshot.stem, hashlib.sha256(snapshot.read_bytes()).hexdigest())
            with (root / "processed" / "ai_policy_events.csv").open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual({row["event_id"] for row in rows}, {event["event_id"] for event in events})
            report = json.loads((root / "processed" / "validation.json").read_text(encoding="utf-8"))
            self.assertTrue(report["valid"])

    def test_robots_denial_is_logged_without_fetching_source(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            collector = Collector(Path(directory))
            try:
                with patch.object(collector, "_request", return_value=(
                    200, {}, b"User-agent: *\nDisallow: /\n"
                )) as request:
                    self.assertEqual(collector.run(fixture_sources()[:1]), [])
                    request.assert_called_once_with(BASE + "/robots.txt")
            finally:
                collector.session.close()
            self.assertIn("Robots disallow", (Path(directory) / "fetch_log.jsonl").read_text())

    def test_invalid_feed_is_logged_and_not_silently_parsed(self):
        responses = fixture_responses()
        responses[BASE + "/feed"] = (200, {}, b"<rss>broken")
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            collector = Collector(Path(directory))
            try:
                with patch.object(collector, "_request", side_effect=lambda url: responses[url]):
                    self.assertEqual(collector.run(fixture_sources()[:1]), [])
            finally:
                collector.session.close()
            self.assertIn("Invalid RSS/Atom XML", (Path(directory) / "fetch_log.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
