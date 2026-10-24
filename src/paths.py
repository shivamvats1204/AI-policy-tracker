"""Default storage locations, independent of the caller's working directory."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
ARTICLES_PATH = RAW_DATA_DIR / "articles.jsonl"
SOURCE_CONFIG_PATH = PROJECT_ROOT / "src" / "sources.json"
