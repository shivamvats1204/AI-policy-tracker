"""Check module entry points and filesystem paths without network requests."""

import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import collect, paths
from src.validate import validate_events


class PathTests(unittest.TestCase):
    def test_default_configuration_works_from_another_directory(self):
        with tempfile.TemporaryDirectory(dir=paths.PROJECT_ROOT) as directory:
            with contextlib.chdir(directory), patch.object(sys, "argv", ["collect"]):
                with patch.object(collect, "Collector") as collector, contextlib.redirect_stdout(io.StringIO()):
                    collector.return_value.run.return_value = [{}]
                    self.assertEqual(collect.main(), 0)
                    sources = collector.return_value.run.call_args.args[0]
                    self.assertGreater(len(sources), 1)
                    self.assertEqual({source["state"] for source in sources if source.get("state")},
                                     {"Karnataka", "Telangana", "Tamil Nadu", "Maharashtra", "Uttar Pradesh"})

    def test_defaults_stay_in_repository_from_another_directory(self):
        expected_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(dir=expected_root) as directory:
            with contextlib.chdir(directory):
                importlib.reload(paths)
                self.assertEqual(paths.PROJECT_ROOT, expected_root)
                self.assertEqual(paths.ARTICLES_PATH, expected_root / "data" / "raw" / "articles.jsonl")
                self.assertEqual(paths.PROCESSED_DATA_DIR, expected_root / "data" / "processed")

    def test_collector_default_output_from_another_directory(self):
        config = paths.PROJECT_ROOT / "src" / "sources.json"
        with tempfile.TemporaryDirectory(dir=paths.PROJECT_ROOT) as directory:
            with contextlib.chdir(directory), patch.object(sys, "argv", ["collect", "--config", str(config)]):
                with patch.object(collect, "Collector") as collector, contextlib.redirect_stdout(io.StringIO()):
                    collector.return_value.run.return_value = [{}]
                    self.assertEqual(collect.main(), 0)
                    collector.assert_called_once_with(paths.RAW_DATA_DIR, 2.0)

    def test_explicit_relative_paths_use_working_directory(self):
        with tempfile.TemporaryDirectory(dir=paths.PROJECT_ROOT) as directory:
            config = Path(directory) / "sources.json"
            config.write_text((paths.PROJECT_ROOT / "src" / "sources.json").read_text(encoding="utf-8"), encoding="utf-8")
            with contextlib.chdir(directory), patch.object(sys, "argv", ["collect", "--config", "sources.json", "--output", "custom raw"]):
                with patch.object(collect, "Collector") as collector, contextlib.redirect_stdout(io.StringIO()):
                    collector.return_value.run.return_value = [{}]
                    self.assertEqual(collect.main(), 0)
                    collector.assert_called_once_with(Path("custom raw"), 2.0)

    def test_collector_direct_script_from_another_directory(self):
        with tempfile.TemporaryDirectory(dir=paths.PROJECT_ROOT) as directory:
            result = subprocess.run(
                [sys.executable, str(paths.PROJECT_ROOT / "src" / "collect.py"), "--help"],
                cwd=directory, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)

    def test_collector_module_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-m", "src.collect", "--help"],
            cwd=paths.PROJECT_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)

    def test_validator_package_import(self):
        self.assertTrue(validate_events([])["valid"])


if __name__ == "__main__":
    unittest.main()
