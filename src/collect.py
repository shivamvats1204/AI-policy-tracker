"""Bounded RSS/HTML collector with robots checks, raw snapshots and audit logs.

Install: python -m pip install requests beautifulsoup4
Run:     python collect.py --config sources.json --output data/raw

Configuration is a JSON array, for example:
[
  {"name": "Indian Express Bangalore", "state": "Karnataka", "kind": "rss",
   "url": "https://indianexpress.com/section/cities/bangalore/feed/"},
  {"name": "TN IT releases", "state": "Tamil Nadu", "kind": "listing",
   "url": "https://it.tn.gov.in/en/Press_Release",
   "selector": "main a[href]"}
]

Kinds: rss, listing, article. Listing selectors require source-specific inspection.
State is a discovery hint, not a confirmed event location. PDFs are logged as
unsupported. RSS candidates are retained without fetching their article pages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

from extract import discover_links, extract_article, extract_feed, web_url


USER_AGENT = "SAFLPolicyCollector/0.1"
MAX_BYTES = 5_000_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Collector:
    def __init__(self, output: Path, delay: float = 2.0):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        (output / "snapshots").mkdir(exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.delay = max(1.0, delay)
        self.last_request = {}
        self.robots = {}

    def log(self, **entry):
        with (self.output / "fetch_log.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps({"at": now(), **entry}, ensure_ascii=False) + "\n")

    def _request(self, url):
        host = urlsplit(url).netloc
        elapsed = time.monotonic() - self.last_request.get(host, 0)
        time.sleep(max(0, self.delay - elapsed))
        self.last_request[host] = time.monotonic()
        with self.session.get(url, timeout=(10, 25), stream=True,
                              allow_redirects=False) as response:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Response exceeds 5 MB limit")
                chunks.append(chunk)
            return response.status_code, response.headers.copy(), b"".join(chunks)

    def allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            try:
                status, headers, body = self._request(robots_url)
                # Fail closed for redirects, access errors and unavailable robots.
                if status in {404, 410}:
                    self.robots[origin] = True
                elif status == 200:
                    parser = robotparser.RobotFileParser(robots_url)
                    parser.parse(body.decode("utf-8-sig", errors="replace").splitlines())
                    self.robots[origin] = parser
                else:
                    self.robots[origin] = False
                self.log(url=robots_url, kind="robots", http_status=status)
            except (requests.RequestException, ValueError) as exc:
                self.robots[origin] = False
                self.log(url=robots_url, kind="robots", error=str(exc))
        policy = self.robots[origin]
        if isinstance(policy, bool):
            return policy
        wait = policy.crawl_delay(USER_AGENT) or policy.crawl_delay("*")
        if wait:
            host = parts.netloc
            time.sleep(max(0, wait - (time.monotonic() - self.last_request.get(host, 0))))
        return policy.can_fetch(USER_AGENT, url)

    def fetch(self, url):
        requested = url
        for _ in range(6):
            if not web_url(url):
                raise ValueError("Invalid HTTP(S) URL")
            if not self.allowed(url):
                raise ValueError(f"Robots disallow or unavailable: {url}")
            status, headers, body = self._request(url)
            self.log(url=url, requested_url=requested, http_status=status)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("Location")
                if not location:
                    raise ValueError("Redirect has no Location")
                url = urljoin(url, location)
                continue
            if status != 200:
                raise ValueError(f"HTTP {status}: {url}")
            digest = hashlib.sha256(body).hexdigest()
            relative = f"snapshots/{digest}.bin"
            (self.output / relative).write_bytes(body)
            self.log(url=url, snapshot=relative, sha256=digest,
                     content_type=headers.get("Content-Type", ""))
            return url, headers, body, relative
        raise ValueError("Too many redirects")

    def article(self, url):
        final, headers, body, snapshot = self.fetch(url)
        content_type = headers.get("Content-Type", "").lower()
        if "html" not in content_type and body.lstrip()[:1] != b"<":
            raise ValueError(f"Unsupported article content type: {content_type}")
        record = extract_article(body, final)
        record["raw_snapshot"] = snapshot
        return record

    def run(self, sources, limit=20):
        records = []
        seen = set()
        for source in sources:
            try:
                kind = source["kind"]
                if kind == "article":
                    candidates = [self.article(source["url"])]
                else:
                    final, headers, body, snapshot = self.fetch(source["url"])
                    if kind == "rss":
                        candidates = extract_feed(body, final)[:limit]
                        for candidate in candidates:
                            candidate["raw_snapshot"] = snapshot
                    else:
                        links = discover_links(body, final, source.get("selector", "main a[href], article a[href]"))
                        candidates = []
                        for link in links[:limit]:
                            try:
                                candidates.append(self.article(link["source_url"]))
                            except (requests.RequestException, ValueError) as exc:
                                self.log(url=link["source_url"], error=str(exc))
                for record in candidates:
                    identity = record["source_url"]
                    if identity in seen:
                        continue
                    seen.add(identity)
                    record.update({
                        "article_id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                        "state_candidate": source.get("state", ""),
                        "source_name": record.get("source_name") or source["name"],
                        "source_type": kind,
                        "discovery_url": source["url"],
                        "collected_at": now(),
                        "review_status": "candidate",
                    })
                    records.append(record)
            except (requests.RequestException, ValueError, KeyError) as exc:
                self.log(source=source.get("name"), url=source.get("url"), error=str(exc))
        # One complete run per file; fetch logs and content-addressed snapshots persist.
        with (self.output / "articles.jsonl").open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument("--limit", type=int, default=20, help="Maximum items per source")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    sources = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(sources, list) or not sources or args.limit < 1:
        parser.error("Provide a nonempty source array and positive limit")
    for source in sources:
        if (not isinstance(source, dict) or not source.get("name")
                or source.get("kind") not in {"rss", "listing", "article"}
                or not web_url(source.get("url", ""))):
            parser.error("Each source requires name, valid url, and kind rss/listing/article")
    collector = Collector(args.output, args.delay)
    try:
        records = collector.run(sources, args.limit)
    finally:
        collector.session.close()
    print(f"Collected {len(records)} candidates. Review {args.output / 'fetch_log.jsonl'} for failures.")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())