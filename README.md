# AI policy tracker

Launch the Streamlit dashboard from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL printed in the terminal (normally `http://localhost:8501`).
The dashboard provides event search and filters, CSV/JSON downloads, source
provenance, exclusions, validation reports, and collection logs. Use **Update
data** in the sidebar to process saved articles or collect and process configured
sources. New collections are saved separately under `data/runs/`; previous
datasets are preserved. Select a recent run or use **Custom folders** to open
other exports. If generated synthetic examples are available, the **Demo
(synthetic)** dataset is clearly labeled and its run actions are disabled.

The dashboard can be tested with the rest of the project using
`python -m unittest discover -v` after installing the requirements.

RSS items with AI signals but insufficient metadata are now enriched from their
article pages, with a default limit of 10 requests per RSS source. Use
`--enrich-limit 0` to disable this or change the budget with `--enrich-limit N`.
Failed fetches preserve the original RSS records. Article and feed snapshots
remain linked for auditing.

For an existing run, use **Update data → Enrich and reprocess saved articles**,
or run:

```powershell
python -m src.enrich --input data/raw/articles.jsonl --output data/processed --limit 30
```

State detection and classification use the headline, description, and up to
three lead paragraphs. Events retain the matched evidence. Platform launches
and AI-based public services can qualify as government deployments; private
funding stories, related-story blocks, and explicitly negated launches remain
excluded. These rules produce review candidates, not verified policy facts.

Events are limited to Karnataka, Telangana, Tamil Nadu, Maharashtra and Uttar
Pradesh by default. Turn on **Update data → Include all Indian states** in the
dashboard, or pass `--all-states` to `python -m src.pipeline` or
`python -m src.enrich`, to keep events from every Indian state; the dataset's
scope is shown under the headline metrics. The short abbreviations "MP" and "AP"
only name a state in context (for example "MP government"), because they
usually mean Member of Parliament or Associated Press.

The default configuration is `src/sources.json`: 21 real source URLs covering
Karnataka, Telangana, Tamil Nadu, Maharashtra, and Uttar Pradesh. It combines
five Google News AI-policy searches, eight Indian Express RSS feeds, five
official government listings, and three regional news listings. Inspect the list
under **Update data → Configured sources**. The smaller
`src/sources.example.json` remains available as a minimal configuration example.
See [source notes](docs/sources.md) for coverage and collection limitations.

Run these commands from the repository root:

```sh
python -m pip install -r requirements.txt
python -m src.collect --config src/sources.json
python -m src.pipeline
python -m unittest discover
```

The collector stores articles in `data/raw/articles.jsonl`, snapshots in
`data/raw/snapshots/`, and its audit log in `data/raw/fetch_log.jsonl`.
The pipeline writes exports to `data/processed/`. These defaults are anchored
to the repository directory even when scripts are launched from another folder.

To use your own configuration or storage locations:

```sh
python -m src.collect --config src/sources.json --output data/custom-raw
python -m src.pipeline --input data/custom-raw/articles.jsonl --output data/custom-processed
```

Explicit relative paths use your current working directory; absolute paths also
work. Direct script execution (`python src/collect.py` or
`python src/pipeline.py`, with the same options) is supported. Snapshot paths
stored in article records and fetch logs are relative to the collector's output
directory.

Deduplication merges matching headlines with the same state, category, status,
and known day/date basis, retaining each article's source link. Paraphrased
headlines, conflicting numbers, and uncertain dates remain separate candidates
for human review.
