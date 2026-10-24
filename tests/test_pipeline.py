"""Synthetic fixtures only: these are not authentic news records."""

import json
import tempfile
import unittest
from pathlib import Path

from classify import classify_article
from deduplicate import deduplicate, same_event
from normalize import detect_states, normalize_article, normalize_date, normalize_state
from pipeline import run_pipeline
from validate import validate_events


def fixture(**updates):
    value = {"article_id": "a1", "state_candidate": "Karnataka",
             "title": "Karnataka government approves Bengaluru AI mission",
             "description": "The Karnataka government approved an artificial intelligence mission.",
             "source_name": "Synthetic publisher", "source_url": "https://example.org/a1",
             "published_at_raw": "2026-10-01", "date_origin": "feed_publication"}
    value.update(updates)
    return value


def process(**updates):
    return classify_article(normalize_article(fixture(**updates)))


class NormalizationTests(unittest.TestCase):
    def test_state_aliases(self):
        for value in ("UP", "uttar pradesh", "Uttar-Pradesh"):
            self.assertEqual(normalize_state(value), "Uttar Pradesh")

    def test_no_city_or_lowercase_up_inference(self):
        self.assertEqual(detect_states("Bengaluru is setting up an AI centre"), [])
        self.assertEqual(detect_states("UP government signs AI agreement"), ["Uttar Pradesh"])

    def test_multiple_states_are_not_assigned_one_state(self):
        record = normalize_article(fixture(title="Karnataka and Telangana approve AI missions"))
        self.assertEqual(record["state"], "")
        self.assertIn("multiple_states", record["normalization_issues"])

    def test_dates(self):
        for raw in ("2026-10-01", "1 October 2026", "Thu, 01 Oct 2026 10:00:00 GMT"):
            self.assertEqual(normalize_date(raw)["value"], "2026-10-01")
        self.assertEqual(normalize_date("2026-10-01T00:30:00+05:30")["value"], "2026-10-01")

    def test_no_ambiguous_or_invalid_dates(self):
        self.assertEqual(normalize_date("03/04/2026")["issue"], "ambiguous_numeric_date")
        self.assertEqual(normalize_date("2026-02-30")["value"], "")
        self.assertEqual(normalize_date("")["precision"], "unknown")
        self.assertEqual(normalize_date("2026-10")["value"], "2026-10")

    def test_event_date_needs_evidence(self):
        record = normalize_article(fixture(event_date_raw="2026-09-29"))
        self.assertEqual(record["date_basis"], "publication_date")
        record = normalize_article(fixture(event_date_raw="2026-09-29", event_date_evidence="Approved on 29 September"))
        self.assertEqual(record["date"], "2026-09-29")
        self.assertEqual(record["date_basis"], "event_date")


class ClassificationTests(unittest.TestCase):
    def test_relevant_and_irrelevant(self):
        self.assertTrue(process()["included"])
        self.assertFalse(process(title="Karnataka AI startup raises funds", description="Private funding round")["included"])
        self.assertFalse(process(title="Government approves healthcare programme", description="Karnataka programme")["included"])

    def test_category_and_status(self):
        record = process(title="Karnataka government cancels Bengaluru AI MoU")
        self.assertEqual(record["category"], "AI MoU/Partnership")
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["review_status"], "needs_review")


class DeduplicationTests(unittest.TestCase):
    def test_duplicate_coverage_retains_sources(self):
        a = process()
        b = process(article_id="a2", source_url="https://example.net/coverage")
        events, links = deduplicate([a, b])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_count"], 2)
        self.assertEqual(len(links), 2)
        self.assertTrue(validate_events(events)["valid"])

    def test_separate_milestones(self):
        a = process()
        b = process(title="Karnataka government launches Bengaluru AI mission")
        self.assertFalse(same_event(a, b))

    def test_no_merge_across_dates_or_unknown_dates(self):
        self.assertFalse(same_event(process(), process(source_url="https://example.net/b", published_at_raw="2026-10-10")))
        self.assertFalse(same_event(process(), process(source_url="https://example.net/b", published_at_raw="")))

    def test_no_merge_different_amounts(self):
        a = process(title="Karnataka government approves 100 crore Bengaluru AI mission")
        b = process(title="Karnataka government approves 200 crore Bengaluru AI mission", source_url="https://example.net/b")
        self.assertFalse(same_event(a, b))

    def test_no_merge_different_states(self):
        a = process()
        b = {**a, "state": "Telangana"}
        self.assertFalse(same_event(a, b))


class PipelineTests(unittest.TestCase):
    def test_offline_end_to_end(self):
        # Explicit workspace path avoids restricted system temporary directories.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "input.jsonl"
            articles = [fixture(), fixture(article_id="a2", source_url="https://example.net/a2"),
                        fixture(article_id="a3", title="Private AI product review", description="Consumer gadget")]
            source.write_text("\n".join(json.dumps(a) for a in articles), encoding="utf-8")
            summary = run_pipeline(source, root / "out")
            self.assertEqual(summary["events"], 1)
            self.assertEqual(summary["excluded_articles"], 1)
            self.assertTrue((root / "out" / "ai_policy_events.csv").exists())
            self.assertEqual(len(json.loads((root / "out" / "event_sources.json").read_text())), 2)

    def test_missing_date_is_preserved_as_warning(self):
        events, _ = deduplicate([process(published_at_raw="")])
        report = validate_events(events)
        self.assertTrue(report["valid"])
        self.assertTrue(any("Missing date" in w["message"] for w in report["warnings"]))

    def test_provenance_validation(self):
        events, _ = deduplicate([process()])
        events[0]["source_count"] = 9
        self.assertFalse(validate_events(events)["valid"])

    def test_bad_json_fails_with_line_number(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            source = Path(directory) / "bad.jsonl"
            source.write_text('{"title":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                run_pipeline(source, Path(directory) / "out")


if __name__ == "__main__":
    unittest.main()