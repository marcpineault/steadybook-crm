# Intelligence Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the enrichment engine, merge calm-money-bot's sophisticated intake pipeline into SteadyBook, and implement the Omniscient AI Assistant that continuously monitors all data sources and acts within the configured trust level.

**Architecture:** `enrichment.py` runs as an async background job processing the `enrichment_queue` table added in the Capture Layer plan. The calm-money-bot `IntakeClassifier` + `EntityResolver` + `ActionExecutor` replaces SteadyBook's simpler intake handlers for new channels. The `omniscient_agent.py` runs on APScheduler every 15 minutes, reads all channels via the database, synthesizes with GPT-4.1, and acts based on trust level.

**Tech Stack:** Python 3.13, OpenAI GPT-4.1, APScheduler (already in `scheduler.py`), SQLite via `db.py`, requests for Google Search.

**Dependency:** Requires Task 1 of the Capture Layer plan (DB schema) to be complete before starting Task 1 here.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `enrichment.py` | Create | Async prospect enrichment from Google + LinkedIn + Instagram |
| `intake_pipeline.py` | Create | Ported IntakeClassifier + EntityResolver + ActionExecutor from calm-money-bot |
| `omniscient_agent.py` | Create | Continuous AI assistant — reads all sources, synthesizes, acts |
| `scheduler.py` | Modify | Register enrichment job and omniscient agent on APScheduler |
| `tests/test_enrichment.py` | Create | Unit tests for enrichment parsing |
| `tests/test_intake_pipeline.py` | Create | Unit tests for pipeline classifier |
| `tests/test_omniscient_agent.py` | Create | Unit tests for agent synthesis |

---

## Task 1: Enrichment Engine

**Files:**
- Create: `enrichment.py`
- Create: `tests/test_enrichment.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_enrichment.py`:

```python
"""Tests for the prospect enrichment engine."""
import pytest
from unittest.mock import patch, MagicMock


def test_parse_google_result_extracts_linkedin():
    from enrichment import parse_google_result
    snippet = 'Sarah Chen - CFO at Maple Ridge Construction | LinkedIn\nhttps://linkedin.com/in/sarah-chen-cfo'
    result = parse_google_result(snippet, "Sarah Chen", "Maple Ridge Construction")
    assert "linkedin.com" in result.get("linkedin_url", "")


def test_parse_google_result_extracts_instagram():
    from enrichment import parse_google_result
    snippet = 'Sarah Chen (@sarahchen_cfo) • Instagram photos and videos'
    result = parse_google_result(snippet, "Sarah Chen", "")
    assert "instagram" in result.get("instagram_handle", "").lower() or result.get("instagram_handle") == "@sarahchen_cfo"


def test_parse_google_result_no_match_returns_empty():
    from enrichment import parse_google_result
    result = parse_google_result("nothing useful here", "Unknown Person", "")
    assert result.get("linkedin_url", "") == ""


def test_build_search_query_with_company():
    from enrichment import build_search_query
    q = build_search_query("Sarah Chen", "Maple Ridge Construction")
    assert "Sarah Chen" in q
    assert "Maple Ridge" in q
    assert "linkedin.com" in q


def test_build_search_query_without_company():
    from enrichment import build_search_query
    q = build_search_query("John Smith", "")
    assert "John Smith" in q


def test_should_skip_enrichment_maxed_attempts():
    from enrichment import should_skip_enrichment
    record = {"attempts": 5, "linkedin_url": ""}
    assert should_skip_enrichment(record) is True


def test_should_skip_enrichment_already_done():
    from enrichment import should_skip_enrichment
    record = {"attempts": 1, "linkedin_url": "https://linkedin.com/in/sarah", "status": "done"}
    assert should_skip_enrichment(record) is True


def test_should_skip_enrichment_pending():
    from enrichment import should_skip_enrichment
    record = {"attempts": 0, "linkedin_url": "", "status": "pending"}
    assert should_skip_enrichment(record) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_enrichment.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'enrichment'`

- [ ] **Step 3: Create `enrichment.py`**

```python
"""
Prospect enrichment engine.
Processes the enrichment_queue table: for each pending prospect,
searches Google for LinkedIn URL and Instagram handle, then writes
results back to the prospect record and re-triggers lead scoring.

Runs as a background job every 10 minutes via APScheduler.
All data sourced from public information only.
"""

import logging
import os
import re
import time
from urllib.parse import quote_plus

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

    instagram_match = re.search(r'@([\w.]+)\s*[•·]\s*Instagram', text)
    if instagram_match:
        result["instagram_handle"] = f"@{instagram_match.group(1)}"
    else:
        ig_url = re.search(r'instagram\.com/([\w.]+)', text)
        if ig_url and ig_url.group(1) not in ("p", "reel", "explore", "accounts"):
            result["instagram_handle"] = f"@{ig_url.group(1)}"

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
    """
    Run a Google Custom Search and return concatenated result text.
    Falls back to empty string if API not configured or request fails.
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        logger.debug("Google Search API not configured — skipping")
        return ""

    try:
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?key={GOOGLE_API_KEY}&cx={GOOGLE_CSE_ID}"
            f"&q={quote_plus(query)}&num=5"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        parts = []
        for item in items:
            parts.append(item.get("title", ""))
            parts.append(item.get("snippet", ""))
            parts.append(item.get("link", ""))
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"Google search failed: {e}")
        return ""


def enrich_prospect(prospect_id: int, prospect_name: str, company: str = "") -> dict:
    """
    Run enrichment for a single prospect.
    Returns dict of enriched fields written to DB.
    """
    query = build_search_query(prospect_name, company)
    search_text = _google_search(query)

    enriched = parse_google_result(search_text, prospect_name, company)

    updates = {}
    if enriched.get("linkedin_url"):
        updates["linkedin_url"] = enriched["linkedin_url"]
    if enriched.get("instagram_handle"):
        updates["instagram_handle"] = enriched["instagram_handle"]

    if updates:
        db.update_prospect(prospect_name, updates)
        logger.info(f"Enriched {prospect_name}: {updates}")

        # Re-score after enrichment
        try:
            import scoring
            scoring.update_score(prospect_name)
        except Exception:
            logger.debug("Scoring update skipped (module not available)")

    return enriched


def process_enrichment_queue() -> int:
    """
    Process all pending items in the enrichment_queue.
    Returns number of prospects processed.
    Called by APScheduler every 10 minutes.
    """
    processed = 0
    with db.get_db() as conn:
        pending = conn.execute("""
            SELECT eq.id, eq.prospect_id, eq.attempts, eq.linkedin_url, eq.status,
                   p.name, p.company
            FROM enrichment_queue eq
            JOIN prospects p ON p.id = eq.prospect_id
            WHERE eq.status = 'pending' AND eq.attempts < ?
            ORDER BY eq.created_at ASC
            LIMIT 20
        """, (MAX_ATTEMPTS,)).fetchall()

    for row in pending:
        record = dict(row)
        if should_skip_enrichment(record):
            continue

        prospect_id = record["prospect_id"]
        prospect_name = record["name"]
        company = record.get("company") or ""

        try:
            enriched = enrich_prospect(prospect_id, prospect_name, company)

            with db.get_db() as conn:
                new_status = "done" if enriched.get("linkedin_url") else "partial"
                conn.execute("""
                    UPDATE enrichment_queue
                    SET attempts = attempts + 1,
                        status = ?,
                        last_attempt = datetime('now'),
                        linkedin_url = ?,
                        instagram_handle = ?
                    WHERE id = ?
                """, (
                    new_status,
                    enriched.get("linkedin_url", ""),
                    enriched.get("instagram_handle", ""),
                    record["id"]
                ))
            processed += 1
            time.sleep(0.5)  # Rate limit

        except Exception:
            logger.exception(f"Enrichment failed for {prospect_name}")
            with db.get_db() as conn:
                conn.execute("""
                    UPDATE enrichment_queue
                    SET attempts = attempts + 1, last_attempt = datetime('now')
                    WHERE id = ?
                """, (record["id"],))

    if processed:
        logger.info(f"Enrichment: processed {processed} prospects")
    return processed
```

- [ ] **Step 4: Add Google Search API keys to `.env`**

```bash
echo "GOOGLE_SEARCH_API_KEY=" >> .env
echo "GOOGLE_CSE_ID=" >> .env
```

(Fill in values from Google Cloud Console → Custom Search API. Create a CSE at cse.google.com targeting linkedin.com and instagram.com.)

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_enrichment.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add enrichment.py tests/test_enrichment.py .env
git commit -m "feat: add enrichment engine with Google Search + LinkedIn/Instagram extraction"
```

---

## Task 2: Intake Pipeline (from calm-money-bot)

Port the sophisticated `IntakeClassifier` + `EntityResolver` + `ActionExecutor` from calm-money-bot into SteadyBook. This replaces ad-hoc processing for new channel types.

**Files:**
- Create: `intake_pipeline.py`
- Create: `tests/test_intake_pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intake_pipeline.py`:

```python
"""Tests for the structured intake pipeline."""
import pytest
from unittest.mock import patch, MagicMock


def test_intake_event_creation():
    from intake_pipeline import IntakeEvent
    event = IntakeEvent(
        source="instagram_dm",
        raw_data={"name": "Sarah", "message": "interested in group benefits"},
        raw_text="interested in group benefits"
    )
    assert event.source == "instagram_dm"
    assert event.raw_text == "interested in group benefits"


def test_entity_resolver_dedup_by_email():
    from intake_pipeline import EntityResolver
    import db

    with patch.object(db, 'get_prospect_by_email', return_value={"id": 1, "name": "Sarah Chen", "email": "sarah@test.com"}):
        with patch.object(db, 'get_prospect_by_phone', return_value=None):
            resolver = EntityResolver()
            result = resolver.resolve("Sarah Chen", "sarah@test.com", "")
            assert result["id"] == 1
            assert result["name"] == "Sarah Chen"


def test_entity_resolver_returns_none_for_unknown():
    from intake_pipeline import EntityResolver
    import db

    with patch.object(db, 'get_prospect_by_email', return_value=None):
        with patch.object(db, 'get_prospect_by_phone', return_value=None):
            with patch.object(db, 'get_prospect_by_name', return_value=None):
                resolver = EntityResolver()
                result = resolver.resolve("Brand New Person", "", "")
                assert result is None


def test_classify_intent_instagram_lead():
    from intake_pipeline import classify_intent
    data = {
        "source": "instagram_dm",
        "message": "Hi I saw your post about life insurance, I'm interested"
    }
    intent = classify_intent(data)
    assert intent in ("lead", "inquiry", "general")


def test_classify_intent_calendar_booking():
    from intake_pipeline import classify_intent
    data = {
        "source": "calendly",
        "name": "Sarah Chen",
        "meeting_datetime": "2026-04-01T14:00:00"
    }
    intent = classify_intent(data)
    assert intent == "booking"


def test_classify_intent_ad_form():
    from intake_pipeline import classify_intent
    data = {
        "source": "linkedin_ad",
        "name": "John Park",
        "email": "john@park.ca",
        "campaign": "group-benefits"
    }
    intent = classify_intent(data)
    assert intent == "lead"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_intake_pipeline.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'intake_pipeline'`

- [ ] **Step 3: Create `intake_pipeline.py`**

```python
"""
Structured intake pipeline for all social/external channels.
Ported and adapted from calm-money-bot/intake_pipeline.py.

Flow:
  IntakeEvent → classify_intent → EntityResolver → ActionExecutor
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import db

logger = logging.getLogger(__name__)

# Intent types
INTENT_LEAD = "lead"
INTENT_BOOKING = "booking"
INTENT_INQUIRY = "inquiry"
INTENT_GENERAL = "general"
INTENT_NOISE = "noise"

AD_SOURCES = {"instagram_ad", "linkedin_ad"}
BOOKING_SOURCES = {"calendly", "cal_com", "google_calendar", "outlook_calendar"}
MESSAGE_SOURCES = {"instagram_dm", "whatsapp", "gmail", "outlook"}

LEAD_KEYWORDS = {
    "insurance", "life insurance", "disability", "group benefits", "critical illness",
    "investments", "wealth", "retirement", "mortgage", "policy", "coverage",
    "quote", "interested", "looking for", "need help", "advisor", "financial plan"
}

NOISE_KEYWORDS = {
    "spam", "unsubscribe", "stop", "remove me", "do not contact"
}


@dataclass
class IntakeEvent:
    source: str
    raw_data: dict
    raw_text: str = ""
    intent: str = ""
    prospect_id: Optional[int] = None
    prospect_name: str = ""


def classify_intent(data: dict) -> str:
    """
    Classify the intent of an incoming event without calling the LLM.
    Uses rule-based classification for speed and cost efficiency.
    """
    source = data.get("source", "")

    if source in AD_SOURCES:
        return INTENT_LEAD

    if source in BOOKING_SOURCES:
        return INTENT_BOOKING

    if source in MESSAGE_SOURCES:
        text = (
            data.get("message") or
            data.get("body") or
            data.get("snippet") or ""
        ).lower()

        if any(kw in text for kw in NOISE_KEYWORDS):
            return INTENT_NOISE

        if any(kw in text for kw in LEAD_KEYWORDS):
            return INTENT_LEAD

        return INTENT_INQUIRY

    return INTENT_GENERAL


class EntityResolver:
    """Resolves incoming contact data to existing prospect records."""

    def resolve(self, name: str, email: str, phone: str) -> Optional[dict]:
        """
        Find existing prospect by email → phone → name.
        Returns prospect dict or None if not found.
        """
        if email:
            existing = db.get_prospect_by_email(email)
            if existing:
                return existing

        if phone:
            existing = db.get_prospect_by_phone(phone)
            if existing:
                return existing

        if name:
            existing = db.get_prospect_by_name(name)
            if existing:
                return existing

        return None


class ActionExecutor:
    """Executes deterministic DB writes based on classified intent."""

    def __init__(self):
        self.resolver = EntityResolver()

    def execute(self, event: IntakeEvent) -> dict:
        """
        Process a classified intake event.
        Returns dict with prospect_id, prospect_name, action_taken.
        """
        data = event.raw_data
        source = event.source
        intent = event.intent

        name = (data.get("name") or "").strip()
        email = (data.get("email") or data.get("from_email") or "").strip()
        phone = (data.get("phone") or "").strip()

        if not name and not email:
            return {"action_taken": "skipped_no_identity"}

        existing = self.resolver.resolve(name, email, phone)

        if intent == INTENT_NOISE:
            if existing:
                db.apply_tag(existing["id"], "do_not_contact")
            return {"action_taken": "tagged_do_not_contact"}

        if intent == INTENT_BOOKING:
            return self._handle_booking(data, source, existing, name, email, phone)

        if intent in (INTENT_LEAD, INTENT_INQUIRY):
            return self._handle_lead(data, source, existing, name, email, phone, intent)

        return {"action_taken": "no_action"}

    def _handle_lead(self, data, source, existing, name, email, phone, intent):
        from intake import _score_and_schedule

        message = data.get("message") or data.get("body") or data.get("snippet") or ""
        company = data.get("company") or ""
        title = data.get("title") or ""
        campaign = data.get("campaign") or ""

        notes_parts = [f"[{source}]"]
        if campaign:
            notes_parts.append(f"Campaign: {campaign}")
        if title and company:
            notes_parts.append(f"{title} at {company}")
        if message:
            notes_parts.append(message[:200])
        notes = " | ".join(notes_parts)

        if existing:
            db.update_prospect(existing["name"], {
                "notes": f"{existing.get('notes', '')} | {notes}".strip(" |")
            })
            pid = existing["id"]
            prospect_name = existing["name"]
        else:
            db.add_prospect({
                "name": name, "phone": phone, "email": email,
                "source": source, "priority": "Warm",
                "stage": "New Lead", "product": "", "notes": notes,
            })
            prospect = db.get_prospect_by_name(name)
            pid = prospect["id"]
            prospect_name = name
            _score_and_schedule(name)

        db.apply_tag(pid, f"source_{source}")
        db.apply_tag(pid, "new_lead")
        if campaign:
            db.apply_tag(pid, f"campaign_{campaign[:30].replace(' ', '_').lower()}")
        db.queue_enrichment(pid)

        db.add_interaction({
            "prospect": prospect_name,
            "source": source,
            "raw_text": message,
            "summary": f"Inbound {intent} from {source}",
            "action_items": "",
        })

        return {
            "action_taken": f"created_or_updated_{intent}",
            "prospect_id": pid,
            "prospect_name": prospect_name,
        }

    def _handle_booking(self, data, source, existing, name, email, phone):
        from intake import _score_and_schedule

        meeting_datetime = data.get("meeting_datetime") or ""
        meeting_type = data.get("meeting_type") or "Consultation"
        notes = f"[{source}] Booked: {meeting_type}"

        if existing:
            db.update_prospect(existing["name"], {
                "notes": f"{existing.get('notes', '')} | {notes}".strip(" |")
            })
            pid = existing["id"]
            prospect_name = existing["name"]
        else:
            db.add_prospect({
                "name": name, "phone": phone, "email": email,
                "source": source, "priority": "Warm",
                "stage": "New Lead", "product": "", "notes": notes,
            })
            prospect = db.get_prospect_by_name(name)
            pid = prospect["id"]
            prospect_name = name
            _score_and_schedule(name)

        db.apply_tag(pid, "meeting_booked")
        db.apply_tag(pid, "new_lead")
        db.queue_enrichment(pid)

        meeting_date = meeting_time = ""
        if meeting_datetime:
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(meeting_datetime.replace("Z", "+00:00"))
                meeting_date = dt.strftime("%Y-%m-%d")
                meeting_time = dt.strftime("%H:%M")
            except ValueError:
                pass

        db.add_meeting({
            "date": meeting_date,
            "time": meeting_time,
            "prospect": prospect_name,
            "type": meeting_type,
            "prep_notes": f"Booked via {source}",
        })

        return {
            "action_taken": "created_booking",
            "prospect_id": pid,
            "prospect_name": prospect_name,
        }


def process_intake_event(data: dict) -> dict:
    """
    Top-level entry point. Takes raw n8n payload dict, returns result dict.
    Used by social_intake.py as an optional enriched processing path.
    """
    source = data.get("source", "unknown")
    event = IntakeEvent(
        source=source,
        raw_data=data,
        raw_text=data.get("message") or data.get("body") or "",
    )
    event.intent = classify_intent(data)

    if event.intent == INTENT_NOISE:
        logger.info(f"Noise detected from {source} — skipping")
        return {"action_taken": "noise_filtered"}

    executor = ActionExecutor()
    result = executor.execute(event)
    return result
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_intake_pipeline.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add intake_pipeline.py tests/test_intake_pipeline.py
git commit -m "feat: port intake pipeline (IntakeClassifier + EntityResolver + ActionExecutor) from calm-money-bot"
```

---

## Task 3: Omniscient AI Assistant

**Files:**
- Create: `omniscient_agent.py`
- Modify: `scheduler.py`
- Create: `tests/test_omniscient_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_omniscient_agent.py`:

```python
"""Tests for the omniscient AI assistant."""
import pytest
from unittest.mock import patch, MagicMock


def test_build_prospect_context_includes_tags():
    from omniscient_agent import build_prospect_context
    prospect = {
        "id": 1, "name": "Sarah Chen", "stage": "Proposal",
        "product": "Group Benefits", "notes": "Interested in 35-person team coverage",
        "lead_score": 78, "last_contact": "2026-03-10", "company": "Maple Ridge"
    }
    tags = ["source_instagram_dm", "meeting_booked", "interest_group_benefits"]
    context = build_prospect_context(prospect, tags, recent_activities=[])
    assert "Sarah Chen" in context
    assert "meeting_booked" in context
    assert "Group Benefits" in context


def test_build_prospect_context_includes_activities():
    from omniscient_agent import build_prospect_context
    prospect = {"id": 1, "name": "John Park", "stage": "New Lead",
                "product": "", "notes": "", "lead_score": 45, "company": ""}
    activities = [
        {"action": "Email sent", "outcome": "Re: Life Insurance", "created_at": "2026-03-20"},
        {"action": "Voice note", "outcome": "Interested in disability", "created_at": "2026-03-22"},
    ]
    context = build_prospect_context(prospect, [], activities)
    assert "Email sent" in context
    assert "disability" in context


def test_get_trust_level_defaults_to_1():
    from omniscient_agent import get_trust_level
    with patch("db.get_trust_level", return_value=None):
        level = get_trust_level()
        assert level == 1


def test_get_trust_level_reads_from_db():
    from omniscient_agent import get_trust_level
    with patch("db.get_trust_level", return_value=2):
        assert get_trust_level() == 2


def test_format_alert_message():
    from omniscient_agent import format_alert_message
    msg = format_alert_message(
        prospect_name="Sarah Chen",
        insight="Her policy renews in 28 days and last contact was 40 days ago",
        suggested_action="Send renewal prep email",
        priority="high"
    )
    assert "Sarah Chen" in msg
    assert "28 days" in msg
    assert "renewal" in msg.lower()


def test_should_alert_high_priority():
    from omniscient_agent import should_alert
    assert should_alert(priority="high", trust_level=1) is True
    assert should_alert(priority="high", trust_level=3) is True


def test_should_alert_low_priority_high_trust():
    from omniscient_agent import should_alert
    # At trust level 3, low priority items are handled autonomously — no alert needed
    assert should_alert(priority="low", trust_level=3) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_omniscient_agent.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'omniscient_agent'`

- [ ] **Step 3: Create `omniscient_agent.py`**

```python
"""
Omniscient AI Assistant.
Runs every 15 minutes via APScheduler.
Reads all prospect/client data + recent activities across all channels,
synthesizes cross-channel signals with GPT-4.1, and acts based on trust level.

Trust levels:
  1 = Draft only — queues all actions for advisor approval
  2 = Routine tasks auto — sends pre-approved templates, moves stages, creates tasks
  3 = Full autonomy — handles everything, alerts advisor only for high-stakes decisions
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import db

logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STALE_DAYS = 14        # Days without contact before flagging
RENEWAL_WARN_DAYS = 90 # Days before renewal to start flagging
MAX_PROSPECTS_PER_RUN = 30


def get_trust_level() -> int:
    """Get current trust level from DB. Defaults to 1 (draft only)."""
    try:
        level = db.get_trust_level()
        if level and isinstance(level, int) and 1 <= level <= 3:
            return level
    except Exception:
        pass
    return 1


def build_prospect_context(prospect: dict, tags: list[str], recent_activities: list[dict]) -> str:
    """Build a text context block for a single prospect for GPT synthesis."""
    lines = [
        f"Prospect: {prospect.get('name')}",
        f"Stage: {prospect.get('stage')} | Score: {prospect.get('lead_score') or 'N/A'}",
        f"Product: {prospect.get('product') or 'Unknown'}",
        f"Company: {prospect.get('company') or 'N/A'}",
        f"Last contact: {prospect.get('last_contact') or 'Never'}",
        f"Tags: {', '.join(tags) if tags else 'None'}",
        f"Notes: {(prospect.get('notes') or '')[:300]}",
    ]

    if recent_activities:
        lines.append("Recent activity:")
        for act in recent_activities[:5]:
            date = act.get("created_at", "")[:10]
            lines.append(f"  [{date}] {act.get('action')}: {act.get('outcome', '')[:100]}")

    return "\n".join(lines)


def format_alert_message(prospect_name: str, insight: str,
                         suggested_action: str, priority: str) -> str:
    """Format a Telegram alert message for the advisor."""
    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
    return (
        f"{priority_emoji} {prospect_name}\n"
        f"{insight}\n\n"
        f"Suggested: {suggested_action}"
    )


def should_alert(priority: str, trust_level: int) -> bool:
    """
    Determine whether to send a Telegram alert.
    At trust level 3, only high-priority items surface to the advisor.
    """
    if priority == "high":
        return True
    if priority == "medium" and trust_level < 3:
        return True
    if priority == "low" and trust_level == 1:
        return True
    return False


def _get_stale_prospects() -> list[dict]:
    """Get prospects with no contact in STALE_DAYS days."""
    cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT p.*,
                   MAX(a.created_at) as last_activity
            FROM prospects p
            LEFT JOIN activities a ON a.prospect = p.name
            WHERE p.stage NOT IN ('Closed Won', 'Closed Lost')
            GROUP BY p.id
            HAVING last_activity IS NULL OR last_activity < ?
            ORDER BY p.lead_score DESC
            LIMIT ?
        """, (cutoff, MAX_PROSPECTS_PER_RUN)).fetchall()
        return [dict(r) for r in rows]


def _get_upcoming_renewals() -> list[dict]:
    """Get insurance policies renewing within RENEWAL_WARN_DAYS days."""
    cutoff = (datetime.now() + timedelta(days=RENEWAL_WARN_DAYS)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT ib.*, p.lead_score, p.stage
            FROM insurance_book ib
            JOIN prospects p ON p.name = ib.client_name
            WHERE ib.renewal_date BETWEEN ? AND ?
            AND ib.status = 'Active'
            ORDER BY ib.renewal_date ASC
            LIMIT 20
        """, (today, cutoff)).fetchall()
        return [dict(r) for r in rows]


def _synthesize_with_gpt(context_block: str, prospect_name: str) -> dict:
    """
    Ask GPT-4.1 to analyze a prospect's full context and recommend action.
    Returns dict with keys: insight, suggested_action, priority, draft_message.
    """
    system = """You are an AI assistant for a Canadian insurance and financial services advisor.

Analyze the prospect context below and identify the most important action to take RIGHT NOW.

Return a JSON object:
{
  "insight": "1-2 sentence explanation of what you noticed and why it matters",
  "suggested_action": "Specific action the advisor should take (e.g. 'Call to discuss renewal', 'Send disability quote')",
  "priority": "high | medium | low",
  "draft_message": "A ready-to-send SMS or email draft (or empty string if no message needed)"
}

Rules:
- Be specific. Name the prospect, the product, the timing.
- Draft messages should be concise, warm, and in plain Canadian English.
- Only set priority=high if there is genuine urgency (renewal <30 days, hot lead gone silent >7 days, etc.)
- Return ONLY the JSON object, no other text."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=400,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": context_block},
            ],
        )
        import json
        raw = resp.choices[0].message.content or ""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"GPT synthesis failed for {prospect_name}: {e}")
        return {
            "insight": "",
            "suggested_action": "",
            "priority": "low",
            "draft_message": ""
        }


def _queue_draft(prospect_name: str, draft_message: str, channel: str = "sms") -> None:
    """Queue an AI-drafted message for advisor approval."""
    try:
        import approval_queue as aq
        aq.add_to_queue({
            "prospect": prospect_name,
            "channel": channel,
            "message": draft_message,
            "generated_by": "omniscient_agent",
        })
    except Exception as e:
        logger.warning(f"Failed to queue draft for {prospect_name}: {e}")


def _send_telegram_alert(message: str) -> None:
    """Send an alert to the advisor via Telegram."""
    if not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")


def run_omniscient_cycle() -> dict:
    """
    Main cycle — runs every 15 minutes.
    Returns summary dict with counts.
    """
    trust_level = get_trust_level()
    alerts_sent = 0
    drafts_queued = 0
    prospects_analyzed = 0

    # 1. Check stale prospects
    stale = _get_stale_prospects()
    for prospect in stale[:10]:  # Cap per cycle to avoid GPT overuse
        pid = prospect["id"]
        name = prospect["name"]
        tags = db.get_tags(pid)

        # Skip do-not-contact
        if "do_not_contact" in tags:
            continue

        with db.get_db() as conn:
            activities = conn.execute("""
                SELECT action, outcome, created_at FROM activities
                WHERE prospect = ? ORDER BY created_at DESC LIMIT 5
            """, (name,)).fetchall()
            activities = [dict(a) for a in activities]

        context = build_prospect_context(prospect, tags, activities)
        result = _synthesize_with_gpt(context, name)
        prospects_analyzed += 1

        if result.get("draft_message") and trust_level >= 2:
            _queue_draft(name, result["draft_message"])
            drafts_queued += 1

        if result.get("insight") and should_alert(result.get("priority", "low"), trust_level):
            msg = format_alert_message(
                name,
                result["insight"],
                result["suggested_action"],
                result["priority"]
            )
            _send_telegram_alert(msg)
            alerts_sent += 1

    # 2. Check upcoming renewals
    renewals = _get_upcoming_renewals()
    for renewal in renewals:
        client_name = renewal.get("client_name", "")
        renewal_date = renewal.get("renewal_date", "")
        product = renewal.get("product_type", "Policy")

        prospect = db.get_prospect_by_name(client_name)
        if not prospect:
            continue

        tags = db.get_tags(prospect["id"])
        if "do_not_contact" in tags:
            continue

        days_until = (
            datetime.strptime(renewal_date, "%Y-%m-%d") - datetime.now()
        ).days

        priority = "high" if days_until <= 30 else "medium"
        insight = f"{product} renewal in {days_until} days (renews {renewal_date})"
        action = f"Contact {client_name} to review and renew their {product}"

        if should_alert(priority, trust_level):
            msg = format_alert_message(client_name, insight, action, priority)
            _send_telegram_alert(msg)
            alerts_sent += 1

        # Apply renewal tag for automation engine to pick up
        db.apply_tag(prospect["id"], f"policy_renewal_{min(days_until, 90)}")

    summary = {
        "prospects_analyzed": prospects_analyzed,
        "alerts_sent": alerts_sent,
        "drafts_queued": drafts_queued,
        "trust_level": trust_level,
        "timestamp": datetime.now().isoformat(),
    }
    logger.info(f"Omniscient cycle complete: {summary}")
    return summary
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_omniscient_agent.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Register omniscient agent and enrichment in `scheduler.py`**

Open `scheduler.py`. Find where APScheduler jobs are registered. Add:

```python
from enrichment import process_enrichment_queue
from omniscient_agent import run_omniscient_cycle

# Add inside the scheduler setup function, alongside existing jobs:
scheduler.add_job(
    process_enrichment_queue,
    "interval",
    minutes=10,
    id="enrichment_queue",
    replace_existing=True,
)

scheduler.add_job(
    run_omniscient_cycle,
    "interval",
    minutes=15,
    id="omniscient_agent",
    replace_existing=True,
)
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add omniscient_agent.py scheduler.py tests/test_omniscient_agent.py
git commit -m "feat: add omniscient AI assistant — continuous monitoring, cross-channel synthesis, trust-level action"
```

---

## Final Verification

- [ ] `python -m pytest tests/ -q 2>&1 | tail -5` — all pass
- [ ] `python -c "from enrichment import process_enrichment_queue; print('OK')"`
- [ ] `python -c "from intake_pipeline import process_intake_event; print('OK')"`
- [ ] `python -c "from omniscient_agent import run_omniscient_cycle; print('OK')"`
- [ ] `python -c "import scheduler; print('Scheduler imports OK')"` (no errors)
