# Live source configuration

`src/sources.json` is the default for the collector CLI and Streamlit dashboard.
The URLs and Google News query templates come from the user's supplied
"Authentic, Scrapeable Sources for an Indian State AI-Policy News Pipeline"
research document. The file contains 21 sources:

| Source group | Sources | Coverage |
| --- | ---: | --- |
| Google News RSS AI-policy searches | 5 | One targeted query for each selected state |
| Indian Express city RSS | 7 | Bangalore, Hyderabad, Chennai, Mumbai, Pune, Nagpur, Lucknow |
| Indian Express Artificial Intelligence RSS | 1 | Cross-state AI reporting |
| Official government listings | 5 | Karnataka EITBT orders, Startup Telangana policies, Tamil Nadu IT releases, Maharashtra IT/AI policies, Invest UP releases |
| Regional news listings | 3 | Telangana Today AI Mission; The New Indian Express Karnataka; DT Next Tamil Nadu |

The publisher lists the Indian Express feeds in its [RSS directory](https://indianexpress.com/rss/).
Google News searches use the supplied query terms with URL encoding and the
`hl=en-IN`, `gl=IN`, and `ceid=IN:en` parameters. This is an undocumented
discovery interface, and links may point to Google News redirects. RSS records
retain the original publisher name when the feed supplies it.

The collector checks robots rules at runtime. Unavailable, denied, or timed-out
sources are logged and skipped; other sources can still produce articles.
HTML listings use the semantic selectors provided in the research document.
An accessible listing can yield zero candidates when there are no matching AI
anchor labels, links lead off-site, or the page template changes.

Several official archives primarily link to PDFs or use generic "View" links.
The current HTML extractor does not ingest PDF content, execute JavaScript, or
infer events from catalogue headings. Official listing coverage is therefore
partial. A configured government URL does not make news-derived events
officially verified. All generated events remain candidates for human review.

Social-media accounts, API endpoints requiring keys, and reference PDFs from
the supplied document are not included as runnable HTML/RSS sources. They need
different ingestion adapters. No unverified RSS endpoint has been invented for
publishers that only supplied HTML pages.

Collect and process from the repository root:

```powershell
python -m src.collect --config src/sources.json
python -m src.pipeline
```

The CLI also accepts `python -m src.collect` without a config argument. The
dashboard writes each collection into its own `data/runs/` folder and switches
to the resulting dataset. Logs and exclusions distinguish source failures from
articles that were fetched successfully but did not match the policy rules.

## Live check on 24 October 2026

The initial 20-source run collected 80 RSS articles. The focused Telangana
Today AI Mission source collected five additional articles, and the corrected
DT Next selector collected one. The consolidated dataset is saved in
`data/runs/20261024-real-sources-verified/`, with per-source outcomes in
`source_check.json` and the exact configuration in `sources.json`.

All 86 articles were excluded by the original headline/description classifier;
there are zero exported events. Some relevant reporting is rejected when the
government connection only appears in article body text or when an AI platform
launch does not match the current category keywords. These are classification
limitations, rather than evidence that the articles contain no policy activity.
That observation predates the evidence/enrichment fix. The current classifier
uses a bounded article lead, and the collector enriches promising RSS items.
Existing runs can be rebuilt with `python -m src.enrich` or the dashboard's
**Enrich and reprocess saved articles** action.

Google News RSS searches were denied by robots rules during this check.
Karnataka's EITBT site failed certificate validation, and the Tamil Nadu IT
source was denied by its robots policy. Those sources remain configured so the
collector can recheck them on later runs. No certificate or robots checks were
disabled. Other official pages were accessible but yielded no eligible links.
