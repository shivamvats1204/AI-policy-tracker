"""Enrich saved RSS articles and rebuild exports without recollecting feeds."""

import argparse
import json
from pathlib import Path

from .collect import Collector
from .paths import ARTICLES_PATH, PROCESSED_DATA_DIR
from .pipeline import run_pipeline


def enrich_and_process(input_path: Path, output: Path, limit: int = 30, delay: float = 2.0):
    if limit < 0 or delay < 1:
        raise ValueError("Enrichment limit must be nonnegative and delay at least one second")
    collector = Collector(input_path.parent, delay)
    try:
        enrichment = collector.enrich_saved(input_path, limit)
    finally:
        collector.session.close()
    return {**run_pipeline(input_path, output), "enrichment": enrichment}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ARTICLES_PATH)
    parser.add_argument("--output", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    try:
        print(json.dumps(enrich_and_process(args.input, args.output, args.limit, args.delay), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Enrichment failed: {exc}\n")


if __name__ == "__main__":
    main()
