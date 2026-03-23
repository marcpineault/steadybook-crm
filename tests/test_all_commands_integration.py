"""
Comprehensive integration test — simulates every bot command by calling handlers
directly with mocked Telegram objects. No real API calls are made.

Tests both admin and non-admin flows, verifying:
- Commands respond without errors
- Usage messages appear when no args given
- Admin-only commands reject non-admin users
- Data flows correctly to/from the database
"""
import asyncio
import importlib
import json
import os
import re

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    """Set up a clean DB and mock env vars for every test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("DASHBOARD_API_KEY", "fake-dash-key")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15195550000")

    import db
    importlib.reload(db)
    db.init_db()
    yield


def _make_update(text, chat_id="12345", first_name="Marc"):
    """Build a mock Telegram Update with message.text set."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = int(chat_id)
    update.effective_user = MagicMock()
    update.effective_user.first_name = first_name
    update.callback_query = None
    return update


def _make_context(*args):
    """Build a mock context with .args."""
    ctx = MagicMock()
    ctx.args = list(args)
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _get_reply(update):
    """Extract the text from the last reply_text call."""
    calls = update.message.reply_text.call_args_list
    if not calls:
        return None
    return calls[-1].args[0] if calls[-1].args else calls[-1].kwargs.get("text")


def _all_replies(update):
    """Extract all reply texts."""
    return [c.args[0] if c.args else c.kwargs.get("text") for c in update.message.reply_text.call_args_list]


# ── Mock LLM ──────────────────────────────────────────────────────────

def _mock_openai():
    """Patch OpenAI so no real API calls are made."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message = MagicMock()
    mock_response.choices[0].message.content = "Mock LLM response"
    mock_response.choices[0].message.tool_calls = None

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


@pytest.fixture
def bot():
    """Import bot module with mocked OpenAI client."""
    import bot as bot_mod
    original_client = bot_mod.client
    bot_mod.client = _mock_openai()
    yield bot_mod
    bot_mod.client = original_client


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC COMMANDS
# ═══════════════════════════════════════════════════════════════════════

class TestQuoteCommand:
    def test_quote_no_args_shows_usage(self, bot):
        update = _make_update("/quote")
        _run(bot.cmd_quote(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_quote_with_args_calls_llm(self, bot):
        update = _make_update("/quote disability office worker 50k income 3k benefit")
        _run(bot.cmd_quote(update, _make_context()))
        assert "Mock LLM response" in _get_reply(update)

    def test_quote_non_admin_works(self, bot):
        update = _make_update("/quote term 35 male nonsmoker", chat_id="99999")
        _run(bot.cmd_quote(update, _make_context()))
        assert _get_reply(update) is not None


class TestAddCommand:
    def test_add_no_args_admin(self, bot):
        update = _make_update("/add")
        _run(bot.cmd_add(update, _make_context()))
        assert _get_reply(update) is not None

    def test_add_with_data_admin(self, bot):
        update = _make_update("/add John Smith, interested in life insurance")
        _run(bot.cmd_add(update, _make_context()))
        assert _get_reply(update) is not None

    def test_add_no_args_coworker(self, bot):
        update = _make_update("/add", chat_id="99999", first_name="Sarah")
        _run(bot.cmd_add(update, _make_context()))
        assert "Add a prospect" in _get_reply(update)

    def test_add_with_data_coworker(self, bot):
        update = _make_update("/add Bob Brown, auto insurance", chat_id="99999", first_name="Sarah")
        _run(bot.cmd_add(update, _make_context()))
        assert _get_reply(update) is not None


class TestStatusCommand:
    def test_status_no_args(self, bot):
        update = _make_update("/status")
        _run(bot.cmd_status(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_status_not_found(self, bot):
        update = _make_update("/status Nobody")
        _run(bot.cmd_status(update, _make_context()))
        assert "No prospect found" in _get_reply(update)

    def test_status_found(self, bot):
        import db
        db.add_prospect({"name": "Jane Doe", "stage": "Discovery Call", "priority": "Hot", "product": "Life"})
        update = _make_update("/status Jane Doe")
        _run(bot.cmd_status(update, _make_context()))
        reply = _get_reply(update)
        assert "Jane Doe" in reply
        assert "Discovery Call" in reply


class TestMsgCommand:
    def test_msg_as_admin_rejected(self, bot):
        update = _make_update("/msg Hey")
        _run(bot.cmd_msg(update, _make_context()))
        assert "You're Marc" in _get_reply(update)

    def test_msg_no_args_coworker(self, bot):
        update = _make_update("/msg", chat_id="99999", first_name="Sarah")
        _run(bot.cmd_msg(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_msg_coworker_sends(self, bot):
        with patch.object(bot, "message_marc", return_value="Message sent to Marc!"):
            update = _make_update("/msg Johnson file ready", chat_id="99999", first_name="Sarah")
            _run(bot.cmd_msg(update, _make_context()))
            assert _get_reply(update) is not None


# ═══════════════════════════════════════════════════════════════════════
# ADMIN-ONLY GATING
# ═══════════════════════════════════════════════════════════════════════

class TestAdminGating:
    @pytest.mark.parametrize("cmd_name", [
        "cmd_call", "cmd_priority", "cmd_merge", "cmd_lead",
        "cmd_memory", "cmd_confirm", "cmd_forget", "cmd_drafts",
        "cmd_voice", "cmd_calendar", "cmd_trust", "cmd_campaign",
        "cmd_nurture", "cmd_coldcall", "cmd_outcomes", "cmd_clearsms",
        "agent_command",
    ])
    def test_non_admin_blocked(self, bot, cmd_name):
        handler = getattr(bot, cmd_name)
        update = _make_update(f"/{cmd_name}", chat_id="99999")
        _run(handler(update, _make_context()))
        assert "You have access to" in _get_reply(update)


# ═══════════════════════════════════════════════════════════════════════
# ADMIN COMMANDS
# ═══════════════════════════════════════════════════════════════════════

class TestCallCommand:
    def test_no_args(self, bot):
        update = _make_update("/call")
        _run(bot.cmd_call(update, _make_context()))
        assert "Quick call log" in _get_reply(update)

    def test_with_args(self, bot):
        update = _make_update("/call John Smith - voicemail")
        _run(bot.cmd_call(update, _make_context()))
        assert _get_reply(update) is not None

    def test_log_alias(self, bot):
        update = _make_update("/log Mike - no answer")
        _run(bot.cmd_call(update, _make_context()))
        assert _get_reply(update) is not None


class TestTodoCommand:
    def test_no_args(self, bot):
        update = _make_update("/todo")
        _run(bot.cmd_todo(update, _make_context()))
        assert "Create a task" in _get_reply(update)

    def test_td_alias(self, bot):
        update = _make_update("/td")
        _run(bot.cmd_todo(update, _make_context()))
        assert "Create a task" in _get_reply(update)

    def test_with_text_creates_task(self, bot):
        # Set up mock to simulate tool call → final response
        mock_msg1 = MagicMock()
        mock_msg1.tool_calls = [MagicMock()]
        mock_msg1.tool_calls[0].function.name = "create_task"
        mock_msg1.tool_calls[0].function.arguments = json.dumps({
            "title": "Send brochure to John", "prospect": "John", "due_date": "2026-03-25",
        })
        mock_msg1.tool_calls[0].id = "call_123"

        mock_msg2 = MagicMock()
        mock_msg2.tool_calls = None
        mock_msg2.content = "Task created: Send brochure to John, due March 25."

        bot.client.chat.completions.create.side_effect = [
            MagicMock(choices=[MagicMock(message=mock_msg1)]),
            MagicMock(choices=[MagicMock(message=mock_msg2)]),
        ]

        update = _make_update("/todo send John the brochure by Friday")
        _run(bot.cmd_todo(update, _make_context()))
        assert _get_reply(update) is not None

        # Reset
        bot.client.chat.completions.create.side_effect = None
        bot.client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Mock LLM response", tool_calls=None))]
        )


class TestTasksCommand:
    def test_empty(self, bot):
        update = _make_update("/tasks")
        _run(bot.cmd_tasks(update, _make_context()))
        assert "No pending tasks" in _get_reply(update)

    def test_with_data(self, bot):
        import db
        db.add_task({"title": "Test task", "prospect": "", "due_date": "2026-03-25", "created_by": "12345"})
        update = _make_update("/tasks")
        _run(bot.cmd_tasks(update, _make_context()))
        assert "Test task" in _get_reply(update)

    def test_non_admin_sees_own(self, bot):
        import db
        db.add_task({"title": "Admin task", "prospect": "", "due_date": "2026-03-25", "created_by": "12345", "assigned_to": "12345"})
        db.add_task({"title": "Coworker task", "prospect": "", "due_date": "2026-03-25", "created_by": "99999", "assigned_to": "99999"})
        update = _make_update("/tasks", chat_id="99999")
        _run(bot.cmd_tasks(update, _make_context()))
        reply = _get_reply(update)
        assert "Coworker task" in reply


class TestDoneCommand:
    def test_no_args(self, bot):
        update = _make_update("/done")
        _run(bot.cmd_done(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_invalid_id(self, bot):
        update = _make_update("/done abc")
        _run(bot.cmd_done(update, _make_context()))
        assert "task ID number" in _get_reply(update)

    def test_valid(self, bot):
        import db
        task = db.add_task({"title": "Finish report", "prospect": "", "due_date": "2026-03-25", "created_by": "12345"})
        tid = task["id"]
        update = _make_update(f"/done {tid}")
        _run(bot.cmd_done(update, _make_context()))
        assert _get_reply(update) is not None


class TestPriorityCommand:
    def test_empty_pipeline(self, bot):
        update = _make_update("/priority")
        _run(bot.cmd_priority(update, _make_context()))
        reply = _get_reply(update)
        assert "No active deals" in reply or "CALL LIST" in reply

    def test_with_prospects(self, bot):
        import db
        db.add_prospect({"name": "Alice", "stage": "Discovery Call", "priority": "Hot"})
        db.add_prospect({"name": "Bob", "stage": "Needs Analysis", "priority": "Warm"})
        update = _make_update("/priority")
        _run(bot.cmd_priority(update, _make_context()))
        assert _get_reply(update) is not None


class TestMergeCommand:
    def test_no_args(self, bot):
        update = _make_update("/merge")
        _run(bot.cmd_merge(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_with_names(self, bot):
        import db
        db.add_prospect({"name": "John Smith", "stage": "New Lead"})
        db.add_prospect({"name": "John S", "stage": "New Lead"})
        update = _make_update("/merge John Smith into John S")
        _run(bot.cmd_merge(update, _make_context("John", "Smith", "into", "John", "S")))
        assert _get_reply(update) is not None


class TestLeadCommand:
    def test_no_args(self, bot):
        update = _make_update("/lead")
        _run(bot.cmd_lead(update, _make_context()))
        assert "Paste a lead" in _get_reply(update)

    def test_with_data(self, bot):
        update = _make_update("/lead Mike Johnson, 35, life insurance")
        with patch("intake.process_email_lead", return_value="Lead processed: Mike Johnson"):
            _run(bot.cmd_lead(update, _make_context()))
        replies = _all_replies(update)
        assert any("Processing" in r for r in replies)


class TestMemoryCommand:
    def test_no_args_shows_review(self, bot):
        update = _make_update("/memory")
        _run(bot.cmd_memory(update, _make_context()))
        reply = _get_reply(update)
        assert "No facts" in reply or "FACTS" in reply

    def test_not_found(self, bot):
        update = _make_update("/memory Unknown Person")
        _run(bot.cmd_memory(update, _make_context("Unknown", "Person")))
        assert "No prospect found" in _get_reply(update)

    def test_found(self, bot):
        import db
        db.add_prospect({"name": "Test Person", "stage": "New Lead"})
        update = _make_update("/memory Test Person")
        _run(bot.cmd_memory(update, _make_context("Test", "Person")))
        reply = _get_reply(update)
        assert "MEMORY" in reply or "Test Person" in reply


class TestConfirmForget:
    def test_confirm_no_args(self, bot):
        update = _make_update("/confirm")
        _run(bot.cmd_confirm(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_confirm_invalid(self, bot):
        update = _make_update("/confirm abc")
        _run(bot.cmd_confirm(update, _make_context("abc")))
        assert "Invalid" in _get_reply(update)

    def test_forget_no_args(self, bot):
        update = _make_update("/forget")
        _run(bot.cmd_forget(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_forget_invalid(self, bot):
        update = _make_update("/forget xyz")
        _run(bot.cmd_forget(update, _make_context("xyz")))
        assert "Invalid" in _get_reply(update)


class TestDraftsCommand:
    def test_empty(self, bot):
        update = _make_update("/drafts")
        _run(bot.cmd_drafts(update, _make_context()))
        assert "No pending drafts" in _get_reply(update)

    def test_with_data(self, bot):
        import approval_queue
        approval_queue.add_draft(
            draft_type="follow_up", channel="sms", content="Hey, following up!",
            context="Follow-up with John", prospect_id=None,
        )
        update = _make_update("/drafts")
        _run(bot.cmd_drafts(update, _make_context()))
        assert "DRAFT" in _get_reply(update)


class TestVoiceCommand:
    def test_no_args(self, bot):
        update = _make_update("/voice")
        _run(bot.cmd_voice(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_list_empty(self, bot):
        update = _make_update("/voice list")
        _run(bot.cmd_voice(update, _make_context("list")))
        assert "No brand voice" in _get_reply(update)

    def test_add_and_list(self, bot):
        update_add = _make_update("/voice add linkedin educational Great post")
        _run(bot.cmd_voice(update_add, _make_context("add", "linkedin", "educational", "Great", "post")))
        assert "Added brand voice" in _get_reply(update_add)

        update_list = _make_update("/voice list")
        _run(bot.cmd_voice(update_list, _make_context("list")))
        assert "linkedin" in _get_reply(update_list)


class TestCalendarCommand:
    def test_empty(self, bot):
        update = _make_update("/calendar")
        _run(bot.cmd_calendar(update, _make_context()))
        reply = _get_reply(update)
        assert "No upcoming" in reply or "MARKET CALENDAR" in reply

    def test_add_event(self, bot):
        update = _make_update("/calendar add 2026-04-01 Rate Decision")
        _run(bot.cmd_calendar(update, _make_context("add", "2026-04-01", "Rate", "Decision")))
        assert "Added" in _get_reply(update)

    def test_news_alias(self, bot):
        update = _make_update("/news")
        _run(bot.cmd_calendar(update, _make_context()))
        assert _get_reply(update) is not None


class TestTrustCommand:
    def test_view(self, bot):
        update = _make_update("/trust")
        _run(bot.cmd_trust(update, _make_context()))
        assert "trust level" in _get_reply(update).lower()

    def test_set_valid(self, bot):
        update = _make_update("/trust 2")
        _run(bot.cmd_trust(update, _make_context("2")))
        assert "Trust level set to 2" in _get_reply(update)

    def test_set_invalid(self, bot):
        update = _make_update("/trust 5")
        _run(bot.cmd_trust(update, _make_context("5")))
        assert "must be 1, 2, or 3" in _get_reply(update)

    def test_set_not_number(self, bot):
        update = _make_update("/trust abc")
        _run(bot.cmd_trust(update, _make_context("abc")))
        assert "Usage" in _get_reply(update)


class TestCampaignCommand:
    def test_no_args(self, bot):
        update = _make_update("/campaign")
        _run(bot.cmd_campaign(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_new(self, bot):
        update = _make_update("/campaign new Spring Checkup")
        _run(bot.cmd_campaign(update, _make_context("new", "Spring", "Checkup")))
        assert "created" in _get_reply(update).lower()

    def test_list_empty(self, bot):
        update = _make_update("/campaign list")
        _run(bot.cmd_campaign(update, _make_context("list")))
        assert "No campaigns" in _get_reply(update)

    def test_list_with_data(self, bot):
        import campaigns as camp
        camp.create_campaign(name="Test", description="Test campaign")
        update = _make_update("/campaign list")
        _run(bot.cmd_campaign(update, _make_context("list")))
        assert "Test" in _get_reply(update)


class TestNurtureCommand:
    def test_no_active(self, bot):
        update = _make_update("/nurture")
        _run(bot.cmd_nurture(update, _make_context()))
        assert "No active" in _get_reply(update)

    def test_start(self, bot):
        update = _make_update("/nurture start John Smith")
        _run(bot.cmd_nurture(update, _make_context("start", "John", "Smith")))
        assert "started" in _get_reply(update).lower()

    def test_stop_not_found(self, bot):
        update = _make_update("/nurture stop 999")
        _run(bot.cmd_nurture(update, _make_context("stop", "999")))
        assert "not found" in _get_reply(update)


class TestColdcallCommand:
    def test_no_args(self, bot):
        update = _make_update("/coldcall")
        _run(bot.cmd_coldcall(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_no_phone(self, bot):
        update = _make_update("/coldcall nophone")
        _run(bot.cmd_coldcall(update, _make_context("nophone")))
        assert "phone" in _get_reply(update).lower()

    def test_with_phone(self, bot):
        update = _make_update("/coldcall +15196001234 Sarah Jones")
        with patch.object(bot, "draft_cold_outreach", return_value={
            "prospect": {"id": 1, "name": "Sarah Jones"},
            "display_name": "Sarah Jones",
            "phone": "+15196001234",
            "content": "Hey Sarah, Marc from Co-operators here!",
            "queue_id": 1,
            "is_new_prospect": True,
            "has_prior_thread": False,
        }):
            _run(bot.cmd_coldcall(update, _make_context("+15196001234", "Sarah", "Jones")))
        replies = _all_replies(update)
        assert any("Sarah" in r for r in replies)

    def test_cc_alias(self, bot):
        update = _make_update("/cc")
        _run(bot.cmd_coldcall(update, _make_context()))
        assert "Usage" in _get_reply(update)


class TestClearSmsCommand:
    def test_no_args(self, bot):
        update = _make_update("/clearsms")
        _run(bot.cmd_clearsms(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_bad_phone(self, bot):
        update = _make_update("/clearsms 123")
        _run(bot.cmd_clearsms(update, _make_context("123")))
        assert "Couldn't parse" in _get_reply(update)

    def test_valid(self, bot):
        update = _make_update("/clearsms +15196001234")
        _run(bot.cmd_clearsms(update, _make_context("+15196001234")))
        assert "Cleared" in _get_reply(update)


class TestAgentCommand:
    def test_no_args(self, bot):
        update = _make_update("/agent")
        _run(bot.agent_command(update, _make_context()))
        assert "Usage" in _get_reply(update)

    def test_bad_format(self, bot):
        update = _make_update("/agent bad input no dash")
        _run(bot.agent_command(update, _make_context("bad", "input", "no", "dash")))
        assert "Usage" in _get_reply(update)

    def test_valid_create(self, bot):
        update = _make_update("/agent +15191234567 John Smith — book a discovery call")
        with patch("sms_agent.create_mission", return_value={"id": 1, "status": "pending_opener"}):
            _run(bot.agent_command(update, _make_context(
                "+15191234567", "John", "Smith", "—", "book", "a", "discovery", "call"
            )))
        replies = _all_replies(update)
        assert any("mission" in r.lower() for r in replies)

    def test_resume_no_id(self, bot):
        update = _make_update("/agent resume")
        _run(bot.agent_command(update, _make_context("resume")))
        assert "Usage" in _get_reply(update)

    def test_resume_valid(self, bot):
        update = _make_update("/agent resume 42")
        with patch("sms_agent.resume_mission", return_value="Mission #42 resumed"):
            _run(bot.agent_command(update, _make_context("resume", "42")))
        assert "42" in _get_reply(update)


class TestOutcomesCommand:
    def test_empty(self, bot):
        update = _make_update("/outcomes")
        _run(bot.cmd_outcomes(update, _make_context()))
        reply = _get_reply(update)
        assert "No outcomes" in reply or reply is not None


# ═══════════════════════════════════════════════════════════════════════
# FREE-FORM MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════

class TestHandleMessage:
    def test_admin_freeform(self, bot):
        update = _make_update("What's my pipeline looking like?")
        _run(bot.handle_message(update, _make_context()))
        assert _get_reply(update) is not None

    def test_coworker_freeform(self, bot):
        update = _make_update("Can you look up John Smith?", chat_id="99999", first_name="Sarah")
        _run(bot.handle_message(update, _make_context()))
        assert _get_reply(update) is not None

    def test_empty_message_ignored(self, bot):
        update = _make_update("")
        update.message.text = None
        _run(bot.handle_message(update, _make_context()))
        assert update.message.reply_text.call_count == 0


# ═══════════════════════════════════════════════════════════════════════
# DATABASE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════

class TestDatabaseFlows:
    def test_trust_persists(self, bot):
        update = _make_update("/trust 3")
        _run(bot.cmd_trust(update, _make_context("3")))
        assert bot.get_trust_level() == 3

        update2 = _make_update("/trust 1")
        _run(bot.cmd_trust(update2, _make_context("1")))
        assert bot.get_trust_level() == 1

    def test_status_after_add(self, bot):
        import db
        db.add_prospect({"name": "Integration Test", "stage": "New Lead", "priority": "Hot", "product": "Life Insurance"})
        update = _make_update("/status Integration Test")
        _run(bot.cmd_status(update, _make_context()))
        reply = _get_reply(update)
        assert "Integration Test" in reply
        assert "Hot" in reply

    def test_task_lifecycle(self, bot):
        import db
        task = db.add_task({"title": "Lifecycle test", "prospect": "", "due_date": "2026-04-01", "created_by": "12345"})
        tid = task["id"]

        # List
        update_list = _make_update("/tasks")
        _run(bot.cmd_tasks(update_list, _make_context()))
        assert "Lifecycle test" in _get_reply(update_list)

        # Complete
        update_done = _make_update(f"/done {tid}")
        _run(bot.cmd_done(update_done, _make_context()))
        assert _get_reply(update_done) is not None

        # Verify gone
        update_list2 = _make_update("/tasks")
        _run(bot.cmd_tasks(update_list2, _make_context()))
        assert "No pending tasks" in _get_reply(update_list2)


# ═══════════════════════════════════════════════════════════════════════
# SMS AGENT MODULE
# ═══════════════════════════════════════════════════════════════════════

class TestSmsAgentModule:
    def test_create_mission(self):
        import sms_agent
        importlib.reload(sms_agent)

        # Mock for SMS agent (returns plain text opener)
        opener_response = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Hey John, Marc here!"))]
        )
        # Mock for compliance (returns JSON with passed=True)
        compliance_response = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"passed": true, "issues": []}'))]
        )

        mock_oai = MagicMock()
        mock_oai.chat.completions.create.side_effect = [opener_response, compliance_response]

        mock_compliance_oai = MagicMock()
        mock_compliance_oai.chat.completions.create.return_value = compliance_response

        with patch("sms_agent.openai_client", mock_oai), \
             patch("compliance.openai_client", mock_compliance_oai):
            mission = sms_agent.create_mission(
                phone="+15195551234", prospect_name="John Smith", objective="book a discovery call"
            )
        assert mission is not None
        assert mission["status"] in ("pending_opener", "pending_approval", "active")

    def test_get_active_agent_none(self):
        import sms_agent
        importlib.reload(sms_agent)
        assert sms_agent.get_active_agent("+15195559999") is None

    def test_classify_status(self):
        import sms_agent
        importlib.reload(sms_agent)
        thread = [
            {"direction": "outbound", "body": "Hey John, want to connect?"},
            {"direction": "inbound", "body": "Sure, sounds good"},
        ]
        with patch("sms_agent.openai_client") as mock_oai:
            mock_oai.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="ongoing"))]
            )
            status = sms_agent.classify_mission_status(thread, "book a call")
        assert status in ("ongoing", "success", "cold", "needs_marc")


# ═══════════════════════════════════════════════════════════════════════
# SMS CONVERSATIONS MODULE
# ═══════════════════════════════════════════════════════════════════════

class TestSmsConversations:
    def test_was_recently_contacted_false(self):
        import sms_conversations
        importlib.reload(sms_conversations)
        assert sms_conversations.was_recently_contacted("+15195559999", hours=4) is False

    def test_get_recent_thread_empty(self):
        import sms_conversations
        importlib.reload(sms_conversations)
        assert sms_conversations.get_recent_thread("+15195559999", limit=5) == []

    def test_log_and_retrieve(self):
        import sms_conversations
        importlib.reload(sms_conversations)
        sms_conversations.log_message("+15195551111", "Hello!", "outbound")
        sms_conversations.log_message("+15195551111", "Hi there!", "inbound")
        thread = sms_conversations.get_recent_thread("+15195551111", limit=5)
        assert len(thread) == 2
        assert thread[0]["body"] == "Hello!"


# ═══════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

class TestHelperFunctions:
    def test_is_admin_true(self, bot):
        update = _make_update("/test", chat_id="12345")
        assert bot._is_admin(update) is True

    def test_is_admin_false(self, bot):
        update = _make_update("/test", chat_id="99999")
        assert bot._is_admin(update) is False

    def test_get_trust_default(self, bot):
        assert bot.get_trust_level() == 1

    def test_set_trust(self, bot):
        bot.set_trust_level(3)
        assert bot.get_trust_level() == 3

    def test_otter_detection(self, bot):
        otter = "Title: Notes\nAbstract summary: Stuff\nOutline:\n1. Things\nAction items:\n- Do stuff"
        assert bot._is_otter_transcript(otter) is True
        assert bot._is_otter_transcript("Regular message") is False
