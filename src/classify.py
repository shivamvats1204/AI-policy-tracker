"""Explainable candidate filtering and classification; all results need review."""

import re

if __package__:
    from .evidence import context_parts
else:
    from evidence import context_parts


CATEGORY_RULES = (
    ("AI Regulation/Ethics", r"\b(?:regulat\w*|ethic\w*|responsible AI|AI governance|guidelines?)\b"),
    ("AI Budget/Funding", r"\b(?:budgets?|allocat\w*|funding|outlay)\b"),
    ("AI Task Force/Centre of Excellence", r"\b(?:task[ -]force|committees?|panels?|cent(?:re|er)s? of excellence|CoE)\b"),
    ("AI MoU/Partnership", r"\b(?:MoUs?|memorandum of understanding|partners?\w*|agreements?)\b"),
    ("Government AI Deployment", r"\b(?:deploy\w*|roll[ -]?out|implement\w*|procure\w*|adopt\w*|platforms?|e[- ]?sevai|public services?|skill enhancement)\b"),
    ("AI Policy/Mission", r"\b(?:polic(?:y|ies)|missions?|strateg(?:y|ies)|frameworks?|initiatives?)\b"),
)
CATEGORIES = {category for category, _ in CATEGORY_RULES}
AI = re.compile(r"\b(?:AI|GenAI|artificial intelligence|machine learning|LLMs?)\b", re.I)
GOVERNMENT = re.compile(r"\b(?:governments?|govts?|cabinet|departments?|ministers?|secretary|state government|authorit(?:y|ies)|police|municipal\w*|GHMC|TNeGA|ELCOT)\b", re.I)
ACTION = re.compile(r"\b(?:announc\w*|approv\w*|launch\w*|sign(?:s|ed|ing)?|establish\w*|deploy\w*|allocat\w*|constitut\w*|adopt\w*|implement\w*|propos\w*|cancel\w*|scrap\w*|withdraw\w*|unveil\w*|introduc\w*|set(?:ting)? up)\b", re.I)
STATUS_RULES = (
    ("cancelled", r"\b(?:cancel\w*|scrap\w*|withdraw\w*)\b"),
    ("proposed", r"\b(?:propos\w*|plans? to|planning|consider\w*)\b"),
    ("approved", r"\b(?:approv\w*|cleared)\b"),
    ("launched", r"\b(?:launch\w*|inaugurat\w*|roll[ -]?out)\b"),
    ("implemented", r"\b(?:implement\w*|deploy\w*|operational)\b"),
    ("announced", r"\b(?:announc\w*|signed|establish\w*|constitut\w*|unveil\w*|introduc\w*|set(?:ting)? up)\b"),
)
STATUSES = {status for status, _ in STATUS_RULES} | {"unknown"}
MILESTONE = re.compile(
    r"\b(?:announc\w*|approv\w*|launch\w*|sign(?:s|ed|ing)?|establish\w*|deploy\w*|"
    r"allocat\w*|constitut\w*|adopt(?:s|ed|ing)?|implement(?:s|ed|ing)?|propos\w*|"
    r"cancel\w*|scrap\w*|withdraw\w*|unveil\w*|introduc\w*|set(?:ting)? up|plans? to)\b", re.I)
BACKGROUND_HEADLINE = re.compile(
    r"\b(?:targets? (?:at|to)|major focus|progress report|overview|interview|journey so far)\b", re.I)


def classify_article(article: dict) -> dict:
    """Use headline, description and a bounded lead, retaining matched evidence.

    Negation and multi-event articles require human review. Categories/statuses
    are rule-based suggestions, not verified policy facts.
    """
    title = str(article.get("title") or "")
    parts = context_parts(article)
    text = " ".join(value for _, value in parts)
    # A policy story must introduce AI in its metadata or first lead paragraph.
    # Do not turn a private funding story into a policy event using a later sidebar.
    primary = title + " " + str(article.get("description") or "")
    lead = [value for field, value in parts if field == "article_lead"]
    topical = bool(AI.search(primary) or (lead and AI.search(lead[0])))
    signals = {"ai": topical, "government": bool(GOVERNMENT.search(text)),
               "action": bool(ACTION.search(text)), "state": bool(article.get("state"))}
    matches = [category for category, pattern in CATEGORY_RULES if re.search(pattern, text, re.I)]
    # Headlines describe the central event; background paragraphs can mention older
    # partnerships, budgets or policies that should not determine its category.
    category = ""
    category_basis = ""
    for field, value in parts:
        category = next((name for name, pattern in CATEGORY_RULES if re.search(pattern, value, re.I)), "")
        if category:
            category_basis = field
            break
    reasons = [f"missing_{key}_signal" for key, present in signals.items() if not present]
    if not matches:
        reasons.append("no_policy_category")
    background = bool(BACKGROUND_HEADLINE.search(title) and not MILESTONE.search(title))
    if background:
        reasons.append("background_or_progress_report")
    if (not GOVERNMENT.search(primary)
            and re.search(r"\b(?:raises?|raised|funding round|series [a-f])\b", title, re.I)):
        reasons.append("private_funding_story")
    if re.search(r"\b(?:no plans? to|(?:will|does|did) not|not planning to)\s+(?:\w+\s+){0,2}(?:launch|introduce|implement|adopt|establish|sign)\b", primary, re.I):
        reasons.append("negated_policy_action")
    # Prefer a status explicitly present in the headline over one in a description.
    status = "unknown"
    for context in (title, str(article.get("description") or ""), text):
        status = next((name for name, pattern in STATUS_RULES if re.search(pattern, context, re.I)), "unknown")
        if status != "unknown":
            break
    if background:
        status = "unknown"
    evidence = {}
    for name, pattern in (("ai", AI), ("government", GOVERNMENT), ("action", ACTION)):
        evidence[name] = [{"field": field, "text": value} for field, value in parts if pattern.search(value)][:2]
    evidence["category"] = [{"field": field, "text": value} for field, value in parts
                            if category and re.search(dict(CATEGORY_RULES)[category], value, re.I)][:2]
    return {**article, "included": not reasons, "classification_evidence": evidence,
            "category": category, "category_basis": category_basis, "category_matches": matches,
            "article_type": "background" if background else "event_candidate",
            "status": status, "classification_signals": signals,
            "exclusion_reasons": reasons, "review_status": "needs_review"}
