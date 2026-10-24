"""Launch with: python -m streamlit run app.py."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import streamlit as st

from src.dashboard import (
    collect_and_process, csv_export, filter_events, load_dataset, project_path, read_json, read_log,
)
from src.paths import PROJECT_ROOT, PROCESSED_DATA_DIR, RAW_DATA_DIR
from src.pipeline import run_pipeline
from src.enrich import enrich_and_process
from src.validate import valid_url
from src.settings import TRACKED_STATES


def sidebar():
    pending = st.session_state.pop("pending_paths", None)
    if pending:
        st.session_state.update(dataset="Custom folders", raw_folder=str(pending[0]),
                                processed_folder=str(pending[1]))
    choices = {"Project data": (RAW_DATA_DIR, PROCESSED_DATA_DIR)}
    for label, folder in (("Live verification", "live"), ("Demo (synthetic)", "controlled")):
        root = PROJECT_ROOT / "data" / "verification" / folder
        if (root / "processed" / "events.json").exists():
            choices[label] = (root / "raw", root / "processed")
    runs = PROJECT_ROOT / "data" / "runs"
    if runs.exists():
        for run in sorted(runs.iterdir(), reverse=True)[:10]:
            if (run / "processed" / "events.json").is_file():
                choices[f"Run {run.name}"] = (run / "raw", run / "processed")
    with st.sidebar:
        st.markdown("### AI Policy Tracker")
        st.caption("INDIA · POLICY OBSERVATORY")
        st.caption("Default event scope: " + ", ".join(TRACKED_STATES))
        st.divider()
        selected = st.selectbox("Dataset", [*choices, "Custom folders"], key="dataset")
        if selected == "Custom folders":
            st.session_state.setdefault("raw_folder", "data/raw")
            st.session_state.setdefault("processed_folder", "data/processed")
            raw = project_path(st.text_input("Raw data folder", key="raw_folder"))
            processed = project_path(st.text_input("Processed data folder", key="processed_folder"))
        else:
            raw, processed = choices[selected]
        demo = selected == "Demo (synthetic)"
        if demo:
            st.warning("Synthetic examples for exploring the dashboard.")
        with st.expander("Update data"):
            st.caption("Process saved articles, or collect into a new run folder.")
            st.caption("New RSS collections fetch missing context for up to 10 promising articles per source. Saved runs can be enriched in batches of 30.")
            if st.button("Process existing articles", disabled=demo or not (raw / "articles.jsonl").is_file(),
                         width="stretch", key="process"):
                try:
                    with st.spinner("Processing articles…"):
                        summary = run_pipeline(raw / "articles.jsonl", processed)
                    st.success(f"Processed: {summary['events']} events; {summary['excluded_articles']} excluded articles.")
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
            if st.session_state.get("config") in (None, "src/sources.example.json"):
                st.session_state["config"] = "src/sources.json"
            config = st.text_input("Source configuration", key="config")
            try:
                sources = read_json(project_path(config), list, [])
                states = sorted({source.get("state") for source in sources if source.get("state")})
                st.caption(f"{len(sources)} configured sources · {len(states)} states")
                with st.expander("Configured sources"):
                    st.dataframe([{"Name": source.get("name", ""), "State": source.get("state", "All states"),
                                   "Kind": source.get("kind", ""), "URL": source.get("url", "")} for source in sources],
                                 hide_index=True, width="stretch",
                                 column_config={"URL": st.column_config.LinkColumn("URL")})
            except (OSError, ValueError) as exc:
                st.warning(str(exc))
            limit = st.number_input("Maximum articles per source", min_value=1, max_value=200, value=20, step=1)
            delay = st.number_input("Request delay (seconds)", min_value=1.0, max_value=60.0, value=2.0)
            if st.button("Enrich and reprocess saved articles", disabled=demo or not (raw / "articles.jsonl").is_file(),
                         width="stretch", key="enrich"):
                try:
                    with st.spinner("Fetching context for up to 30 promising RSS articles…"):
                        summary = enrich_and_process(raw / "articles.jsonl", processed, 30, float(delay))
                    st.success(f"Enriched {summary['enrichment']['fetched']} articles; exported {summary['events']} events.")
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
            if st.button("Collect and process", type="primary", disabled=demo, width="stretch", key="collect"):
                try:
                    with st.spinner("Collecting sources and processing articles…"):
                        run, summary = collect_and_process(project_path(config), int(limit), float(delay))
                    st.session_state["pending_paths"] = (run / "raw", run / "processed")
                    st.session_state["notice"] = f"Collection complete: {summary['events']} events; {summary['excluded_articles']} excluded articles."
                    st.rerun()
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
        st.button("Refresh outputs", width="stretch")
        with st.expander("Storage locations"):
            st.caption("Raw articles")
            st.code(str(raw), language=None)
            st.caption("Processed exports")
            st.code(str(processed), language=None)
        st.divider()
        st.caption("Rule-generated candidates require human review. Coverage depends on configured sources.")
    return raw, processed, demo


def event_browser(events, links):
    st.subheader("Explore events")
    if not events:
        st.info("No policy events in this dataset yet. Inspect Excluded articles to see why records were filtered out, or use Update data to collect additional sources.")
        return
    query = st.text_input("Search events", placeholder="Search a policy, state, or publisher", key="search")
    cols = st.columns(3)
    selections = []
    for col, field, label in zip(cols, ("state", "category", "status"), ("States", "Categories", "Statuses")):
        selections.append(col.multiselect(label, sorted({event.get(field, "") for event in events} - {""}), key=field))
    filtered = filter_events(events, query, *selections)
    with st.expander("Date filters"):
        apply_dates = st.checkbox("Filter by date", key="apply_dates")
        left, right = st.columns(2)
        start = left.date_input("From", value=date(2020, 1, 1), disabled=not apply_dates)
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        end = right.date_input("To", value=today, disabled=not apply_dates)
        st.caption("Date filtering includes records with an exact day. Publication dates may differ from event dates.")
    if apply_dates:
        if start > end:
            st.error("The start date must be on or before the end date.")
            return
        filtered = [event for event in filtered if event.get("date_precision") == "day"
                    and start.isoformat() <= event.get("date", "") <= end.isoformat()]
    filtered = sorted(filtered, key=lambda event: (event.get("date", ""), event.get("title", "")), reverse=True)
    st.caption(f"Showing {len(filtered)} of {len(events)} events")
    if not filtered:
        st.info("No matching events. Adjust your filters, inspect excluded articles, or collect additional sources.")
        return
    columns = ("date", "state", "title", "category", "status", "source_count", "source_url")
    st.dataframe([{key: event.get(key, "") for key in columns} for event in filtered],
                 hide_index=True, width="stretch",
                 column_config={"source_url": st.column_config.LinkColumn("Primary source"),
                                "source_count": "Sources", "date": "Date", "title": "Event"})
    first, second = st.columns(2)
    first.download_button("Download filtered CSV", csv_export(filtered), "ai_policy_events.csv", "text/csv")
    second.download_button("Download filtered JSON", json.dumps(filtered, ensure_ascii=False, indent=2),
                           "events.json", "application/json")
    with st.expander("Coverage by state"):
        counts = Counter(event["state"] for event in filtered)
        st.bar_chart({"State": list(counts), "Events": list(counts.values())},
                     x="State", y="Events", color="#18766B")
    st.divider()
    st.subheader("Event details")
    by_id = {event["event_id"]: event for event in filtered}
    selected = st.selectbox("Choose an event", list(by_id), key="event_detail",
                            format_func=lambda value: f"{by_id[value].get('date') or 'Undated'} · {by_id[value]['title']}")
    event = by_id[selected]
    st.write(event.get("summary", ""))
    st.caption(f"{event['state']} · {event['status']} · {event.get('review_status', 'needs_review')}"
               f" · Date basis: {event.get('date_basis', 'unknown')} · Precision: {event.get('date_precision', 'unknown')}")
    for index, url in enumerate(event.get("source_urls", []), 1):
        if valid_url(url):
            st.link_button(f"Source {index}", url)
    evidence = [link for link in links if link.get("event_id") == selected]
    if evidence:
        with st.expander("Article provenance"):
            st.dataframe(evidence, hide_index=True, width="stretch")
    with st.expander("Full event record"):
        st.json(event)
    with st.expander("Why this event was included"):
        st.json({"state_evidence": event.get("state_evidence", ""),
                 "classification_evidence": event.get("classification_evidence", {})})


def exclusion_browser(excluded):
    st.subheader("Excluded articles")
    st.caption("Inspect why a collected article did not become an event.")
    if not excluded:
        st.info("No excluded articles in this dataset.")
        return
    reasons = Counter(reason for record in excluded for reason in record.get("exclusion_reasons", []))
    reason = st.selectbox("Exclusion reason", ["All reasons", *sorted(reasons)], key="reason")
    rows = []
    for record in excluded:
        if reason != "All reasons" and reason not in record.get("exclusion_reasons", []):
            continue
        article = record.get("article", record)
        rows.append({"Title": article.get("title", "Untitled"), "Source": article.get("source_name", ""),
                     "Reasons": ", ".join(record.get("exclusion_reasons", [])),
                     "URL": article.get("source_url", "")})
    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn("Source URL")})
    st.download_button("Download exclusions", json.dumps(excluded, ensure_ascii=False, indent=2),
                       "excluded.json", "application/json")


def main():
    st.set_page_config(page_title="AI Policy Tracker", page_icon="◈", layout="wide")
    st.markdown("""<style>
        .block-container {padding-top: 2.5rem; max-width: 1500px;}
        [data-testid="stMetric"] {background: #EDF2EF; padding: 1.1rem; border-radius: 12px;}
        h1 {letter-spacing: -0.045em;}
        </style>""", unsafe_allow_html=True)
    raw, processed, demo = sidebar()
    st.caption("INDIA / ARTIFICIAL INTELLIGENCE / PUBLIC POLICY")
    st.title("Policy, in focus.")
    st.write("Track state AI initiatives, follow policy milestones, and trace every event to its sources.")
    if demo:
        st.warning("DEMO DATA — All events in this dataset are synthetic examples.")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    try:
        data = load_dataset(processed)
    except (OSError, ValueError, TypeError) as exc:
        st.error(f"Unable to load this dataset: {exc}")
        st.info("Choose another dataset or process the raw articles to rebuild these exports.")
        st.stop()
    events = data["events"] if data["publishable"] else []
    if not data["exists"]:
        st.info("No event export yet. Use Update data in the sidebar to process existing articles or collect sources.")
    elif not data["publishable"]:
        st.error("Event exports are unavailable because validation is missing or failed. Inspect Validation & logs, then reprocess the articles.")
    cards = st.columns(4)
    for col, label, value in zip(cards, ("Policy events", "States covered", "Articles processed", "Excluded articles"),
                                 (len(events), len({event["state"] for event in events}),
                                  len(data["articles"]), len(data["excluded"]))):
        col.metric(label, value)
    st.caption("Counts describe the selected dataset. Events remain candidates until reviewed.")
    scope = data["coverage"].get("tracked_states")
    if scope:
        st.caption("Dataset scope: " + ", ".join(scope))
    tabs = st.tabs(["Events", "Excluded articles", "Validation & logs"])
    with tabs[0]:
        if data["publishable"] and not events and data["articles"]:
            st.warning(f"All {len(data['articles'])} processed articles were excluded. The pipeline completed successfully, but this source sample produced no matching AI policy events.")
        demo_path = PROJECT_ROOT / "data" / "verification" / "controlled" / "processed" / "events.json"
        if not events and not demo and demo_path.exists():
            def select_demo():
                st.session_state["dataset"] = "Demo (synthetic)"
            st.button("Explore demo events", on_click=select_demo, key="show_demo")
        event_browser(events, data["event_sources"])
    with tabs[1]:
        exclusion_browser(data["excluded"])
    with tabs[2]:
        st.subheader("Validation")
        if data["publishable"]:
            st.success("Export validation passed.")
        elif not data["report"]:
            st.info("No saved validation report. Process the articles to generate one.")
        for label, report in (("Saved validation report", data["report"]),
                              ("Current export check", data["current_report"])):
            with st.expander(label):
                st.json(report)
        st.subheader("Recent collection activity")
        st.caption("Last 100 log entries. Collection timestamps are recorded in UTC.")
        try:
            logs = read_log(raw)
            if logs:
                st.dataframe(logs, hide_index=True, width="stretch")
            else:
                st.info("No collection log for this dataset.")
        except OSError as exc:
            st.error(f"Could not read collection log: {exc}")


if __name__ == "__main__":
    main()
