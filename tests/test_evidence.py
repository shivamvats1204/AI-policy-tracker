"""Regression checks for observed real exclusions and unrelated-context traps."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from src.classify import classify_article
from src.collect import Collector
from src.extract import extract_article
from src.normalize import normalize_article
from src.paths import PROJECT_ROOT
from tests.test_pipeline import fixture


BACKGROUND_FIXTURES = {
    "T-AIM targets at training 30,000 people in AI",
    "AI adoption major focus for Telangana: Jayesh Ranjan",
}


def classify(**values):
    return classify_article(normalize_article(fixture(**values)))


class EvidenceTests(unittest.TestCase):
    def test_publisher_body_container_excludes_restricted_and_related_content(self):
        html = '''<h1>Karnataka police AI modernisation</h1><div id="pcl-full-content" class="story_details">
            <p>Karnataka government constituted panels for AI modernisation.</p>
            <div class="paywall"><p>Restricted content.</p></div>
            <div class="related-articles"><p>Unrelated story.</p></div></div>'''
        article = extract_article(html, "https://example.org/story")
        self.assertNotIn("Restricted", article["body_text"])
        self.assertNotIn("Unrelated", article["body_text"])
        record = classify_article(normalize_article(article))
        self.assertTrue(record["included"])
        self.assertEqual(record["category"], "AI Task Force/Centre of Excellence")

    def test_structured_article_body_is_available_without_article_element(self):
        html = '<script type="application/ld+json">' + json.dumps({
            "@type": "NewsArticle", "headline": "AI mission launches a challenge",
            "articleBody": "The Government of Telangana launched an AI mission challenge.",
        }) + '</script>'
        article = extract_article(html, "https://example.org/story")
        self.assertTrue(classify_article(normalize_article(article))["included"])

    def test_real_articles_no_longer_rejected_for_missing_lead_context(self):
        examples = json.loads((PROJECT_ROOT / "tests" / "fixtures" / "real_article_regressions.json").read_text(encoding="utf-8"))
        for example in examples:
            with self.subTest(title=example["title"]):
                record = classify_article(normalize_article(example))
                if example["title"] in BACKGROUND_FIXTURES:
                    # An interview and a speech with no policy milestone: excluded on purpose,
                    # but never for missing lead context.
                    self.assertEqual(record["exclusion_reasons"], ["background_or_progress_report"])
                    self.assertEqual(record["article_type"], "background")
                    self.assertEqual(record["status"], "unknown")
                    continue
                self.assertTrue(record["included"], record["exclusion_reasons"])
                self.assertEqual(record["review_status"], "needs_review")
                self.assertTrue(record["classification_evidence"]["government"])

    def test_platform_launch_is_a_deployment(self):
        record = classify(title="Karnataka government launches AI skills platform", description="")
        self.assertTrue(record["included"])
        self.assertEqual(record["category"], "Government AI Deployment")

    def test_government_and_state_can_be_established_by_lead(self):
        record = classify(title="AI mission launches a new challenge", description="",
                          body_text="The Government of Telangana launched the AI mission challenge.")
        self.assertTrue(record["included"])
        self.assertEqual(record["state"], "Telangana")
        self.assertEqual(record["state_basis"], "article_lead")
        self.assertEqual(record["classification_evidence"]["government"][0]["field"], "article_lead")

    def test_state_hint_alone_is_not_evidence(self):
        record = classify(title="Government launches AI platform in Bengaluru", description="")
        self.assertFalse(record["included"])
        self.assertEqual(record["state"], "")

    def test_related_story_and_late_paragraph_do_not_supply_signals(self):
        for body in ("Also read: Karnataka government approves an AI mission.",
                     "Product prices changed.\nSales grew.\nCustomers subscribed.\nKarnataka government approves AI mission."):
            with self.subTest(body=body):
                record = classify(title="AI product review", description="Consumer gadget", body_text=body)
                self.assertFalse(record["included"])

    def test_private_funding_is_not_promoted_by_government_background(self):
        record = classify(title="Karnataka AI startup raises funds", description="Private funding round",
                          body_text="The Karnataka government launched an AI mission last year.")
        self.assertFalse(record["included"])
        self.assertIn("private_funding_story", record["exclusion_reasons"])

    def test_negated_launch_is_not_an_event(self):
        record = classify(title="Karnataka government will not launch an AI platform", description="")
        self.assertFalse(record["included"])
        self.assertIn("negated_policy_action", record["exclusion_reasons"])

    def test_ambiguous_lead_states_remain_unassigned(self):
        record = classify(title="Government announces AI mission", description="",
                          body_text="Karnataka and Telangana governments announced AI missions.")
        self.assertEqual(record["state"], "")
        self.assertFalse(record["included"])

    def test_member_of_parliament_does_not_add_a_second_state(self):
        record = classify(title="Karnataka government launches AI task force; local MP welcomes move",
                          description="")
        self.assertEqual(record["state"], "Karnataka")
        self.assertNotIn("multiple_states", record["normalization_issues"])
        self.assertTrue(record["included"], record["exclusion_reasons"])

    def test_significant_and_signal_are_not_action_verbs(self):
        for title in ("Karnataka government statement on AI: significant concerns remain",
                      "Karnataka government sees a signal of AI interest"):
            with self.subTest(title=title):
                record = classify(title=title, description="")
                self.assertFalse(record["included"])
                self.assertIn("missing_action_signal", record["exclusion_reasons"])

    def test_signing_still_counts_as_an_action(self):
        for verb in ("signs", "signed", "signing"):
            with self.subTest(verb=verb):
                record = classify(title=f"Karnataka government {verb} AI agreement", description="")
                self.assertTrue(record["included"], record["exclusion_reasons"])

    def test_plural_government_terms_count_as_government(self):
        record = classify(title="Karnataka and Kerala governments sign AI pact", description="",
                          body_text="")
        self.assertTrue(record["classification_signals"]["government"])
        record = classify(title="Karnataka unveils AI guidelines for departments", description="")
        self.assertTrue(record["classification_signals"]["government"])


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.collector = Collector(self.root)
        self.addCleanup(self.collector.session.close)
        self.record = fixture(title="Karnataka police AI modernisation", description="",
                              source_type="rss", raw_snapshot="snapshots/feed.bin")

    def test_failure_retains_feed_record(self):
        with patch.object(self.collector, "article", side_effect=requests.RequestException("unavailable")):
            result = self.collector.enrich_record(self.record)
        self.assertEqual(result["source_url"], self.record["source_url"])
        self.assertEqual(result["raw_snapshot"], "snapshots/feed.bin")
        self.assertEqual(result["enrichment_status"], "failed")

    def test_metadata_fallback_snapshot_provenance_and_cache(self):
        details = {"title": self.record["title"], "description": "", "published_at_raw": "",
                   "date_origin": "", "body_text": "Karnataka police announced an AI mission.",
                   "raw_snapshot": "snapshots/article.bin"}
        with patch.object(self.collector, "article", return_value=details) as fetch:
            result = self.collector.enrich_record(self.record)
            self.collector.enrich_record(self.record)
            fetch.assert_called_once()
        self.assertEqual(result["published_at_raw"], self.record["published_at_raw"])
        self.assertEqual(result["rss_snapshot"], "snapshots/feed.bin")
        self.assertEqual(result["raw_snapshot"], "snapshots/article.bin")
        self.assertTrue(classify_article(normalize_article(result))["included"])

    def test_saved_enrichment_is_bounded_and_prioritizes_state(self):
        national = {**self.record, "title": "AI tool gets an update", "source_url": "https://example.org/national"}
        path = self.root / "articles.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in [national, self.record]), encoding="utf-8")
        with patch.object(self.collector, "article", return_value={"body_text": "Context"}) as fetch:
            result = self.collector.enrich_saved(path, 1)
        fetch.assert_called_once_with(self.record["source_url"])
        self.assertEqual(result, {"attempted": 1, "fetched": 1, "remaining": 1})
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_malformed_saved_input_is_not_overwritten_or_fetched(self):
        path = self.root / "articles.jsonl"
        path.write_text('{"title":', encoding="utf-8")
        before = path.read_bytes()
        with patch.object(self.collector, "article") as fetch:
            with self.assertRaisesRegex(ValueError, "line 1"):
                self.collector.enrich_saved(path)
            fetch.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_rss_collection_fetches_insufficient_metadata(self):
        feed = b'<rss><channel><item><title>Karnataka police AI modernisation</title><link>https://example.org/story</link></item></channel></rss>'
        def fetch(url):
            if url.endswith('/robots.txt'):
                return 200, {}, b'User-agent: *\nAllow: /'
            if url.endswith('/feed'):
                return 200, {"Content-Type": "application/rss+xml"}, feed
            return 200, {"Content-Type": "text/html"}, b'<article><h1>Karnataka police AI modernisation</h1><p>Karnataka police announced an AI mission.</p></article>'
        with patch.object(self.collector, "_request", side_effect=fetch):
            records = self.collector.run([{"name": "Synthetic", "kind": "rss", "url": "https://example.org/feed"}])
        self.assertEqual(records[0]["enrichment_status"], "fetched")
        self.assertTrue(classify_article(normalize_article(records[0]))["included"])

    def test_rss_budget_zero_preserves_candidate(self):
        feed = b'<rss><channel><item><title>Karnataka police AI modernisation</title><link>https://example.org/story</link></item></channel></rss>'
        with patch.object(self.collector, "fetch", return_value=("https://example.org/feed", {}, feed, "snapshots/feed.bin")):
            with patch.object(self.collector, "article") as fetch:
                records = self.collector.run([{"name": "Synthetic", "kind": "rss", "url": "https://example.org/feed"}], enrich_limit=0)
                fetch.assert_not_called()
        self.assertEqual(records[0]["enrichment_status"], "budget_exhausted")

    def test_denied_article_is_not_fetched_and_feed_record_survives(self):
        feed = b'<rss><channel><item><title>Karnataka police AI modernisation</title><link>https://example.org/story</link></item></channel></rss>'
        def response(url):
            if url.endswith('/robots.txt'):
                return 200, {}, b'User-agent: *\nDisallow: /story\n'
            if url.endswith('/feed'):
                return 200, {}, feed
            self.fail("Denied article must not be fetched")
        with patch.object(self.collector, "_request", side_effect=response):
            records = self.collector.run([{"name": "Synthetic", "kind": "rss", "url": "https://example.org/feed"}])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["enrichment_status"], "failed")
        self.assertTrue((self.root / records[0]["raw_snapshot"]).exists())


if __name__ == "__main__":
    unittest.main()
