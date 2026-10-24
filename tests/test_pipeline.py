"""Synthetic fixtures only: these are not authentic news records."""

import json
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from src.classify import classify_article
from src.deduplicate import deduplicate, same_event
from src.normalize import detect_states, normalize_article, normalize_date, normalize_state
from src.paths import PROJECT_ROOT
from src.pipeline import run_pipeline
from src.validate import validate_events


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

    def test_ambiguous_abbreviations_need_state_context(self):
        cases = {
            "local MP welcomes the Karnataka AI task force": ["Karnataka"],
            "MP Avinash Reddy taken into preventive custody": [],
            "MP's son booked after crash": [],
            "AP reported that officials met": [],
            "MP government signs MoU for AI": ["Madhya Pradesh"],
            "AI centre of excellence in MP": ["Madhya Pradesh"],
            "AP government launches AI portal": ["Andhra Pradesh"],
            "Across AP, schools will get AI tutors": ["Andhra Pradesh"],
            "AP, Telangana governments to set up AI university": ["Andhra Pradesh", "Telangana"],
            "Telangana and AP cabinets approve AI policy": ["Andhra Pradesh", "Telangana"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(detect_states(text), expected)

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
    def test_empty_input(self):
        self.assertEqual(deduplicate([]), ([], []))

    def test_headline_case_and_punctuation(self):
        self.assertTrue(same_event(process(), process(
            title="KARNATAKA GOVERNMENT APPROVES: Bengaluru AI mission!")))

    def test_distinct_projects_on_same_day_remain_separate(self):
        self.assertFalse(same_event(process(), process(
            title="Karnataka government approves Mysuru AI mission")))

    def test_uncertain_dates_and_date_bases_remain_separate(self):
        for raw in ("", "2026", "2026-10"):
            with self.subTest(raw=raw):
                self.assertFalse(same_event(process(published_at_raw=raw), process(published_at_raw=raw)))
        self.assertFalse(same_event(process(), process(
            event_date_raw="2026-10-01", event_date_evidence="Approved on 1 October")))
        self.assertFalse(same_event(process(date_origin="time_element_unverified"),
                                    process(date_origin="time_element_unverified")))

    def test_conflicting_description_amounts_remain_separate(self):
        self.assertFalse(same_event(
            process(description="Karnataka government approved a 100 crore AI mission."),
            process(description="Karnataka government approved a 200 crore AI mission.")))

    def test_source_count_counts_urls_and_links_preserve_articles(self):
        first = process(raw_snapshot="snapshots/first.bin")
        second = process(article_id="a2", raw_snapshot="snapshots/second.bin")
        events, links = deduplicate([first, second])
        self.assertEqual(events[0]["source_count"], 1)
        self.assertEqual(events[0]["source_urls"], [first["source_url"]])
        self.assertEqual({link["article_id"] for link in links}, {"a1", "a2"})
        self.assertEqual({link["raw_snapshot"] for link in links},
                         {"snapshots/first.bin", "snapshots/second.bin"})
        self.assertTrue(all(link["event_id"] == events[0]["event_id"] for link in links))

    def test_stable_output_without_mutating_input(self):
        articles = [process(), process(article_id="a2", source_url="https://example.net/a2")]
        before = deepcopy(articles)
        self.assertEqual(deduplicate(articles), deduplicate(reversed(articles)))
        self.assertEqual(articles, before)
        self.assertEqual(deduplicate([articles[0]])[0][0]["event_id"],
                         deduplicate(articles)[0][0]["event_id"])

    def test_missing_description_uses_title_and_requires_review(self):
        events, _ = deduplicate([process(description="", review_status="verified")])
        self.assertEqual(events[0]["summary"], events[0]["title"])
        self.assertEqual(events[0]["review_status"], "needs_review")
        self.assertTrue(validate_events(events)["valid"])

    def test_excluded_records_do_not_create_events(self):
        self.assertEqual(deduplicate([process(title="Private AI product review", description="Consumer gadget")]), ([], []))

    def test_unknown_dates_get_distinct_event_ids(self):
        events, _ = deduplicate([process(published_at_raw=""), process(published_at_raw="")])
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])
        self.assertTrue(validate_events(events)["valid"])

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
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
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

    def test_scope_defaults_to_tracked_states_and_can_include_all_states(self):
        articles = [fixture(),
                    fixture(article_id="k1", source_url="https://example.net/kerala",
                            title="Kerala government approves AI mission",
                            description="The Kerala government approved an artificial intelligence mission.")]
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            root = Path(directory)
            source = root / "input.jsonl"
            source.write_text("\n".join(json.dumps(a) for a in articles), encoding="utf-8")
            default = run_pipeline(source, root / "default")
            everywhere = run_pipeline(source, root / "all", None)
            self.assertEqual(default["events"], 1)
            excluded = json.loads((root / "default" / "excluded.json").read_text())
            self.assertEqual(excluded[0]["exclusion_reasons"], ["out_of_scope_state"])
            self.assertEqual(everywhere["events"], 2)
            states = {event["state"] for event in json.loads((root / "all" / "events.json").read_text())}
            self.assertEqual(states, {"Karnataka", "Kerala"})
            self.assertIsNone(json.loads((root / "all" / "coverage.json").read_text())["tracked_states"])

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
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            source = Path(directory) / "bad.jsonl"
            source.write_text('{"title":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                run_pipeline(source, Path(directory) / "out")


if __name__ == "__main__":
    unittest.main()
