# Automation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tag-based trigger system, product intake forms, formalized cross-sell engine, and referral tracking — turning SteadyBook from a passive CRM into an active automation platform.

**Architecture:** A `tag_engine.py` module listens for tag application events and enrolls prospects in appropriate sequences. Intake forms are Flask routes that render product-specific HTML forms and write responses to `intake_form_responses`. The cross-sell engine runs after every stage change to Closed Won. Referral tracking adds source attribution and automated ask sequences.

**Tech Stack:** Python 3.13, Flask, SQLite via `db.py`, existing `sequences.py` and `nurture.py`, existing `follow_up.py`.

**Dependency:** Requires Task 1 of the Capture Layer plan (DB schema with `prospect_tags`, `intake_form_responses`, `referrals` tables) before starting.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `tag_engine.py` | Create | Tag trigger rules → sequence enrollment |
| `cross_sell.py` | Create | Formalized cross-sell engine with product matrix |
| `referral.py` | Create | Referral tracking, attribution, ask sequences |
| `intake_forms.py` | Create | Flask routes for product-specific intake forms |
| `templates/intake_form.html` | Create | Dynamic intake form template |
| `dashboard.py` | Modify | Register intake form blueprint |
| `db.py` | Modify | Add `get_trust_level` helper if missing |
| `tests/test_tag_engine.py` | Create | Unit tests for tag trigger logic |
| `tests/test_cross_sell.py` | Create | Unit tests for cross-sell product matrix |
| `tests/test_referral.py` | Create | Unit tests for referral tracking |

---

## Task 1: Tag Engine

**Files:**
- Create: `tag_engine.py`
- Create: `tests/test_tag_engine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tag_engine.py`:

```python
"""Tests for the tag-based trigger engine."""
import pytest
from unittest.mock import patch, MagicMock, call


def test_get_trigger_rules_new_lead():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("new_lead")
    assert len(actions) > 0
    action_types = [a["type"] for a in actions]
    assert "create_task" in action_types


def test_get_trigger_rules_source_qr():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("source_qr")
    action_types = [a["type"] for a in actions]
    assert "enroll_sequence" in action_types or "create_task" in action_types


def test_get_trigger_rules_unknown_tag_returns_empty():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("nonexistent_tag_xyz")
    assert actions == []


def test_get_trigger_rules_closed_life():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("closed_life")
    action_types = [a["type"] for a in actions]
    assert "enroll_sequence" in action_types or "schedule_crosssell" in action_types


def test_process_tag_creates_task(monkeypatch):
    from tag_engine import process_tag
    import db

    monkeypatch.setattr(db, "add_task", MagicMock(return_value=1))
    monkeypatch.setattr(db, "get_tags", MagicMock(return_value=["new_lead"]))

    prospect = {"id": 1, "name": "Sarah Chen", "stage": "New Lead"}
    with patch("tag_engine.get_trigger_actions", return_value=[
        {"type": "create_task", "subject": "Follow up with {{name}}", "due_days": 2}
    ]):
        process_tag(prospect, "new_lead")
        db.add_task.assert_called_once()


def test_process_tag_skips_do_not_contact(monkeypatch):
    from tag_engine import process_tag
    import db

    monkeypatch.setattr(db, "get_tags", MagicMock(return_value=["do_not_contact"]))
    add_task_mock = MagicMock()
    monkeypatch.setattr(db, "add_task", add_task_mock)

    prospect = {"id": 1, "name": "Sarah Chen", "stage": "New Lead"}
    process_tag(prospect, "new_lead")
    add_task_mock.assert_not_called()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_tag_engine.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'tag_engine'`

- [ ] **Step 3: Create `tag_engine.py`**

```python
"""
Tag-based trigger engine.
When a tag is applied to a prospect, this module determines what actions to take.
Called from db.apply_tag() — or manually after bulk tag operations.

Trigger rules are defined inline. To add a new trigger:
  1. Add an entry to TRIGGER_RULES
  2. Handle the action type in _execute_action if needed
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# ── Trigger Rules ─────────────────────────────────────────────────────────────
# Each tag maps to a list of actions.
# Action types: create_task | enroll_sequence | schedule_crosssell | apply_tag
# {{name}} in strings is replaced with the prospect's name.

TRIGGER_RULES: dict[str, list[dict]] = {
    "new_lead": [
        {
            "type": "create_task",
            "subject": "Follow up with {{name}}",
            "description": "New lead — reach out within 48 hours",
            "due_days": 2,
        }
    ],
    "source_qr": [
        {
            "type": "create_task",
            "subject": "QR lead — contact {{name}}",
            "description": "They scanned your QR code. Reach out while interest is fresh.",
            "due_days": 1,
        }
    ],
    "source_event": [
        {
            "type": "create_task",
            "subject": "Event follow-up: {{name}}",
            "description": "Met at an event. Follow up within 24 hours.",
            "due_days": 1,
        }
    ],
    "meeting_booked": [
        {
            "type": "create_task",
            "subject": "Prep for {{name}} meeting",
            "description": "Review their profile, prepare talking points.",
            "due_days": 0,
        }
    ],
    "closed_life": [
        {
            "type": "schedule_crosssell",
            "product": "disability",
            "delay_days": 30,
        }
    ],
    "closed_disability": [
        {
            "type": "schedule_crosssell",
            "product": "critical_illness",
            "delay_days": 30,
        }
    ],
    "policy_renewal_90": [
        {
            "type": "create_task",
            "subject": "Renewal coming: {{name}}",
            "description": "Policy renews in ~90 days. Start renewal conversation.",
            "due_days": 3,
        }
    ],
    "policy_renewal_30": [
        {
            "type": "create_task",
            "subject": "URGENT renewal: {{name}}",
            "description": "Policy renews in ~30 days. Contact immediately.",
            "due_days": 0,
        }
    ],
    "job_change": [
        {
            "type": "create_task",
            "subject": "Job change signal: {{name}}",
            "description": "Detected a job change. Great time for group benefits / life review conversation.",
            "due_days": 2,
        }
    ],
    "referral_given": [
        {
            "type": "create_task",
            "subject": "Thank {{name}} for referral",
            "description": "Send a thank you within 24 hours of the referral.",
            "due_days": 1,
        }
    ],
    "interest_life": [
        {
            "type": "create_task",
            "subject": "Send life insurance intake form to {{name}}",
            "description": "They indicated interest in life insurance. Send the intake form.",
            "due_days": 1,
        }
    ],
    "interest_group_benefits": [
        {
            "type": "create_task",
            "subject": "Send group benefits intake form to {{name}}",
            "description": "They indicated interest in group benefits. Send the intake form.",
            "due_days": 1,
        }
    ],
    "interest_disability": [
        {
            "type": "create_task",
            "subject": "Send disability intake form to {{name}}",
            "description": "They indicated interest in disability coverage. Send the intake form.",
            "due_days": 1,
        }
    ],
}


def get_trigger_actions(tag: str) -> list[dict]:
    """Return list of actions for a given tag. Empty list if no rules."""
    return TRIGGER_RULES.get(tag, [])


def _execute_action(prospect: dict, action: dict) -> None:
    """Execute a single trigger action for a prospect."""
    name = prospect["name"]
    pid = prospect["id"]
    action_type = action["type"]

    if action_type == "create_task":
        subject = action.get("subject", "Follow up").replace("{{name}}", name)
        description = action.get("description", "").replace("{{name}}", name)
        due_days = action.get("due_days", 2)
        due_date = (datetime.now() + timedelta(days=due_days)).strftime("%Y-%m-%d")
        db.add_task({
            "prospect": name,
            "subject": subject,
            "description": description,
            "due_date": due_date,
            "status": "pending",
        })
        logger.info(f"Task created for {name}: {subject}")

    elif action_type == "schedule_crosssell":
        product = action.get("product", "")
        delay_days = action.get("delay_days", 30)
        trigger_date = (datetime.now() + timedelta(days=delay_days)).strftime("%Y-%m-%d")
        # Apply a dated cross-sell tag that the omniscient agent will pick up
        db.apply_tag(pid, f"crosssell_{product}_after_{trigger_date}")
        logger.info(f"Cross-sell scheduled for {name}: {product} after {delay_days} days")

    elif action_type == "enroll_sequence":
        sequence_name = action.get("sequence", "")
        if sequence_name:
            try:
                import sequences
                sequences.enroll_prospect(name, sequence_name)
                logger.info(f"Enrolled {name} in sequence: {sequence_name}")
            except Exception as e:
                logger.warning(f"Sequence enrollment failed for {name}: {e}")

    elif action_type == "apply_tag":
        new_tag = action.get("tag", "")
        if new_tag:
            db.apply_tag(pid, new_tag)


def process_tag(prospect: dict, tag: str) -> int:
    """
    Process all trigger actions for a tag applied to a prospect.
    Returns number of actions executed.
    Skips prospects tagged do_not_contact.
    """
    pid = prospect["id"]
    current_tags = db.get_tags(pid)

    if "do_not_contact" in current_tags:
        logger.debug(f"Skipping triggers for {prospect['name']} — do_not_contact tag set")
        return 0

    actions = get_trigger_actions(tag)
    executed = 0

    for action in actions:
        try:
            _execute_action(prospect, action)
            executed += 1
        except Exception:
            logger.exception(f"Action failed for tag '{tag}' on {prospect['name']}: {action}")

    return executed


def process_tags_for_prospect(prospect_id: int) -> int:
    """Re-process all current tags for a prospect. Used after bulk tag imports."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        if not row:
            return 0
        prospect = dict(row)

    tags = db.get_tags(prospect_id)
    total = 0
    for tag in tags:
        total += process_tag(prospect, tag)
    return total
```

- [ ] **Step 4: Hook `process_tag` into `db.apply_tag`**

In `db.py`, update the `apply_tag` function to call the tag engine after successful insertion:

```python
def apply_tag(prospect_id: int, tag: str, applied_by: str = "system") -> bool:
    """Apply a tag to a prospect. Returns True if new, False if already existed."""
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO prospect_tags (prospect_id, tag, applied_by) VALUES (?,?,?)",
                (prospect_id, tag, applied_by)
            )
            is_new = True
        except sqlite3.IntegrityError:
            is_new = False

    if is_new:
        # Fire tag triggers asynchronously (non-blocking)
        try:
            import tag_engine
            with get_db() as conn:
                row = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
                if row:
                    prospect = dict(row)
                    tag_engine.process_tag(prospect, tag)
        except Exception:
            pass  # Tag triggers are best-effort, never block tag application

    return is_new
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_tag_engine.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add tag_engine.py db.py tests/test_tag_engine.py
git commit -m "feat: add tag-based trigger engine with rules for all core tags"
```

---

## Task 2: Cross-Sell Engine

**Files:**
- Create: `cross_sell.py`
- Create: `tests/test_cross_sell.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cross_sell.py`:

```python
"""Tests for the cross-sell engine."""
import pytest
from unittest.mock import patch, MagicMock


def test_get_crosssell_recommendations_life_only():
    from cross_sell import get_crosssell_recommendations
    current_products = ["life_insurance"]
    recs = get_crosssell_recommendations(current_products, is_business_owner=False)
    product_names = [r["product"] for r in recs]
    assert "disability" in product_names
    assert "critical_illness" in product_names


def test_get_crosssell_recommendations_business_owner():
    from cross_sell import get_crosssell_recommendations
    recs = get_crosssell_recommendations(["life_insurance"], is_business_owner=True)
    product_names = [r["product"] for r in recs]
    assert "group_benefits" in product_names


def test_get_crosssell_recommendations_no_duplicates():
    from cross_sell import get_crosssell_recommendations
    recs = get_crosssell_recommendations(["life_insurance", "disability"], is_business_owner=False)
    product_names = [r["product"] for r in recs]
    assert "life_insurance" not in product_names
    assert "disability" not in product_names


def test_get_crosssell_recommendations_fully_covered():
    from cross_sell import get_crosssell_recommendations
    all_products = ["life_insurance", "disability", "critical_illness", "group_benefits"]
    recs = get_crosssell_recommendations(all_products, is_business_owner=True)
    assert len(recs) == 0 or all(r["product"] not in all_products for r in recs)


def test_cooldown_blocks_recommendation():
    from cross_sell import is_in_cooldown
    # 20 days ago — within 30-day cooldown
    from datetime import datetime, timedelta
    last_attempt = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    assert is_in_cooldown(last_attempt, cooldown_days=30) is True


def test_cooldown_allows_after_period():
    from cross_sell import is_in_cooldown
    from datetime import datetime, timedelta
    last_attempt = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
    assert is_in_cooldown(last_attempt, cooldown_days=30) is False


def test_format_crosssell_task_subject():
    from cross_sell import format_crosssell_task
    task = format_crosssell_task("Sarah Chen", "disability")
    assert "Sarah Chen" in task["subject"]
    assert "disability" in task["subject"].lower() or "Disability" in task["subject"]
    assert "talking_point" in task
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_cross_sell.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'cross_sell'`

- [ ] **Step 3: Create `cross_sell.py`**

```python
"""
Formalized cross-sell engine.
Fires after every stage change to 'Closed Won'.
Uses a product matrix to identify gaps and recommend next products.
Applies a 30-day cooldown between cross-sell attempts per client.
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

COOLDOWN_DAYS = 30

# ── Product Matrix ─────────────────────────────────────────────────────────────
# Maps owned products → recommended next products with talking points.
# Keys must match the product tags applied when a deal closes.

PRODUCT_MATRIX = {
    "life_insurance": [
        {
            "product": "disability",
            "talking_point": "Your life insurance protects your family if you die — but what protects your income if you can't work? Disability covers that gap.",
        },
        {
            "product": "critical_illness",
            "talking_point": "A critical illness diagnosis like cancer or a heart attack won't kill you — but the financial impact can be devastating. CI pays a lump sum so you can focus on recovery.",
        },
    ],
    "disability": [
        {
            "product": "critical_illness",
            "talking_point": "Disability covers your income during recovery. Critical illness gives you a tax-free lump sum on diagnosis — covers things disability doesn't.",
        },
    ],
    "group_benefits": [
        {
            "product": "key_person_life",
            "talking_point": "Your business has group benefits for your team — but what happens to the business if you or a key person passes away? Key person life insurance protects the business itself.",
        },
    ],
}

BUSINESS_OWNER_PRODUCTS = [
    {
        "product": "group_benefits",
        "talking_point": "As a business owner, group benefits are a tax-efficient way to attract and retain talent — and the premiums are deductible.",
    },
    {
        "product": "key_person_life",
        "talking_point": "Key person insurance protects your business if a critical team member passes away. The business owns the policy.",
    },
]

ANNUAL_REVIEW_TAG = "annual_review_due"


def get_crosssell_recommendations(
    current_products: list[str],
    is_business_owner: bool = False
) -> list[dict]:
    """
    Given a list of products the client already has, return recommended next products.
    Excludes products the client already has.
    Returns list of dicts with keys: product, talking_point.
    """
    current_set = set(p.lower().replace(" ", "_") for p in current_products)
    recommendations = []
    seen = set()

    for product in current_set:
        for rec in PRODUCT_MATRIX.get(product, []):
            if rec["product"] not in current_set and rec["product"] not in seen:
                recommendations.append(rec)
                seen.add(rec["product"])

    if is_business_owner:
        for rec in BUSINESS_OWNER_PRODUCTS:
            if rec["product"] not in current_set and rec["product"] not in seen:
                recommendations.append(rec)
                seen.add(rec["product"])

    return recommendations


def is_in_cooldown(last_crosssell_date: str, cooldown_days: int = COOLDOWN_DAYS) -> bool:
    """Return True if the client is still within the cooldown period."""
    if not last_crosssell_date:
        return False
    try:
        last = datetime.strptime(last_crosssell_date, "%Y-%m-%d")
        return (datetime.now() - last).days < cooldown_days
    except ValueError:
        return False


def format_crosssell_task(prospect_name: str, product: str) -> dict:
    """Build a task dict for a cross-sell recommendation."""
    product_display = product.replace("_", " ").title()
    due_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    return {
        "prospect": prospect_name,
        "subject": f"Cross-sell: {product_display} for {prospect_name}",
        "description": f"Cross-sell window open. Recommended next product: {product_display}",
        "due_date": due_date,
        "status": "pending",
        "talking_point": "",  # Populated by run_crosssell_for_prospect
    }


def _get_client_products(prospect_name: str) -> list[str]:
    """Get all products a client currently has from the insurance_book."""
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT product_type FROM insurance_book
            WHERE client_name = ? AND status = 'Active'
        """, (prospect_name,)).fetchall()
        return [r["product_type"] for r in rows]


def _get_last_crosssell_date(prospect_name: str) -> str:
    """Get the date of the last cross-sell task created for this prospect."""
    with db.get_db() as conn:
        row = conn.execute("""
            SELECT created_at FROM tasks
            WHERE prospect = ? AND subject LIKE 'Cross-sell:%'
            ORDER BY created_at DESC LIMIT 1
        """, (prospect_name,)).fetchone()
        if row:
            return row["created_at"][:10]
    return ""


def run_crosssell_for_prospect(prospect_name: str, prospect_id: int) -> list[dict]:
    """
    Run cross-sell analysis for one prospect.
    Creates tasks for each recommendation.
    Returns list of tasks created.
    """
    # Check cooldown
    last_date = _get_last_crosssell_date(prospect_name)
    if is_in_cooldown(last_date):
        logger.debug(f"Cross-sell skipped for {prospect_name} — in cooldown")
        return []

    current_products = _get_client_products(prospect_name)
    tags = db.get_tags(prospect_id)
    is_business_owner = any(
        t in tags for t in ("interest_group_benefits", "business_owner", "self_employed")
    )

    recommendations = get_crosssell_recommendations(current_products, is_business_owner)
    created_tasks = []

    for rec in recommendations[:2]:  # Max 2 cross-sell tasks per cycle
        task = format_crosssell_task(prospect_name, rec["product"])
        task["description"] = f"{task['description']}\n\nTalking point: {rec['talking_point']}"
        db.add_task(task)

        # Schedule cross-sell tag for omniscient agent
        db.apply_tag(prospect_id, f"crosssell_{rec['product']}_pending")
        created_tasks.append(task)
        logger.info(f"Cross-sell task created for {prospect_name}: {rec['product']}")

    # Apply annual review tag
    db.apply_tag(prospect_id, ANNUAL_REVIEW_TAG)

    return created_tasks


def run_crosssell_on_close(prospect_name: str) -> list[dict]:
    """
    Entry point called when a deal closes (stage changes to 'Closed Won').
    """
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        return []
    return run_crosssell_for_prospect(prospect_name, prospect["id"])
```

- [ ] **Step 4: Hook into stage change in `dashboard.py` or `db.py`**

Find where pipeline stage updates happen. In `db.py`, find `update_prospect`. Add cross-sell trigger when stage changes to Closed Won:

```python
# In db.py update_prospect(), after the UPDATE:
if updates.get("stage") == "Closed Won":
    try:
        import cross_sell
        cross_sell.run_crosssell_on_close(name)
    except Exception:
        pass  # Non-blocking
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_cross_sell.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add cross_sell.py tests/test_cross_sell.py db.py
git commit -m "feat: formalized cross-sell engine with product matrix, cooldown, and business owner detection"
```

---

## Task 3: Referral Tracking

**Files:**
- Create: `referral.py`
- Create: `tests/test_referral.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_referral.py`:

```python
"""Tests for referral tracking."""
import pytest
from unittest.mock import patch, MagicMock


def test_record_referral_stores_link():
    from referral import record_referral
    import db

    with patch.object(db, 'get_prospect_by_name') as mock_get:
        mock_get.side_effect = lambda name: (
            {"id": 1, "name": "Sarah Chen"} if "Sarah" in name
            else {"id": 2, "name": "John Park"}
        )
        with patch("db.get_db") as mock_db:
            mock_conn = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            record_referral("Sarah Chen", "John Park", "Met at chamber of commerce event")
            mock_conn.execute.assert_called()


def test_get_top_referrers_returns_list():
    from referral import get_top_referrers
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_top_referrers()
        assert isinstance(result, list)


def test_should_send_referral_ask_at_14_days():
    from referral import should_send_referral_ask
    from datetime import datetime, timedelta
    close_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    assert should_send_referral_ask(close_date, ask_sent=False) is True


def test_should_not_send_referral_ask_if_sent():
    from referral import should_send_referral_ask
    from datetime import datetime, timedelta
    close_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    assert should_send_referral_ask(close_date, ask_sent=True) is False


def test_should_not_send_referral_ask_too_early():
    from referral import should_send_referral_ask
    from datetime import datetime, timedelta
    close_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    assert should_send_referral_ask(close_date, ask_sent=False) is False


def test_format_referral_ask_message():
    from referral import format_referral_ask_message
    msg = format_referral_ask_message("Sarah Chen", "life insurance")
    assert "Sarah" in msg
    assert len(msg) > 20
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_referral.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'referral'`

- [ ] **Step 3: Create `referral.py`**

```python
"""
Referral tracking module.
Records who referred who, surfaces top referrers,
and manages referral ask sequences (14-day and 90-day post-close).
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

ASK_WINDOWS_DAYS = [14, 90]  # Days after close to send referral ask


def record_referral(referrer_name: str, referred_name: str, notes: str = "") -> None:
    """
    Record a referral relationship.
    referrer_name: the existing client who gave the referral
    referred_name: the new prospect they referred
    """
    referrer = db.get_prospect_by_name(referrer_name)
    referred = db.get_prospect_by_name(referred_name)

    if not referrer or not referred:
        logger.warning(f"Referral record failed — could not find both parties: {referrer_name} → {referred_name}")
        return

    with db.get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO referrals
            (referrer_prospect_id, referred_prospect_id, notes)
            VALUES (?, ?, ?)
        """, (referrer["id"], referred["id"], notes))

    # Tag the referrer so they're recognized as a referral source
    db.apply_tag(referrer["id"], "referral_source")
    db.apply_tag(referred["id"], "source_referral")

    logger.info(f"Referral recorded: {referrer_name} → {referred_name}")


def get_top_referrers(limit: int = 10) -> list[dict]:
    """Return top referrers by number of referrals given."""
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT p.name, p.email, p.phone, COUNT(r.id) as referral_count
            FROM referrals r
            JOIN prospects p ON p.id = r.referrer_prospect_id
            GROUP BY r.referrer_prospect_id
            ORDER BY referral_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_referral_source(prospect_name: str) -> dict | None:
    """Get who referred a given prospect."""
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        return None

    with db.get_db() as conn:
        row = conn.execute("""
            SELECT p.name, p.email, p.phone, r.referral_date, r.notes
            FROM referrals r
            JOIN prospects p ON p.id = r.referrer_prospect_id
            WHERE r.referred_prospect_id = ?
            ORDER BY r.referral_date DESC LIMIT 1
        """, (prospect["id"],)).fetchone()
        return dict(row) if row else None


def should_send_referral_ask(close_date: str, ask_sent: bool,
                              window_days: int = 14) -> bool:
    """
    Return True if it's time to send a referral ask.
    Checks if we're past the window and the ask hasn't been sent yet.
    """
    if ask_sent:
        return False
    if not close_date:
        return False
    try:
        close_dt = datetime.strptime(close_date, "%Y-%m-%d")
        days_since = (datetime.now() - close_dt).days
        return days_since >= window_days
    except ValueError:
        return False


def format_referral_ask_message(client_name: str, product: str) -> str:
    """Generate a warm referral ask message."""
    first_name = client_name.split()[0]
    return (
        f"Hi {first_name}, I hope your {product} is giving you great peace of mind. "
        f"If you know anyone else who might benefit from a quick conversation about "
        f"their coverage, I'd love an introduction — even a quick text from you goes a long way. "
        f"Thanks so much!"
    )


def check_referral_asks() -> int:
    """
    Check all closed clients for pending referral asks.
    Called by the omniscient agent or APScheduler.
    Returns number of asks queued.
    """
    queued = 0
    with db.get_db() as conn:
        closed = conn.execute("""
            SELECT p.*, MAX(a.created_at) as close_date
            FROM prospects p
            JOIN activities a ON a.prospect = p.name
            WHERE p.stage = 'Closed Won'
            AND a.action LIKE '%Closed%'
            GROUP BY p.id
        """).fetchall()

    for row in closed:
        prospect = dict(row)
        name = prospect["name"]
        pid = prospect["id"]
        close_date = (prospect.get("close_date") or "")[:10]
        tags = db.get_tags(pid)

        if "do_not_contact" in tags:
            continue

        ask_14_sent = "referral_ask_14_sent" in tags
        ask_90_sent = "referral_ask_90_sent" in tags

        product = prospect.get("product") or "coverage"

        if should_send_referral_ask(close_date, ask_14_sent, window_days=14):
            msg = format_referral_ask_message(name, product)
            try:
                import approval_queue as aq
                aq.add_to_queue({
                    "prospect": name,
                    "channel": "sms",
                    "message": msg,
                    "generated_by": "referral_engine_14day",
                })
                db.apply_tag(pid, "referral_ask_14_sent")
                queued += 1
            except Exception:
                logger.exception(f"Failed to queue 14-day referral ask for {name}")

        elif should_send_referral_ask(close_date, ask_90_sent, window_days=90):
            msg = format_referral_ask_message(name, product)
            try:
                import approval_queue as aq
                aq.add_to_queue({
                    "prospect": name,
                    "channel": "sms",
                    "message": msg,
                    "generated_by": "referral_engine_90day",
                })
                db.apply_tag(pid, "referral_ask_90_sent")
                queued += 1
            except Exception:
                logger.exception(f"Failed to queue 90-day referral ask for {name}")

    return queued
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_referral.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add referral.py tests/test_referral.py
git commit -m "feat: add referral tracking with source attribution, top referrers, and 14/90-day ask sequences"
```

---

## Task 4: Product Intake Forms

**Files:**
- Create: `intake_forms.py`
- Create: `templates/intake_form.html`
- Modify: `dashboard.py`
- Create: `tests/test_intake_forms.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intake_forms.py`:

```python
"""Tests for product intake forms."""
import pytest
import json


@pytest.fixture
def client():
    import os
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")
    import dashboard
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def test_life_form_loads(client):
    resp = client.get("/intake-form/life?prospect=Sarah+Chen&token=test")
    assert resp.status_code == 200
    assert b"life" in resp.data.lower() or b"beneficiar" in resp.data.lower()


def test_group_benefits_form_loads(client):
    resp = client.get("/intake-form/group_benefits?prospect=Sarah+Chen&token=test")
    assert resp.status_code == 200
    assert b"employ" in resp.data.lower() or b"group" in resp.data.lower()


def test_form_submit_saves_response(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: {"id": 1, "name": n})
    saved = []
    def fake_save(d):
        saved.append(d)
    monkeypatch.setattr(db, "add_intake_form_response", fake_save)
    monkeypatch.setattr(db, "apply_tag", lambda *a, **k: True)

    resp = client.post("/api/intake-form-submit", json={
        "prospect_name": "Sarah Chen",
        "form_type": "life",
        "responses": {"beneficiaries": "Spouse", "coverage_amount": "500000", "smoker": "No"}
    })
    assert resp.status_code == 200
    assert len(saved) == 1


def test_unknown_form_type_returns_404(client):
    resp = client.get("/intake-form/unicorn?prospect=Test&token=test")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_intake_forms.py -v 2>&1 | tail -10
```

Expected: FAIL (routes not found)

- [ ] **Step 3: Add `add_intake_form_response` to `db.py`**

```python
def add_intake_form_response(data: dict) -> None:
    import json
    with get_db() as conn:
        conn.execute("""
            INSERT INTO intake_form_responses (prospect_id, form_type, responses)
            VALUES (?, ?, ?)
        """, (
            data["prospect_id"],
            data["form_type"],
            json.dumps(data.get("responses", {}))
        ))
```

- [ ] **Step 4: Create `intake_forms.py`**

```python
"""
Product-specific intake forms.
Each form type asks the right questions for that insurance line.
Form responses are saved to intake_form_responses and tagged on the prospect.
"""

import logging
from flask import Blueprint, request, jsonify, render_template, abort

import db

logger = logging.getLogger(__name__)

intake_forms_bp = Blueprint("intake_forms", __name__)

FORM_DEFINITIONS = {
    "life": {
        "title": "Life Insurance",
        "fields": [
            {"name": "coverage_amount", "label": "Desired coverage amount", "type": "text", "placeholder": "$500,000"},
            {"name": "beneficiaries", "label": "Primary beneficiary name(s)", "type": "text", "placeholder": "Spouse, children"},
            {"name": "existing_coverage", "label": "Do you have existing life insurance?", "type": "select", "options": ["No", "Yes — employer", "Yes — personal policy"]},
            {"name": "smoker", "label": "Do you currently smoke or use tobacco?", "type": "select", "options": ["No", "Yes"]},
            {"name": "health_notes", "label": "Any major health conditions we should know about?", "type": "textarea", "placeholder": "Leave blank if none"},
            {"name": "reason", "label": "Main reason for looking at life insurance", "type": "select", "options": ["Income replacement", "Mortgage protection", "Estate planning", "Business protection", "Not sure yet"]},
        ]
    },
    "disability": {
        "title": "Disability Insurance",
        "fields": [
            {"name": "occupation", "label": "Your occupation", "type": "text", "placeholder": "e.g. Dentist, Engineer"},
            {"name": "annual_income", "label": "Approximate annual income", "type": "text", "placeholder": "$120,000"},
            {"name": "group_coverage", "label": "Do you have group disability through work?", "type": "select", "options": ["No", "Yes — basic", "Yes — comprehensive"]},
            {"name": "waiting_period", "label": "Preferred waiting period before benefits start", "type": "select", "options": ["30 days", "60 days", "90 days", "120 days", "Not sure"]},
            {"name": "self_employed", "label": "Are you self-employed or a business owner?", "type": "select", "options": ["No", "Yes"]},
        ]
    },
    "group_benefits": {
        "title": "Group Benefits",
        "fields": [
            {"name": "num_employees", "label": "Number of employees", "type": "text", "placeholder": "e.g. 35"},
            {"name": "current_provider", "label": "Current group benefits provider", "type": "text", "placeholder": "Sun Life, Manulife, None"},
            {"name": "renewal_date", "label": "Current plan renewal date", "type": "text", "placeholder": "e.g. July 2026"},
            {"name": "decision_maker", "label": "Who makes the decision on benefits?", "type": "text", "placeholder": "CEO, HR Manager, Owner"},
            {"name": "pain_points", "label": "What's not working with your current plan?", "type": "textarea", "placeholder": "Cost, coverage gaps, service..."},
        ]
    },
    "critical_illness": {
        "title": "Critical Illness Insurance",
        "fields": [
            {"name": "existing_ci", "label": "Do you currently have critical illness coverage?", "type": "select", "options": ["No", "Yes — employer", "Yes — personal"]},
            {"name": "family_history", "label": "Family history of cancer, heart disease, or stroke?", "type": "select", "options": ["No known history", "Yes — cancer", "Yes — heart disease", "Yes — stroke", "Multiple"]},
            {"name": "coverage_amount", "label": "Desired lump sum coverage", "type": "text", "placeholder": "$100,000"},
            {"name": "reason", "label": "Main reason for interest in CI", "type": "select", "options": ["Income replacement during recovery", "Debt payoff", "Medical costs", "Lifestyle", "Not sure"]},
        ]
    },
    "home_auto": {
        "title": "Home & Auto Insurance",
        "fields": [
            {"name": "property_type", "label": "Property type", "type": "select", "options": ["House", "Condo", "Tenant / Renter", "Cottage", "Investment property"]},
            {"name": "current_insurer", "label": "Current home/auto insurer", "type": "text", "placeholder": "Intact, TD, Wawanesa..."},
            {"name": "renewal_date", "label": "Next renewal date", "type": "text", "placeholder": "e.g. June 2026"},
            {"name": "vehicles", "label": "Number of vehicles to insure", "type": "select", "options": ["1", "2", "3", "4+"]},
            {"name": "bundled", "label": "Are your home and auto currently bundled?", "type": "select", "options": ["Yes", "No", "Not sure"]},
        ]
    },
}


@intake_forms_bp.route("/intake-form/<form_type>")
def show_intake_form(form_type):
    if form_type not in FORM_DEFINITIONS:
        abort(404)
    form_def = FORM_DEFINITIONS[form_type]
    prospect_name = request.args.get("prospect", "")
    return render_template(
        "intake_form.html",
        form_type=form_type,
        form_def=form_def,
        prospect_name=prospect_name,
    )


@intake_forms_bp.route("/api/intake-form-submit", methods=["POST"])
def intake_form_submit():
    data = request.get_json(force=True) or {}
    prospect_name = (data.get("prospect_name") or "").strip()
    form_type = (data.get("form_type") or "").strip()
    responses = data.get("responses") or {}

    if not prospect_name or not form_type:
        return jsonify({"status": "error", "message": "prospect_name and form_type required"}), 400

    if form_type not in FORM_DEFINITIONS:
        return jsonify({"status": "error", "message": "Unknown form type"}), 400

    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        return jsonify({"status": "error", "message": "Prospect not found"}), 404

    db.add_intake_form_response({
        "prospect_id": prospect["id"],
        "form_type": form_type,
        "responses": responses,
    })

    db.apply_tag(prospect["id"], f"form_completed_{form_type}")
    logger.info(f"Intake form submitted: {prospect_name} / {form_type}")

    return jsonify({"status": "ok"})
```

- [ ] **Step 5: Create `templates/intake_form.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ form_def.title }} — Information Form</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8f7f4;min-height:100vh;display:flex;align-items:flex-start;justify-content:center;padding:32px 20px 60px}
.card{background:#fff;border-radius:20px;padding:32px 28px;max-width:480px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}
h1{font-size:22px;font-weight:700;color:#171717;margin-bottom:6px}
p.sub{font-size:14px;color:#66635f;margin-bottom:28px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;color:#374151;margin-bottom:5px;margin-top:18px}
input,select,textarea{width:100%;border:1.5px solid #e5e7eb;border-radius:10px;padding:11px 14px;font-size:15px;font-family:inherit;outline:none;transition:border-color .15s;background:#fff}
input:focus,select:focus,textarea:focus{border-color:#6366f1}
textarea{resize:vertical;min-height:80px}
button{width:100%;margin-top:28px;padding:14px;background:#6366f1;color:#fff;border:none;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer}
button:hover{background:#4f46e5}
.success{text-align:center;padding:30px 0}
.success h2{font-size:20px;font-weight:700;color:#0f9f6e;margin-bottom:8px}
.success p{color:#66635f;font-size:14px}
</style>
</head>
<body>
<div class="card">
  <div id="form-view">
    <h1>{{ form_def.title }}</h1>
    <p class="sub">Hi {{ prospect_name.split()[0] if prospect_name else 'there' }}, this form helps us find the right coverage for you. Takes about 2 minutes.</p>
    <form id="intake-form">
      {% for field in form_def.fields %}
        <label>{{ field.label }}</label>
        {% if field.type == 'select' %}
          <select name="{{ field.name }}">
            <option value="">Select...</option>
            {% for opt in field.options %}
              <option value="{{ opt }}">{{ opt }}</option>
            {% endfor %}
          </select>
        {% elif field.type == 'textarea' %}
          <textarea name="{{ field.name }}" placeholder="{{ field.get('placeholder','') }}"></textarea>
        {% else %}
          <input type="{{ field.type }}" name="{{ field.name }}" placeholder="{{ field.get('placeholder','') }}">
        {% endif %}
      {% endfor %}
      <button type="submit">Submit</button>
    </form>
  </div>
  <div id="success-view" class="success" style="display:none">
    <h2>Thank you!</h2>
    <p>We've received your information and will be in touch shortly.</p>
  </div>
</div>
<script>
document.getElementById('intake-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const responses = {};
  new FormData(form).forEach((val, key) => { responses[key] = val; });
  const resp = await fetch('/api/intake-form-submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      prospect_name: '{{ prospect_name }}',
      form_type: '{{ form_type }}',
      responses
    })
  });
  if (resp.ok) {
    document.getElementById('form-view').style.display = 'none';
    document.getElementById('success-view').style.display = 'block';
  }
});
</script>
</body>
</html>
```

- [ ] **Step 6: Register blueprint in `dashboard.py`**

```python
from intake_forms import intake_forms_bp
app.register_blueprint(intake_forms_bp)
```

- [ ] **Step 7: Run all tests**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add intake_forms.py templates/intake_form.html dashboard.py db.py tests/test_intake_forms.py
git commit -m "feat: product intake forms for life, disability, group benefits, CI, home/auto"
```

---

## Final Verification

- [ ] `python -m pytest tests/ -q 2>&1 | tail -5` — all pass
- [ ] `python -c "from tag_engine import process_tag; print('OK')"`
- [ ] `python -c "from cross_sell import run_crosssell_on_close; print('OK')"`
- [ ] `python -c "from referral import check_referral_asks; print('OK')"`
- [ ] `python -c "from intake_forms import intake_forms_bp; print('OK')"`
