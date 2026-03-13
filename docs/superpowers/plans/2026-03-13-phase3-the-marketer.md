# Phase 3: "The Marketer" Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a content generation engine that produces social media posts in Marc's voice, driven by a brand voice library, market intelligence calendar, and weekly content planning workflow.

**Architecture:** Two new modules (`content_engine.py`, `market_intel.py`) handle content generation and market context. Brand voice examples are stored in a new `brand_voice` DB table and included as few-shot examples in content prompts. A weekly scheduler job generates a 5-post content plan, queued via the existing `approval_queue` for Marc's review. Market intelligence (BoC rates, tax deadlines, seasonal topics) is pre-loaded in a `market_calendar` table and surfaced in morning briefings.

**Tech Stack:** Python 3.13, OpenAI GPT-4.1 (content generation), GPT-4.1-mini (extraction), python-telegram-bot 21.10 (inline keyboards), APScheduler 3.10.4, SQLite WAL mode.

**Important codebase notes:**
- `db.py` uses `conn.executescript()` for schema in `init_db()` — append new tables there
- OpenAI calls use `max_completion_tokens` (NOT `max_tokens`)
- Use `.replace()` for prompt templating (NOT `.format()`) to avoid crashes on user data with curly braces — EXCEPTION: `briefing.py` uses `.format()` with `_escape_braces()` helper; follow that pattern when modifying briefing.py
- Bot uses `ADMIN_CHAT_ID` module-level constant and `_is_admin()` / `_require_admin()` helpers
- `approval_queue.py` has: `add_draft()`, `get_pending_drafts()`, `get_draft_by_id()`, `update_draft_status()`, `set_telegram_message_id()`, `get_pending_count()`
- `compliance.py` has: `check_compliance()`, `log_action()`, `get_audit_log()`, `update_audit_outcome()`
- Scheduler uses `_bot` global, `CHAT_ID` env var, timezone `ET = pytz.timezone("America/Toronto")`
- Existing inline keyboard pattern: `_draft_keyboard(queue_id)` + `send_draft_to_telegram()` + `handle_draft_callback()` in `bot.py`
- `follow_up.py` and `meeting_prep.py` are the established patterns for content generation modules

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `content_engine.py` | Generate social media posts in Marc's voice using brand voice examples |
| Create | `market_intel.py` | Pre-loaded market calendar, seasonal relevance, content angle suggestions |
| Create | `tests/test_content_engine.py` | Tests for content generation module |
| Create | `tests/test_market_intel.py` | Tests for market intelligence module |
| Modify | `db.py` | Add `brand_voice` and `market_calendar` tables |
| Modify | `bot.py` | Add `/voice`, `/content`, `/calendar` commands |
| Modify | `scheduler.py` | Add weekly content calendar job (Sunday 6PM) and daily market check |
| Modify | `briefing.py` | Include market intelligence in morning briefing |

---

## Chunk 1: Database Schema + Brand Voice

### Task 1: Database Schema — brand_voice and market_calendar Tables

**Files:**
- Modify: `db.py` (inside `init_db()` executescript block, around line 217)
- Create: `tests/test_content_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_content_schema.py`:

```python
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_content_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_brand_voice_table_exists():
    with db.get_db() as conn:
        conn.execute("SELECT id, platform, content, post_type, created_at FROM brand_voice LIMIT 1")


def test_market_calendar_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, event_type, title, date, description, relevance_products, recurring FROM market_calendar LIMIT 1"
        )


def test_brand_voice_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Great chat with a young couple about protecting their growing family.", "story"),
        )
        rows = conn.execute("SELECT * FROM brand_voice WHERE platform = 'linkedin'").fetchall()
        assert len(rows) == 1
        assert rows[0]["post_type"] == "story"


def test_market_calendar_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("rate_decision", "BoC Rate Decision", "2026-04-15", "Bank of Canada interest rate announcement", "Home Insurance,Wealth Management", 0),
        )
        rows = conn.execute("SELECT * FROM market_calendar WHERE event_type = 'rate_decision'").fetchall()
        assert len(rows) == 1
        assert "BoC" in rows[0]["title"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_content_schema.py -v`
Expected: FAIL — tables don't exist

- [ ] **Step 3: Add tables to db.py**

In `db.py`, inside the `init_db()` function's `conn.executescript(...)` block, append after the `audit_log` table (around line 217):

```sql
        CREATE TABLE IF NOT EXISTS brand_voice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'linkedin',
            content TEXT NOT NULL,
            post_type TEXT NOT NULL DEFAULT 'general',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS market_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT DEFAULT '',
            relevance_products TEXT DEFAULT '',
            recurring INTEGER DEFAULT 0
        );
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_content_schema.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_content_schema.py
git commit -m "feat: add brand_voice and market_calendar tables for Phase 3"
```

---

### Task 2: Market Intelligence Module — Pre-loaded Calendar + Relevance

**Files:**
- Create: `market_intel.py`
- Create: `tests/test_market_intel.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_intel.py`:

```python
import os
import sys
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_market_intel"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import market_intel


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_seed_default_calendar():
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    assert count > 0


def test_seed_idempotent():
    market_intel.seed_default_calendar()
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    # Should not double-insert
    first_count = count
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count2 = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    assert count2 == first_count


def test_get_upcoming_events():
    market_intel.seed_default_calendar()
    # Get events in the next 30 days
    events = market_intel.get_upcoming_events(days_ahead=365)
    assert isinstance(events, list)
    # Should have at least some events in a full year window
    assert len(events) > 0


def test_get_upcoming_events_empty_range():
    # No events seeded
    events = market_intel.get_upcoming_events(days_ahead=7)
    assert events == []


def test_get_seasonal_context():
    ctx = market_intel.get_seasonal_context()
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_get_content_angles():
    market_intel.seed_default_calendar()
    angles = market_intel.get_content_angles(days_ahead=365)
    assert isinstance(angles, list)


def test_add_custom_event():
    market_intel.add_event(
        event_type="product_update",
        title="New disability product launch",
        date="2026-04-01",
        description="Co-operators launching enhanced disability coverage",
        relevance_products="Disability Insurance",
    )
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM market_calendar WHERE event_type = 'product_update'").fetchall()
    assert len(rows) == 1
    assert "disability" in rows[0]["title"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_market_intel.py -v`
Expected: FAIL — `market_intel` module doesn't exist

- [ ] **Step 3: Implement market_intel.py**

Create `market_intel.py`:

```python
"""Market intelligence module — pre-loaded calendars and seasonal context.

Provides market event awareness for content generation and morning briefings:
- Bank of Canada rate decision dates
- Tax deadline calendar (RRSP, TFSA, filing)
- Seasonal financial planning topics
- Custom events (product updates, local news)
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# Pre-loaded calendar events (static, published annually)
DEFAULT_EVENTS = [
    # Bank of Canada rate decisions (8 per year, 2026 dates)
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-01-29", "description": "Bank of Canada interest rate announcement — impacts mortgage and investment conversations", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-03-12", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-04-15", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-06-03", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-07-15", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-09-09", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-10-28", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-12-09", "description": "Bank of Canada interest rate announcement", "relevance_products": "Home Insurance,Wealth Management", "recurring": 0},

    # Tax deadlines
    {"event_type": "tax_deadline", "title": "RRSP Contribution Deadline", "date": "2026-03-02", "description": "Last day to contribute to RRSP for 2025 tax year", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "tax_deadline", "title": "Tax Filing Deadline", "date": "2026-04-30", "description": "Personal income tax filing deadline", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "tax_deadline", "title": "TFSA Contribution Room Resets", "date": "2026-01-01", "description": "New TFSA contribution room available for 2026", "relevance_products": "Wealth Management", "recurring": 1},

    # Seasonal topics
    {"event_type": "seasonal", "title": "RRSP Season Starts", "date": "2026-01-15", "description": "Prime time for retirement savings conversations — RRSP season runs Jan-Mar", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Tax Season Starts", "date": "2026-03-15", "description": "Tax preparation season — good time for financial review conversations", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Spring Home Insurance Reviews", "date": "2026-04-01", "description": "Spring season — homeowners reviewing coverage after winter", "relevance_products": "Home Insurance", "recurring": 1},
    {"event_type": "seasonal", "title": "Back-to-School Life Insurance", "date": "2026-08-15", "description": "Back-to-school season — families thinking about protection and education savings", "relevance_products": "Life Insurance,Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Year-End Financial Planning", "date": "2026-10-15", "description": "Year-end planning season — tax optimization, RRSP top-ups, coverage reviews", "relevance_products": "Wealth Management,Life Insurance", "recurring": 1},
    {"event_type": "seasonal", "title": "Winter Driving Safety", "date": "2026-11-15", "description": "Winter tire season — good time for auto insurance conversations", "relevance_products": "Auto Insurance", "recurring": 1},
]

# Seasonal context by month (always available, no DB needed)
SEASONAL_CONTEXT = {
    1: "RRSP season (Jan-Mar). New year financial resolutions. TFSA room reset.",
    2: "RRSP season continues. Valentine's — couples financial planning angle.",
    3: "RRSP deadline approaching. Tax season starting. Spring forward.",
    4: "Tax filing deadline Apr 30. Spring home insurance reviews. Moving season starts.",
    5: "Post-tax season. Summer planning. Home & auto coverage reviews.",
    6: "Summer travel insurance. Mid-year financial check-ups. New grads entering workforce.",
    7: "Summer vacations. Travel insurance. Mid-year portfolio reviews.",
    8: "Back-to-school. Life insurance for families. Education savings (RESP).",
    9: "Fall renewal season. Back to routine. Business insurance reviews.",
    10: "Year-end planning starts. RRSP catch-up. Coverage gap reviews.",
    11: "Winter tire season. Auto insurance. Year-end tax moves.",
    12: "Year-end wrap-up. Holiday insurance considerations. New year planning preview.",
}


def seed_default_calendar():
    """Seed the market_calendar table with pre-loaded events. Idempotent."""
    with db.get_db() as conn:
        for event in DEFAULT_EVENTS:
            existing = conn.execute(
                "SELECT id FROM market_calendar WHERE title = ? AND date = ?",
                (event["title"], event["date"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (event["event_type"], event["title"], event["date"], event["description"], event["relevance_products"], event["recurring"]),
                )


def get_upcoming_events(days_ahead=14):
    """Get market calendar events in the next N days."""
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_calendar WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (today, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def get_seasonal_context():
    """Get the current month's seasonal context string."""
    month = datetime.now().month
    return SEASONAL_CONTEXT.get(month, "No specific seasonal context.")


def get_content_angles(days_ahead=14):
    """Get content angle suggestions based on upcoming events and seasonal context.

    Returns list of dicts with: topic, angle, relevance, source.
    """
    angles = []

    # Upcoming events
    events = get_upcoming_events(days_ahead=days_ahead)
    for event in events:
        angles.append({
            "topic": event["title"],
            "angle": event["description"],
            "relevance": event.get("relevance_products", ""),
            "source": f"market_calendar ({event['event_type']})",
        })

    # Seasonal context
    seasonal = get_seasonal_context()
    if seasonal:
        angles.append({
            "topic": "Seasonal relevance",
            "angle": seasonal,
            "relevance": "all",
            "source": "seasonal_calendar",
        })

    return angles


def add_event(event_type, title, date, description="", relevance_products=""):
    """Add a custom event to the market calendar."""
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (event_type, title, date, description, relevance_products),
        )
    logger.info("Added market event: %s on %s", title, date)


def format_for_briefing(days_ahead=7):
    """Format upcoming market events for inclusion in the morning briefing.

    Includes prospect relevance — which prospects each event is relevant to.
    Returns a string summary, or empty string if no events.
    """
    events = get_upcoming_events(days_ahead=days_ahead)
    if not events:
        return ""

    # Cross-reference events with prospect pipeline for relevance
    try:
        prospects = db.read_prospects()
        active_prospects = [p for p in prospects if p.get("stage") not in ("Closed Won", "Closed Lost", "")]
    except Exception:
        active_prospects = []

    lines = ["UPCOMING MARKET EVENTS:"]
    for event in events[:5]:
        days_until = (datetime.strptime(event["date"], "%Y-%m-%d") - datetime.now()).days
        timing = f"in {days_until} days" if days_until > 1 else ("tomorrow" if days_until == 1 else "today")
        lines.append(f"  - {event['title']} ({timing}): {event['description'][:100]}")

        # Find relevant prospects by matching product interest
        relevance_products = [p.strip().lower() for p in (event.get("relevance_products") or "").split(",") if p.strip()]
        if relevance_products and active_prospects:
            relevant_names = []
            for p in active_prospects:
                prospect_product = (p.get("product") or "").lower()
                if any(rp in prospect_product for rp in relevance_products):
                    relevant_names.append(p["name"])
            if relevant_names:
                lines.append(f"    Relevant prospects: {', '.join(relevant_names[:5])}")

    seasonal = get_seasonal_context()
    if seasonal:
        lines.append(f"  Season: {seasonal}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_market_intel.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add market_intel.py tests/test_market_intel.py
git commit -m "feat: add market intelligence module with pre-loaded calendar and seasonal context"
```

---

## Chunk 2: Content Generation Engine

### Task 3: Content Engine — Core Module

**Files:**
- Create: `content_engine.py`
- Create: `tests/test_content_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_content_engine.py`:

```python
import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_content_engine"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import content_engine


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_brand_voice():
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Had a great chat with a young couple about protecting their growing family. Life insurance isn't exciting, but knowing your kids are covered? That's peace of mind.", "story"),
        )
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Quick tip: Review your home insurance annually. Renovations, new furniture, even a home office setup can change what you need covered.", "educational"),
        )


def test_get_brand_voice_examples():
    _seed_brand_voice()
    examples = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples) == 2
    assert all("content" in e for e in examples)


def test_get_brand_voice_examples_empty():
    examples = content_engine.get_brand_voice_examples(platform="instagram")
    assert examples == []


def test_add_brand_voice_example():
    content_engine.add_brand_voice_example(
        platform="linkedin",
        content="Disability insurance isn't just for physical jobs.",
        post_type="educational",
    )
    examples = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples) == 1


@patch("content_engine.openai_client")
def test_generate_post(mock_client):
    _seed_brand_voice()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Just had a great conversation about life insurance with a growing family in Byron. The look of relief when they realize coverage is more affordable than they thought? That's why I do this."
    mock_client.chat.completions.create.return_value = mock_response

    post = content_engine.generate_post(
        platform="linkedin",
        post_type="story",
        topic="Life insurance for young families",
        context="Spring season, several young family prospects in pipeline",
    )
    assert post is not None
    assert "content" in post
    assert len(post["content"]) > 20


@patch("content_engine.openai_client")
def test_generate_post_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    post = content_engine.generate_post(
        platform="linkedin",
        post_type="educational",
        topic="RRSP tips",
        context="RRSP season",
    )
    assert post is None


@patch("content_engine.openai_client")
def test_generate_weekly_plan(mock_client):
    _seed_brand_voice()

    plan_json = json.dumps([
        {"day": "Monday", "platform": "linkedin", "type": "educational", "topic": "RRSP deadline approaching", "angle": "Last-minute RRSP tips for 2025 tax year"},
        {"day": "Tuesday", "platform": "facebook", "type": "local", "topic": "London housing market", "angle": "What rising home values mean for your insurance coverage"},
        {"day": "Wednesday", "platform": "linkedin", "type": "story", "topic": "Client win", "angle": "Anonymized story about a family getting the right coverage"},
        {"day": "Thursday", "platform": "instagram", "type": "educational", "topic": "Disability insurance", "angle": "Most people underestimate disability risk"},
        {"day": "Friday", "platform": "linkedin", "type": "timely", "topic": "BoC rate decision", "angle": "What the latest rate hold means for your finances"},
    ])
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = plan_json
    mock_client.chat.completions.create.return_value = mock_response

    plan = content_engine.generate_weekly_plan()
    assert plan is not None
    assert isinstance(plan, list)
    assert len(plan) == 5


@patch("content_engine.openai_client")
def test_generate_weekly_plan_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    plan = content_engine.generate_weekly_plan()
    assert plan is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_content_engine.py -v`
Expected: FAIL — `content_engine` module doesn't exist

- [ ] **Step 3: Implement content_engine.py**

Create `content_engine.py`:

```python
"""Content generation engine — social media posts in Marc's voice.

Generates LinkedIn, Facebook, and Instagram posts using brand voice examples
as few-shot prompts. Content types: educational, local angle, story, timely/reactive.
All content runs through compliance before queuing for Marc's approval.
"""

import json
import logging
import os
import re

from openai import OpenAI

import db
import market_intel

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

POST_TYPE_DESCRIPTIONS = {
    "educational": "Financial planning tips, insurance education, practical advice",
    "local": "London, Ontario context — local community, local market, local events",
    "story": "Anonymized client success story or relatable scenario",
    "timely": "Reactive to market events, rate changes, seasonal topics, news",
    "general": "General-purpose financial awareness post",
}

GENERATE_POST_PROMPT = """You are writing a social media post for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

PLATFORM: {platform}
POST TYPE: {post_type} — {post_type_description}
TOPIC: {topic}
CONTEXT: {context}

MARC'S VOICE (study these examples carefully — match the tone, length, and style):
{brand_voice_examples}

GUIDELINES:
1. Sound like Marc — warm, approachable, professional, never salesy
2. Use plain language, no jargon
3. Keep it concise: LinkedIn 150-250 words, Facebook 100-200 words, Instagram 80-150 words
4. Include a call-to-action that feels natural (question, invitation to chat, link to booking)
5. No hashtag spam — max 3 relevant hashtags for LinkedIn/Instagram, none for Facebook
6. Reference London, Ontario when it fits naturally
7. NEVER make specific return promises, rate guarantees, or misleading claims
8. Do NOT include emojis unless the brand voice examples use them

Write ONLY the post text. No explanations, no meta-commentary."""

WEEKLY_PLAN_PROMPT = """You are planning Marc Pereira's social media content for the upcoming week. Marc is a financial advisor at Co-operators in London, Ontario.

CURRENT SEASON/CONTEXT:
{seasonal_context}

UPCOMING MARKET EVENTS:
{market_events}

PIPELINE CONTEXT:
{pipeline_context}

BRAND VOICE EXAMPLES (for tone reference):
{brand_voice_examples}

Generate a 5-post content plan for the week. Mix of content types:
- 2x Educational (financial tips, insurance education)
- 1x Local angle (London, Ontario context)
- 1x Story (anonymized client scenario)
- 1x Timely/reactive (market events, seasonal, or news)

Return ONLY a JSON array with exactly 5 objects:
[
  {"day": "Monday", "platform": "linkedin", "type": "educational", "topic": "...", "angle": "..."},
  ...
]

Spread posts across platforms: primarily LinkedIn (3), with Facebook (1) and Instagram (1).
Each "angle" should be 1-2 sentences explaining the specific approach for that post."""


def get_brand_voice_examples(platform=None, limit=10):
    """Get brand voice examples, optionally filtered by platform."""
    with db.get_db() as conn:
        if platform:
            rows = conn.execute(
                "SELECT * FROM brand_voice WHERE platform = ? ORDER BY id DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM brand_voice ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def add_brand_voice_example(platform, content, post_type="general"):
    """Add a new brand voice example."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            (platform, content, post_type),
        )
    logger.info("Added brand voice example: %s / %s", platform, post_type)


def generate_post(platform, post_type, topic, context=""):
    """Generate a single social media post.

    Returns dict with: platform, post_type, topic, content. Returns None on failure.
    """
    examples = get_brand_voice_examples(platform=platform, limit=5)
    if not examples:
        examples = get_brand_voice_examples(limit=5)  # Fall back to all platforms

    examples_text = "\n\n".join(
        f"[{e.get('post_type', 'general')}] {e['content']}" for e in examples
    ) if examples else "No brand voice examples yet — write in a warm, professional tone."

    type_desc = POST_TYPE_DESCRIPTIONS.get(post_type, POST_TYPE_DESCRIPTIONS["general"])

    try:
        prompt = GENERATE_POST_PROMPT.replace("{platform}", platform)
        prompt = prompt.replace("{post_type_description}", type_desc)
        prompt = prompt.replace("{post_type}", post_type)
        # Static replacements first, user-sourced last
        prompt = prompt.replace("{topic}", topic)
        prompt = prompt.replace("{context}", context)
        prompt = prompt.replace("{brand_voice_examples}", examples_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            temperature=0.8,
        )
        content = response.choices[0].message.content.strip()
        return {
            "platform": platform,
            "post_type": post_type,
            "topic": topic,
            "content": content,
        }
    except Exception:
        logger.exception("Post generation failed for %s/%s", platform, post_type)
        return None


def generate_weekly_plan():
    """Generate a 5-post weekly content plan.

    Returns list of dicts with: day, platform, type, topic, angle. Returns None on failure.
    """
    # Gather context
    seasonal = market_intel.get_seasonal_context()
    events = market_intel.get_upcoming_events(days_ahead=14)
    events_text = "\n".join(
        f"- {e['title']} ({e['date']}): {e['description'][:100]}" for e in events[:5]
    ) if events else "No upcoming market events."

    # Pipeline context
    try:
        pipeline_prospects = db.read_pipeline()
        active = [p for p in pipeline_prospects if p.get("stage") not in ("Closed Won", "Closed Lost", "")]
        product_counts = {}
        for p in active:
            prod = p.get("product", "Other") or "Other"
            product_counts[prod] = product_counts.get(prod, 0) + 1
        pipeline_text = f"{len(active)} active prospects. Top products: " + ", ".join(
            f"{k} ({v})" for k, v in sorted(product_counts.items(), key=lambda x: -x[1])[:3]
        )
    except Exception:
        pipeline_text = "Pipeline data unavailable."

    # Brand voice examples
    examples = get_brand_voice_examples(limit=5)
    examples_text = "\n\n".join(
        f"[{e.get('post_type', 'general')}] {e['content']}" for e in examples
    ) if examples else "No brand voice examples yet."

    try:
        prompt = WEEKLY_PLAN_PROMPT.replace("{seasonal_context}", seasonal)
        prompt = prompt.replace("{market_events}", events_text)
        prompt = prompt.replace("{pipeline_context}", pipeline_text)
        prompt = prompt.replace("{brand_voice_examples}", examples_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            temperature=0.7,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rstrip()
            if raw.endswith("```"):
                raw = raw[:-3].rstrip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        plan = json.loads(raw)
        if not isinstance(plan, list):
            logger.error("Weekly plan is not a list: %s", type(plan))
            return None
        return plan
    except Exception:
        logger.exception("Weekly content plan generation failed")
        return None


def format_plan_for_telegram(plan):
    """Format a weekly content plan for Telegram display.

    Args:
        plan: list of dicts from generate_weekly_plan()
    Returns:
        str: formatted plan text
    """
    lines = ["WEEKLY CONTENT PLAN\n"]
    for i, post in enumerate(plan, 1):
        lines.append(
            f"{i}. {post.get('day', '?')} — {post.get('platform', '?')} ({post.get('type', '?')})"
        )
        lines.append(f"   Topic: {post.get('topic', '?')}")
        lines.append(f"   Angle: {post.get('angle', '?')}")
        lines.append("")
    lines.append("Reply with changes or use the buttons below.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_content_engine.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add content_engine.py tests/test_content_engine.py
git commit -m "feat: add content generation engine with brand voice and weekly planning"
```

---

### Task 4: Wire Content Engine to Bot — /voice, /content, /calendar Commands

**Files:**
- Modify: `bot.py` (add 3 new commands + content plan approval handler)

- [ ] **Step 1: Add /voice command to bot.py**

In `bot.py`, add near other command handlers:

```python
async def cmd_voice(update, context):
    """Manage brand voice examples: /voice add <platform> <type> <content> or /voice list"""
    if not await _require_admin(update):
        return

    import content_engine

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/voice add linkedin educational <post text>\n"
            "/voice add facebook story <post text>\n"
            "/voice list [platform]\n\n"
            "Types: educational, local, story, timely, general"
        )
        return

    action = args[0].lower()

    if action == "list":
        platform = args[1] if len(args) > 1 else None
        examples = content_engine.get_brand_voice_examples(platform=platform, limit=10)
        if not examples:
            await update.message.reply_text("No brand voice examples yet. Add some with /voice add")
            return
        lines = [f"Brand voice examples ({len(examples)}):"]
        for e in examples:
            preview = e["content"][:100] + "..." if len(e["content"]) > 100 else e["content"]
            lines.append(f"\n#{e['id']} [{e['platform']}/{e['post_type']}]\n{preview}")
        await update.message.reply_text("\n".join(lines))

    elif action == "add":
        if len(args) < 4:
            await update.message.reply_text("Usage: /voice add <platform> <type> <post text>")
            return
        platform = args[1].lower()
        post_type = args[2].lower()
        content_text = " ".join(args[3:])
        content_engine.add_brand_voice_example(platform, content_text, post_type)
        count = len(content_engine.get_brand_voice_examples(platform=platform))
        await update.message.reply_text(
            f"Added brand voice example ({platform}/{post_type}).\n"
            f"You now have {count} examples for {platform}."
        )
    else:
        await update.message.reply_text("Unknown action. Use /voice add or /voice list")
```

- [ ] **Step 2: Add /content command to bot.py**

```python
async def cmd_content(update, context):
    """Generate content: /content plan or /content post <platform> <type> <topic>"""
    if not await _require_admin(update):
        return

    import content_engine

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/content plan — Generate this week's 5-post content plan\n"
            "/content post linkedin educational RRSP tips — Generate a single post"
        )
        return

    action = args[0].lower()

    if action == "plan":
        await update.message.reply_text("Generating your weekly content plan...")
        plan = content_engine.generate_weekly_plan()
        if not plan:
            await update.message.reply_text("Failed to generate content plan. Try again.")
            return

        text = content_engine.format_plan_for_telegram(plan)

        # Store plan in approval queue
        import approval_queue
        draft = approval_queue.add_draft(
            draft_type="content_plan",
            channel="social_media",
            content=text,
            context="Weekly content plan — approve to generate all posts",
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve & Generate", callback_data=f"content_approve_{draft['id']}"),
                InlineKeyboardButton("Dismiss", callback_data=f"content_dismiss_{draft['id']}"),
            ],
        ])
        msg = await update.message.reply_text(text, reply_markup=keyboard)
        approval_queue.set_telegram_message_id(draft["id"], str(msg.message_id))

    elif action == "post":
        if len(args) < 4:
            await update.message.reply_text("Usage: /content post <platform> <type> <topic>")
            return
        platform = args[1].lower()
        post_type = args[2].lower()
        topic = " ".join(args[3:])
        await update.message.reply_text(f"Generating {post_type} post for {platform}...")

        import market_intel
        context_text = market_intel.get_seasonal_context()
        post = content_engine.generate_post(platform, post_type, topic, context=context_text)
        if not post:
            await update.message.reply_text("Failed to generate post. Try again.")
            return

        # Run compliance
        import compliance as comp
        comp_result = comp.check_compliance(post["content"])
        comp.log_action(
            action_type="content_generation",
            target=f"{platform}/{post_type}",
            content=post["content"],
            compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
        )

        # Queue for approval
        import approval_queue
        draft = approval_queue.add_draft(
            draft_type="content_post",
            channel=f"{platform}_post",
            content=post["content"],
            context=f"{post_type}: {topic}",
        )

        comp_flag = ""
        if not comp_result["passed"]:
            comp_flag = f"\n\nCOMPLIANCE FLAG: {'; '.join(comp_result['issues'])}"

        text = (
            f"CONTENT DRAFT — {platform.title()} ({post_type})\n"
            f"Topic: {topic}\n\n"
            f"{post['content']}"
            f"{comp_flag}\n\n"
            f"Queue #{draft['id']}"
        )
        keyboard = _draft_keyboard(draft["id"])
        msg = await update.message.reply_text(text, reply_markup=keyboard)
        approval_queue.set_telegram_message_id(draft["id"], str(msg.message_id))
    else:
        await update.message.reply_text("Unknown action. Use /content plan or /content post")
```

- [ ] **Step 3: Add /calendar command to bot.py**

```python
async def cmd_calendar(update, context):
    """View market calendar or add events: /calendar or /calendar add <date> <title>"""
    if not await _require_admin(update):
        return

    import market_intel

    args = context.args
    if not args or args[0].lower() in ("view", "upcoming"):
        events = market_intel.get_upcoming_events(days_ahead=30)
        if not events:
            await update.message.reply_text("No upcoming market events in the next 30 days.")
            return
        lines = ["MARKET CALENDAR — Next 30 Days\n"]
        for e in events:
            lines.append(f"  {e['date']} — {e['title']}")
            if e.get("description"):
                lines.append(f"    {e['description'][:80]}")
        seasonal = market_intel.get_seasonal_context()
        lines.append(f"\nSeason: {seasonal}")
        await update.message.reply_text("\n".join(lines))

    elif args[0].lower() == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /calendar add <YYYY-MM-DD> <title> [description]")
            return
        date_str = args[1]
        title = " ".join(args[2:5])
        description = " ".join(args[5:]) if len(args) > 5 else ""
        market_intel.add_event(
            event_type="custom",
            title=title,
            date=date_str,
            description=description,
        )
        await update.message.reply_text(f"Added market event: {title} on {date_str}")
    else:
        await update.message.reply_text("Usage: /calendar or /calendar add <date> <title>")
```

- [ ] **Step 4: Add content plan callback handler to bot.py**

Add to `handle_draft_callback()` or add a new handler. Extend the existing pattern by adding a new callback handler:

```python
async def handle_content_callback(update, context):
    """Handle inline keyboard callbacks for content plan approval."""
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        return

    data = query.data
    if not data.startswith("content_"):
        return

    parts = data.split("_", 2)
    if len(parts) < 3:
        return

    action = parts[1]
    try:
        queue_id = int(parts[2])
    except ValueError:
        return

    import approval_queue
    draft = approval_queue.get_draft_by_id(queue_id)
    if not draft:
        await query.edit_message_text("Content plan not found or already processed.")
        return

    if action == "approve":
        approval_queue.update_draft_status(queue_id, "approved")
        # If this is a content_post (not a plan), save the content as a brand voice example
        # This is the brand voice evolution mechanism — approved posts improve the voice library
        if draft.get("type") == "content_post" and draft.get("content"):
            try:
                import content_engine
                channel = draft.get("channel", "linkedin_post")
                platform = channel.replace("_post", "")
                context_text = draft.get("context", "")
                post_type = context_text.split(":")[0].strip() if ":" in context_text else "general"
                content_engine.add_brand_voice_example(platform, draft["content"], post_type)
                logger.info("Brand voice updated from approved content post #%s", queue_id)
            except Exception:
                logger.warning("Brand voice update failed for #%s (non-blocking)", queue_id)

        if draft.get("type") == "content_plan":
            await query.edit_message_text(
                f"Content plan approved (#{queue_id}).\n\n"
                "Use /content post to generate individual posts from this plan."
            )
        else:
            content = draft.get("content", "")
            if len(content) > 3800:
                content = content[:3800] + "\n...(truncated)"
            await query.edit_message_text(
                f"APPROVED — content post #{queue_id}\n\n"
                f"{content}\n\n"
                "Copy-paste into Publer for scheduling."
            )
    elif action == "dismiss":
        approval_queue.update_draft_status(queue_id, "dismissed")
        await query.edit_message_text(f"Content dismissed (#{queue_id}).")
```

- [ ] **Step 5: Register all new handlers in build_application()**

In `build_application()`, add BEFORE the MessageHandler lines:

```python
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("content", cmd_content))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("news", cmd_calendar))  # alias for /calendar
    app.add_handler(CallbackQueryHandler(handle_content_callback, pattern=r"^content_"))
```

- [ ] **Step 6: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "feat: add /voice, /content, /calendar commands with content plan approval"
```

---

## Chunk 3: Scheduler Jobs + Briefing Integration

### Task 5: Weekly Content Calendar Scheduler Job

**Files:**
- Modify: `scheduler.py` (add Sunday 6PM content plan job)

- [ ] **Step 1: Add weekly content plan function to scheduler.py**

Add to `scheduler.py` BEFORE `start_scheduler()`:

```python
async def weekly_content_plan():
    """Generate and send weekly content plan every Sunday at 6PM."""
    if not _bot or not CHAT_ID:
        return

    try:
        import content_engine
        import approval_queue

        plan = content_engine.generate_weekly_plan()
        if not plan:
            await _bot.send_message(chat_id=CHAT_ID, text="Failed to generate weekly content plan. Use /content plan to try manually.")
            return

        text = content_engine.format_plan_for_telegram(plan)

        # Store in approval queue
        draft = approval_queue.add_draft(
            draft_type="content_plan",
            channel="social_media",
            content=text,
            context="Weekly content plan — approve to generate all posts",
        )

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve & Generate", callback_data=f"content_approve_{draft['id']}"),
                InlineKeyboardButton("Dismiss", callback_data=f"content_dismiss_{draft['id']}"),
            ],
        ])

        msg = await _bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=keyboard)
        approval_queue.set_telegram_message_id(draft["id"], str(msg.message_id))
        logger.info("Weekly content plan sent (queue #%s)", draft["id"])

    except Exception:
        logger.exception("Weekly content plan generation failed")
```

- [ ] **Step 2: Add daily market check function**

```python
async def daily_market_check():
    """Check for market events today and include in context."""
    if not _bot or not CHAT_ID:
        return

    try:
        import market_intel
        events = market_intel.get_upcoming_events(days_ahead=1)
        if not events:
            return

        lines = ["MARKET ALERT — Today's Events:"]
        for e in events:
            lines.append(f"  - {e['title']}: {e['description'][:150]}")
            if e.get("relevance_products"):
                lines.append(f"    Relevant for: {e['relevance_products']}")

        await _bot.send_message(chat_id=CHAT_ID, text="\n".join(lines))
        logger.info("Sent market alert for %d events", len(events))

    except Exception:
        logger.exception("Daily market check failed")
```

- [ ] **Step 3: Register the jobs in start_scheduler()**

Add BEFORE `scheduler.start()`:

```python
    # Weekly content plan — Sunday 6PM ET
    scheduler.add_job(
        weekly_content_plan,
        "cron",
        day_of_week="sun",
        hour=18,
        minute=0,
        id="weekly_content_plan",
        name="Weekly Content Plan",
    )

    # Daily market intelligence check — 7:30 AM ET weekdays
    scheduler.add_job(
        daily_market_check,
        "cron",
        day_of_week="mon-fri",
        hour=7,
        minute=30,
        id="daily_market_check",
        name="Daily Market Check",
    )
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add scheduler.py
git commit -m "feat: add weekly content plan and daily market check scheduler jobs"
```

---

### Task 6: Wire Market Intelligence into Morning Briefing

**Files:**
- Modify: `briefing.py` (add market events to briefing data and prompt)

- [ ] **Step 1: Add market intel to assemble_briefing_data()**

In `briefing.py`, inside `assemble_briefing_data()`, after the existing data gathering (around line 65, before the return dict), add:

```python
    # Market intelligence (calendar seeded at bot startup, not here)
    try:
        import market_intel
        market_events_text = market_intel.format_for_briefing(days_ahead=7)
    except Exception:
        logger.exception("Market intel failed for briefing (non-blocking)")
        market_events_text = ""
```

Add `"market_events": market_events_text` to the returned dict.

- [ ] **Step 2: Add market events to briefing prompt**

**IMPORTANT:** `briefing.py` uses `.format()` (NOT `.replace()`) with `_escape_braces()` for user data. Follow that pattern.

In `briefing.py`, add to the `BRIEFING_PROMPT` template (around line 122, before the INSTRUCTIONS section):

```
MARKET INTELLIGENCE:
{market_events}
```

And in the instructions section, add:
```
7. MARKET CONTEXT — if any upcoming events, mention how they affect prospects
```

In `_build_briefing_prompt()`, add `market_events` to the `.format()` call (around line 220-232). The market events text is user-derived data, so wrap it with `_escape_braces()`:

```python
        market_events=_escape_braces(data.get("market_events", "")),
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add briefing.py
git commit -m "feat: add market intelligence to morning briefing"
```

---

### Task 7: Seed Calendar on Startup + Final Integration

**Files:**
- Modify: `bot.py` (seed market calendar on bot startup)

- [ ] **Step 1: Add market calendar seeding to bot startup**

In `bot.py`, inside `build_application()` or at the module level near `db.init_db()` calls, add:

```python
    # Seed market calendar with default events
    try:
        import market_intel
        market_intel.seed_default_calendar()
    except Exception:
        logger.warning("Market calendar seeding failed (non-blocking)")
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Verify all new modules import cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -c "import content_engine; import market_intel; print('Phase 3 modules OK')"`
Expected: "Phase 3 modules OK"

- [ ] **Step 4: Verify bot loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && TELEGRAM_BOT_TOKEN=test OPENAI_API_KEY=test TELEGRAM_CHAT_ID=123 WEBHOOK_SECRET=test python3 -c "import bot; print('Bot loads OK')"`
Expected: "Bot loads OK"

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: Phase 3 complete — The Marketer content generation engine"
```

---

## Summary of New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `content_engine.py` | ~220 | Generate social media posts with brand voice, weekly planning |
| `market_intel.py` | ~160 | Pre-loaded market calendar, seasonal context, content angles |
| `tests/test_content_engine.py` | ~120 | Tests for content engine |
| `tests/test_market_intel.py` | ~80 | Tests for market intelligence |
| `tests/test_content_schema.py` | ~45 | Tests for new DB tables |

## Modified Files

| File | Changes |
|------|---------|
| `db.py` | +brand_voice and market_calendar tables |
| `bot.py` | +/voice, /content, /calendar commands, +content plan callback handler, +market calendar seeding |
| `scheduler.py` | +weekly content plan job (Sun 6PM), +daily market check (7:30AM) |
| `briefing.py` | +market intelligence in morning briefing data and prompt |
