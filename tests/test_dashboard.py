"""Dashboard interaction and data-action tests without live network access."""

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.dashboard import collect_and_process, csv_export
from src.paths import PROJECT_ROOT
from src.pipeline import run_pipeline
from src.settings import TRACKED_STATES
from tests.test_pipeline import fixture


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.processed = self.root / "processed"
        self.raw.mkdir()

    def app(self):
        at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
        at.session_state["dataset"] = "Custom folders"
        at.session_state["raw_folder"] = str(self.raw)
        at.session_state["processed_folder"] = str(self.processed)
        return at.run()

    def populate(self, extra=()):
        records = [fixture(), fixture(article_id="a2", source_url="https://example.net/coverage"),
                   fixture(article_id="a3", source_url="https://example.net/launch",
                           title="Karnataka government launches Bengaluru AI mission"),
                   fixture(article_id="a4", source_url="https://example.net/private",
                           title="Private gadget review", description="Consumer electronics"),
                   *extra]
        source = self.raw / "articles.jsonl"
        source.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        run_pipeline(source, self.processed)

    def test_empty_dataset_explains_how_to_start(self):
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, "0")
        self.assertTrue(any("No event export yet" in info.value for info in at.info))
        self.assertTrue(at.button(key="process").disabled)

    def test_populated_dataset_and_filters(self):
        self.populate()
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual([metric.value for metric in at.metric], ["2", "1", "4", "1"])
        at.multiselect(key="status").set_value(["approved"]).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.tabs[0].dataframe[0].value), 1)
        at.text_input(key="search").set_value("no matching policy").run()
        self.assertFalse(at.exception)
        self.assertTrue(any("No matching events" in info.value for info in at.info))

    def test_failed_validation_hides_event_exports(self):
        self.populate()
        report = self.processed / "validation.json"
        report.write_text('{"valid": false, "errors": [{"message": "test failure"}]}', encoding="utf-8")
        at = self.app()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, "0")
        self.assertTrue(any("validation is missing or failed" in error.value for error in at.error))
        self.assertFalse(any(item.label == "Download filtered CSV" for item in at.get("download_button")))

    def test_corrupt_json_is_reported_without_crashing(self):
        self.processed.mkdir()
        (self.processed / "events.json").write_text("not json", encoding="utf-8")
        at = self.app()
        self.assertFalse(at.exception)
        self.assertTrue(any("Unable to load" in error.value for error in at.error))

    def test_process_button_builds_exports(self):
        self.populate()
        (self.processed / "events.json").unlink()
        at = self.app()
        at.button(key="process").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, "2")
        self.assertTrue((self.processed / "events.json").is_file())

    def test_collect_failure_is_visible(self):
        at = self.app()
        at.text_input(key="config").set_value(str(self.root / "missing-config.json")).run()
        at.button(key="collect").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("nonempty source array" in error.value for error in at.error))

    def test_enrichment_button_uses_selected_saved_run(self):
        self.populate()
        at = self.app()
        with patch("src.enrich.enrich_and_process", return_value={"events": 2, "enrichment": {"fetched": 1}}) as action:
            at.button(key="enrich").click().run()
        self.assertFalse(at.exception)
        action.assert_called_once_with(self.raw / "articles.jsonl", self.processed, 30, 2.0, TRACKED_STATES)
        self.assertTrue(any("Enriched 1 articles" in notice.value for notice in at.success))

    def test_enrichment_and_collection_honor_all_states_toggle(self):
        self.populate()
        at = self.app()
        at.toggle(key="all_states").set_value(True).run()
        with patch("src.enrich.enrich_and_process", return_value={"events": 2, "enrichment": {"fetched": 1}}) as action:
            at.button(key="enrich").click().run()
        action.assert_called_once_with(self.raw / "articles.jsonl", self.processed, 30, 2.0, None)
        with patch("src.dashboard.collect_and_process", return_value=(self.root, {"events": 2, "excluded_articles": 1})) as action:
            at.button(key="collect").click().run()
        self.assertEqual(action.call_args.kwargs["tracked_states"], None)

    def test_all_states_toggle_keeps_events_from_other_states(self):
        self.populate(extra=[fixture(article_id="k1", source_url="https://example.net/kerala",
                                     title="Kerala government approves AI mission",
                                     description="The Kerala government approved an artificial intelligence mission.")])
        at = self.app()
        self.assertEqual([metric.value for metric in at.metric], ["2", "1", "5", "2"])
        self.assertTrue(any("Karnataka, Telangana" in caption.value for caption in at.caption))
        at.toggle(key="all_states").set_value(True).run()
        at.button(key="process").click().run()
        self.assertFalse(at.exception)
        self.assertEqual([metric.value for metric in at.metric], ["3", "2", "5", "1"])
        self.assertTrue(any("Dataset scope: all Indian states" in caption.value for caption in at.caption))
        at.toggle(key="all_states").set_value(False).run()
        at.button(key="process").click().run()
        self.assertEqual(at.metric[0].value, "2")
        self.assertTrue(any("Dataset scope: Karnataka" in caption.value for caption in at.caption))

    def test_collect_and_process_passes_requested_scope(self):
        config = self.root / "sources.json"
        config.write_text(json.dumps([{"name": "Synthetic", "kind": "rss", "url": "https://example.org/feed"}]), encoding="utf-8")
        calls = ((), (TRACKED_STATES,), (None,))
        for extra in calls:
            with self.subTest(scope=extra):
                with patch("src.dashboard.Collector") as collector, \
                        patch("src.dashboard.run_pipeline", return_value={"events": 0}) as pipeline:
                    collector.return_value.run.return_value = [{"title": "synthetic"}]
                    collect_and_process(config, 20, 2.0, self.root / "runs", *extra)
                self.assertEqual(pipeline.call_args.args[2], extra[0] if extra else TRACKED_STATES)

    def test_collection_switches_to_new_run(self):
        self.populate()
        at = self.app()
        with patch("src.dashboard.collect_and_process", return_value=(self.root, {"events": 2, "excluded_articles": 1})):
            at.button(key="collect").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, "2")
        self.assertTrue(any("Collection complete" in notice.value for notice in at.success))

    def test_csv_download_preserves_formula_protection(self):
        value = csv_export([{"title": "=1+1", "summary": " @SUM(A1)", "source_urls": ["https://example.org"]}])
        rows = list(csv.DictReader(io.StringIO(value.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["title"], "'=1+1")
        self.assertEqual(rows[0]["summary"], "' @SUM(A1)")
        self.assertEqual(json.loads(rows[0]["source_urls"]), ["https://example.org"])

    def test_unsuccessful_collection_preserves_previous_dataset(self):
        self.populate()
        before = (self.raw / "articles.jsonl").read_bytes()
        config = self.root / "sources.json"
        config.write_text(json.dumps([{"name": "Synthetic", "kind": "rss", "url": "https://example.org/feed"}]), encoding="utf-8")
        with patch("src.dashboard.Collector") as collector:
            collector.return_value.run.return_value = []
            with self.assertRaisesRegex(ValueError, "No articles collected"):
                collect_and_process(config, 20, 2.0, self.root / "runs")
        self.assertEqual((self.raw / "articles.jsonl").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
