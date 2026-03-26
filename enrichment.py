"""
Prospect enrichment engine.
Processes the enrichment_queue table: for each pending prospect,
searches Google for LinkedIn URL and Instagram handle, then writes
results back to the enrichment_queue record.

Runs as a background job every 10 minutes via APScheduler.
All data sourced from public information only.
"""

import logging
import os
import re
import time

import requests

import db

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
MAX_ATTEMPTS = 3


def build_search_query(name: str, company: str) -> str:
    """Build a Google search query to find LinkedIn + Instagram profiles."""
    if company:
        return f'"{name}" "{company}" site:linkedin.com OR site:instagram.com'
    return f'"{name}" site:linkedin.com OR site:instagram.com'


def parse_google_result(text: str, name: str, company: str) -> dict:
    """
    Extract LinkedIn URL and Instagram handle from Google search result text.
    text: concatenated titles + snippets from search results.
    """
    result = {"linkedin_url": "", "instagram_handle": ""}

    linkedin_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[\w\-]+', text)
    if linkedin_match:
        result["linkedin_url"] = linkedin_match.group(0)

    # Instagram handle: @handle pattern or instagram.com/handle
    ig_match = re.search(r'\(@?([\w.]+)\)\s*[•·]\s*Instagram', text)
    if ig_match:
        result["instagram_handle"] = "@" + ig_match.group(1)
    else:
        ig_url = re.search(r'instagram\.com/([\w.]+)', text)
        if ig_url and ig_url.group(1) not in ("p", "reel", "explore", "accounts"):
            result["instagram_handle"] = "@" + ig_url.group(1)

    return result


def should_skip_enrichment(record: dict) -> bool:
    """Return True if this enrichment record should be skipped."""
    if record.get("attempts", 0) >= MAX_ATTEMPTS:
        return True
    if record.get("status") == "done":
        return True
    if record.get("linkedin_url"):
        return True
    return False


def _google_search(query: str) -> str:
    """Call Google Custom Search API. Returns concatenated titles+snippets or empty string."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        logger.debug("Google Search not configured — skipping enrichment")
        return ""
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": 5},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return " ".join(
            item.get("title", "") + " " + item.get("snippet", "")
            for item in items
        )
    except Exception as e:
        logger.warning("Google Search error: %s", e)
        return ""


def enrich_prospect(queue_record: dict) -> None:
    """Run enrichment for one prospect. Updates enrichment_queue with results."""
    prospect_id = queue_record["prospect_id"]

    # Get prospect details
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT name, company FROM prospects WHERE id=?", (prospect_id,)
        ).fetchone()
    if not row:
        logger.warning("Prospect %d not found, skipping enrichment", prospect_id)
        return

    name = row["name"]
    company = row.get("company", "") or ""

    query = build_search_query(name, company)
    text = _google_search(query)
    found = parse_google_result(text, name, company)

    with db.get_db() as conn:
        conn.execute("""
            UPDATE enrichment_queue
            SET attempts = attempts + 1,
                last_attempt = datetime('now'),
                linkedin_url = CASE WHEN ? != '' THEN ? ELSE linkedin_url END,
                instagram_handle = CASE WHEN ? != '' THEN ? ELSE instagram_handle END,
                status = CASE WHEN ? != '' THEN 'done' ELSE status END
            WHERE prospect_id = ?
        """, (
            found["linkedin_url"], found["linkedin_url"],
            found["instagram_handle"], found["instagram_handle"],
            found["linkedin_url"],
            prospect_id,
        ))


def process_enrichment_queue() -> None:
    """Process all pending items in the enrichment queue. Called by APScheduler."""
    try:
        with db.get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM enrichment_queue
                WHERE status != 'done' AND attempts < ?
            """, (MAX_ATTEMPTS,)).fetchall()

        for row in rows:
            record = dict(row)
            if should_skip_enrichment(record):
                continue
            try:
                enrich_prospect(record)
                time.sleep(1)  # Respect Google API rate limits
            except Exception as e:
                logger.error("Enrichment error for prospect %s: %s", record.get("prospect_id"), e)
    except Exception as e:
        logger.error("process_enrichment_queue failed: %s", e)
