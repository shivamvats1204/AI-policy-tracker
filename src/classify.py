"""Explainable candidate filtering and classification; all results need review."""

import re


CATEGORY_RULES = (
    ("AI Regulation/Ethics", r"\b(?:regulat\w*|ethic\w*|responsible AI|AI governance|guidelines?)\b"),
    ("AI Budget/Funding", r"\b(?:budgets?|allocat\w*|funding|outlay)\b"),
    ("AI Task Force/Centre of Excellence", r"\b(?:task[ -]force|committees?|cent(?:re|er)s? of excellence|CoE)\b"),
    ("AI MoU/Partnership", r"\b(?:MoUs?|memorandum of understanding|partners?\w*|agreements?)\b"),
    ("Government AI Deployment", r"\b(?:deploy\w*|roll[ -]?out|implement\w*|procure\w*|adopt\w*)\b"),
    ("AI Policy/Mission", r"\b(?:polic(?:y|ies)|missions?|strateg(?:y|ies)|frameworks?|initiatives?)\b"),
)
CATEGORIES = {category for category, _ in CATEGORY_RULES}
AI = re.compile(r"\b(?:AI|GenAI|artificial intelligence|machine learning|LLMs?)\b", re.I)
GOVERNMENT = re.compile(r"\b(?:government|govt|cabinet|department|minister|secretary|state government|authority)\b", re.I)
ACTION = re.compile(r"\b(?:announc\w*|approv\w*|launch\w*|sign\w*|establish\w*|deploy\w*|allocat\w*|constitut\w*|adopt\w*|implement\w*|propos\w*|cancel\w*|scrap\w*|withdraw\w*)\b", re.I)
STATUS_RULES = (
    ("cancelled", r"\b(?:cancel\w*|scrap\w*|withdraw\w*)\b"),
    ("proposed", r"\b(?:propos\w*|plans? to|planning|consider\w*)\b"),
    ("approved", r"\b(?:approv\w*|cleared)\b"),
    ("launched", r"\b(?:launch\w*|inaugurat\w*|roll[ -]?out)\b"),
    ("implemented", r"\b(?:implement\w*|deploy\w*|operational)\b"),
    ("announced", r"\b(?:announc\w*|signed|establish\w*|constitut\w*)\b"),
)
STATUSES = {status for status, _ in STATUS_RULES} | {"unknown"}


def classify_article(article: dict) -> dict:
    """Use headline/description, avoiding unrelated article-body boilerplate.

    Negation and multi-event articles require human review. Categories/statuses
    are rule-based suggestions, not verified policy facts.
    """
    title = str(article.get("title") or "")
    text = title + " " + str(article.get("description") or "")
    signals = {"ai": bool(AI.search(text)), "government": bool(GOVERNMENT.search(text)),
               "action": bool(ACTION.search(text)), "state": bool(article.get("state"))}
    matches = [category for category, pattern in CATEGORY_RULES if re.search(pattern, text, re.I)]
    reasons = [f"missing_{key}_signal" for key, present in signals.items() if not present]
    if not matches:
        reasons.append("no_policy_category")
    # Prefer a status explicitly present in the headline over one in a description.
    status = "unknown"
    for context in (title, text):
        status = next((name for name, pattern in STATUS_RULES if re.search(pattern, context, re.I)), "unknown")
        if status != "unknown":
            break
    return {**article, "included": not reasons,
            "category": matches[0] if matches else "", "category_matches": matches,
            "status": status, "classification_signals": signals,
            "exclusion_reasons": reasons, "review_status": "needs_review"}