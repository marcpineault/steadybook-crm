import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_booking_nurture"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import booking_nurture

import pytz
ET = pytz.timezone("America/Toronto")


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect():
    db.add_prospect({
        "name": "John Doe", "stage": "New Lead", "priority": "Warm",
        "product": "Life Insurance", "phone": "5198001234",
    })
    with db.get_db() as conn:
        return conn.execute("SELECT id FROM prospects WHERE name = 'John Doe'").fetchone()[0]


FUTURE_MEETING = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def test_create_sequence_inserts_three_rows():
    pid = _seed_prospect()
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
        meeting_type="Consultation",
        product="Life Insurance",
    )
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM booking_nurture_sequences WHERE prospect_id = ?", (pid,)
        ).fetchall()
    assert len(rows) == 3


def test_touch1_scheduled_for_now():
    pid = _seed_prospect()
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT scheduled_for FROM booking_nurture_sequences WHERE prospect_id = ? AND touch_number = 1",
            (pid,)
        ).fetchone()

    scheduled_for_str = row[0]
    scheduled_for = datetime.strptime(scheduled_for_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    diff = abs((scheduled_for - now_utc).total_seconds())
    assert diff < 300, f"Touch 1 scheduled_for should be within 5 minutes of now, got diff={diff}s"


def test_touch2_scheduled_for_9am_day_before():
    pid = _seed_prospect()
    meeting_dt_str = FUTURE_MEETING
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=meeting_dt_str,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT scheduled_for FROM booking_nurture_sequences WHERE prospect_id = ? AND touch_number = 2",
            (pid,)
        ).fetchone()

    scheduled_for_str = row[0]
    scheduled_for_utc = datetime.strptime(scheduled_for_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    scheduled_for_et = scheduled_for_utc.astimezone(ET)

    # Compute expected: day before meeting at 9 AM ET
    meeting_dt = datetime.fromisoformat(meeting_dt_str.replace("Z", "+00:00"))
    meeting_dt_utc = meeting_dt.astimezone(timezone.utc)
    meeting_day_et = meeting_dt_utc.astimezone(ET).date()
    from datetime import timedelta as td
    day_before_et_date = meeting_day_et - td(days=1)
    expected_et = ET.localize(datetime(day_before_et_date.year, day_before_et_date.month, day_before_et_date.day, 9, 0, 0))

    assert scheduled_for_et.hour == 9
    assert scheduled_for_et.minute == 0
    assert scheduled_for_et.date() == expected_et.date()


def test_touch3_scheduled_for_two_hours_before():
    pid = _seed_prospect()
    meeting_dt_str = FUTURE_MEETING
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=meeting_dt_str,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT scheduled_for FROM booking_nurture_sequences WHERE prospect_id = ? AND touch_number = 3",
            (pid,)
        ).fetchone()

    scheduled_for_str = row[0]
    scheduled_for_utc = datetime.strptime(scheduled_for_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    meeting_dt = datetime.fromisoformat(meeting_dt_str.replace("Z", "+00:00"))
    meeting_dt_utc = meeting_dt.astimezone(timezone.utc)
    expected = meeting_dt_utc - timedelta(hours=2)

    diff = abs((scheduled_for_utc - expected).total_seconds())
    assert diff < 60, f"Touch 3 should be 2 hours before meeting, got diff={diff}s"


def test_get_due_touches_returns_overdue():
    pid = _seed_prospect()
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    # Set touch 2 and 3 to past datetime to make them overdue
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE booking_nurture_sequences SET scheduled_for = ? WHERE prospect_id = ? AND touch_number IN (2, 3)",
            (past, pid),
        )

    due = booking_nurture.get_due_touches()
    # Touch 1 is already due (scheduled for now), plus 2 and 3 we set to past
    assert len(due) >= 1
    prospect_ids = [r["prospect_id"] for r in due]
    assert pid in prospect_ids


def test_cancel_sequence():
    pid = _seed_prospect()
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    booking_nurture.cancel_sequence(pid)

    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT status FROM booking_nurture_sequences WHERE prospect_id = ?", (pid,)
        ).fetchall()

    assert len(rows) == 3
    for row in rows:
        assert row[0] == "cancelled"


def test_cancel_on_rebook():
    pid = _seed_prospect()
    # First booking
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )
    # Second booking (rebook) — should cancel first 3 and create 3 new
    new_meeting = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=new_meeting,
        meeting_date="2026-03-24",
        meeting_time="2:00 PM",
    )

    with db.get_db() as conn:
        all_rows = conn.execute(
            "SELECT id, status FROM booking_nurture_sequences WHERE prospect_id = ? ORDER BY id",
            (pid,)
        ).fetchall()

    assert len(all_rows) == 6
    # First 3 should be cancelled
    for row in all_rows[:3]:
        assert row[1] == "cancelled", f"Row {row[0]} should be cancelled, got {row[1]}"
    # Last 3 should be queued
    for row in all_rows[3:]:
        assert row[1] == "queued", f"Row {row[0]} should be queued, got {row[1]}"


@patch("booking_nurture.openai_client")
def test_generate_touch(mock_client):
    pid = _seed_prospect()
    booking_nurture.create_sequence(
        prospect_name="John Doe",
        prospect_id=pid,
        phone="5198001234",
        meeting_datetime_str=FUTURE_MEETING,
        meeting_date="2026-03-22",
        meeting_time="10:00 AM",
    )

    # Get touch 1 row
    with db.get_db() as conn:
        touch_row = dict(conn.execute(
            "SELECT * FROM booking_nurture_sequences WHERE prospect_id = ? AND touch_number = 1",
            (pid,)
        ).fetchone())

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hey John, looking forward to our call! - Marc"
    mock_client.chat.completions.create.return_value = mock_response

    result = booking_nurture.generate_touch(touch_row)

    assert result is not None
    assert "content" in result
    assert "queue_id" in result

    # Verify DB updated to draft_sent with queue_id
    with db.get_db() as conn:
        updated = conn.execute(
            "SELECT status, queue_id FROM booking_nurture_sequences WHERE id = ?",
            (touch_row["id"],)
        ).fetchone()

    assert updated[0] == "draft_sent"
    assert updated[1] is not None
