import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_process_booking_payload():
    from intake import process_booking
    result = process_booking({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-1234",
        "service": "Financial Planning Consultation",
        "start_time": "2026-03-15T14:00:00",
        "notes": "Interested in home insurance",
    })
    assert "Sarah Chen" in result
    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["email"] == "sarah@example.com"
    assert prospect["source"] == "Outlook Booking"


def test_process_booking_duplicate():
    from intake import process_booking
    db.add_prospect({"name": "Sarah Chen", "source": "Manual", "stage": "Contacted"})
    result = process_booking({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "service": "Review Meeting",
        "start_time": "2026-03-15T14:00:00",
    })
    assert "Updated" in result or "already" in result.lower() or "Sarah Chen" in result


def test_process_email_lead(monkeypatch):
    from intake import process_email_lead
    import intake

    class MockMessage:
        content = '{"name": "Mike Johnson", "phone": "519-555-5678", "email": "", "product": "Life Insurance", "notes": "35, married, tech company, referred by neighbor", "priority": "Warm", "source": "Referral", "stage": "New Lead"}'

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    class MockCompletions:
        def create(self, **kwargs):
            return MockResponse()

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    monkeypatch.setattr(intake, "client", MockClient())

    result = process_email_lead({
        "from": "colleague@cooperators.ca",
        "subject": "Referral: Mike Johnson",
        "body": "Hi Marc, Mike Johnson is looking for life insurance.",
    })
    assert "Mike Johnson" in result
    prospect = db.get_prospect_by_name("Mike Johnson")
    assert prospect is not None
    assert prospect["source"] == "Referral"


def test_process_email_lead_minimal(monkeypatch):
    from intake import process_email_lead
    import intake

    class MockMessage:
        content = '{"name": "Jane Doe", "phone": "", "email": "", "product": "Auto Insurance", "notes": "Wants auto insurance quote", "priority": "Warm", "source": "Email Lead", "stage": "New Lead"}'

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    class MockCompletions:
        def create(self, **kwargs):
            return MockResponse()

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    monkeypatch.setattr(intake, "client", MockClient())

    result = process_email_lead({
        "body": "New lead: Jane Doe, wants auto insurance quote",
    })
    assert "Jane" in result


def test_process_calendar_event_with_attendees():
    from intake import process_calendar_event
    result = process_calendar_event({
        "subject": "Discovery Call - Life Insurance",
        "start_time": "2026-03-20T10:00:00",
        "end_time": "2026-03-20T11:00:00",
        "location": "Co-operators Office",
        "body": "Initial meeting to discuss life insurance needs",
        "attendees": [
            {"name": "Tom Wilson", "email": "tom@example.com"},
            {"name": "Lisa Wilson", "email": "lisa@example.com"},
        ],
    })
    assert "Tom Wilson" in result
    assert "Lisa Wilson" in result
    tom = db.get_prospect_by_name("Tom Wilson")
    assert tom is not None
    assert tom["email"] == "tom@example.com"
    assert tom["source"] == "Calendar Event"
    lisa = db.get_prospect_by_name("Lisa Wilson")
    assert lisa is not None
    meetings = db.read_meetings()
    assert any(m["prospect"] == "Tom Wilson" for m in meetings)


def test_process_calendar_event_no_attendees():
    from intake import process_calendar_event
    result = process_calendar_event({
        "subject": "Team Standup",
        "start_time": "2026-03-20T09:00:00",
    })
    assert "logged" in result.lower()


def test_process_calendar_event_skips_marc():
    from intake import process_calendar_event
    result = process_calendar_event({
        "subject": "Client Call",
        "start_time": "2026-03-20T10:00:00",
        "attendees": [
            {"name": "Marc Pineault", "email": "MarcPineault@cooperators.onmicrosoft.com"},
            {"name": "Bob Smith", "email": "bob@example.com"},
        ],
    })
    assert "Bob Smith" in result
    assert db.get_prospect_by_name("Marc Pineault") is None
    assert db.get_prospect_by_name("Bob Smith") is not None
