"""
Webhook endpoint for external lead intake.
Handles:
  - /api/intake — Power Automate / Zapier payloads (requires X-Webhook-Secret)
  - /api/email-inbound — CloudMailin inbound email forwarding (validated by CLOUDMAILIN_SECRET)
"""

import hmac
import html as html_module
import logging
import os
import re

import db as _db
import sms_agent as _sms_agent
import sms_conversations

from flask import Blueprint, jsonify, request
from twilio.request_validator import RequestValidator

from intake import (
    process_booking, process_calendar_event, process_email_lead,
    process_website_contact, process_website_quiz, process_website_tool,
    process_email_event,
)

logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("INTAKE_WEBHOOK_SECRET", "")
CLOUDMAILIN_SECRET = os.environ.get("CLOUDMAILIN_SECRET", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")


def _validate_twilio_signature() -> bool:
    """Validate the X-Twilio-Signature header using HMAC."""
    token = os.environ.get("TWILIO_AUTH_TOKEN", "") or TWILIO_AUTH_TOKEN
    if not token:
        logger.warning("TWILIO_AUTH_TOKEN not set — rejecting all SMS webhooks")
        return False
    validator = RequestValidator(token)
    url = request.url
    # Railway (and most reverse proxies) terminate TLS and forward HTTP
    # internally.  Flask therefore sees http:// in request.url, but Twilio
    # computed its signature against the public https:// URL.  Fix the
    # scheme using the standard X-Forwarded-Proto header so the HMAC
    # matches.
    proto = request.headers.get("X-Forwarded-Proto")
    if proto == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    params = request.form.to_dict()
    signature = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, params, signature)

intake_bp = Blueprint("intake", __name__)


def _check_auth() -> bool:
    """Validate the webhook secret header."""
    if not WEBHOOK_SECRET:
        logger.warning("INTAKE_WEBHOOK_SECRET not set — rejecting all intake webhooks")
        return False
    token = request.headers.get("X-Webhook-Secret", "")
    return hmac.compare_digest(token, WEBHOOK_SECRET)


@intake_bp.route("/api/intake", methods=["POST"])
def intake_webhook():
    """Main intake webhook endpoint.
    Expects JSON body: {"type": "booking" | "email_lead", "data": { ... }}
    """
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    if request.content_length and request.content_length > 512 * 1024:
        return jsonify({"error": "Payload too large"}), 413

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    intake_type = payload.get("type", "")
    data = payload.get("data", {})

    # Otter transcripts come with "transcript" field, not "data"
    if intake_type == "otter_transcript":
        transcript = payload.get("transcript", "") or payload.get("data", {}).get("transcript", "")
        if not transcript:
            return jsonify({"error": "Missing 'transcript' field"}), 400
        try:
            result = _process_otter_transcript(transcript)
            logger.info("Intake webhook (otter_transcript): processed successfully")
            _notify_telegram("Otter transcript processed — pipeline updated.")
            return jsonify({"ok": True, "message": result})
        except Exception as e:
            logger.error(f"Otter transcript processing error: {e}")
            return jsonify({"error": "Internal processing error"}), 500

    if not data:
        return jsonify({"error": "Missing 'data' field"}), 400

    try:
        if intake_type == "booking":
            result = process_booking(data)
        elif intake_type == "calendar_event":
            result = process_calendar_event(data)
        elif intake_type == "email_lead":
            result = process_email_lead(data)
        elif intake_type == "website_contact":
            result = process_website_contact(data)
        elif intake_type == "website_quiz":
            result = process_website_quiz(data)
        elif intake_type == "website_tool":
            result = process_website_tool(data)
        elif intake_type == "email_event":
            result = process_email_event(data)
        else:
            return jsonify({"error": f"Unknown intake type: {intake_type}"}), 400

        logger.info(f"Intake webhook ({intake_type}): processed successfully")
        # Telegram alert for high-signal intake types only
        if intake_type not in ("website_tool", "email_event"):
            _notify_telegram(result)
        return jsonify({"ok": True, "message": result})

    except Exception as e:
        logger.error(f"Intake webhook error: {e}")
        return jsonify({"error": "Internal processing error"}), 500


@intake_bp.route("/api/email-inbound", methods=["POST"])
def email_inbound():
    """Receive inbound emails from CloudMailin and process as leads.
    CloudMailin sends JSON with: envelope, headers, plain, html, attachments.
    """
    # Validate CloudMailin secret (check header first, fall back to query param)
    if not CLOUDMAILIN_SECRET:
        logger.warning("CLOUDMAILIN_SECRET not set — rejecting email-inbound request")
        return jsonify({"error": "Unauthorized"}), 401
    # Accept secret from header or query param (CloudMailin uses query param by default)
    token = request.headers.get("X-CloudMailin-Secret", "") or request.args.get("secret", "")
    if not hmac.compare_digest(token, CLOUDMAILIN_SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    if request.content_length and request.content_length > 512 * 1024:
        return jsonify({"error": "Payload too large"}), 413

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Extract email fields from CloudMailin format
    headers = payload.get("headers", {})
    envelope = payload.get("envelope", {})
    subject = headers.get("Subject", "")
    sender = headers.get("From", envelope.get("from", ""))
    plain = payload.get("plain", "") or ""
    html = payload.get("html", "") or ""

    # Prefer plain text; if only HTML, strip tags to get readable text
    if plain.strip():
        body = plain
    elif html.strip():
        body = _strip_html(html)
    else:
        body = ""

    logger.info(f"Email inbound received, body_len={len(body)}")

    if not body and not subject:
        return jsonify({"error": "Empty email"}), 400

    # Detect Otter.ai transcripts by sender or content markers
    is_otter = (
        "otter.ai" in sender.lower()
        or "otter" in sender.lower()
        or _is_otter_content(subject, body)
    )

    if is_otter:
        try:
            result = _process_otter_transcript(body)
            logger.info("Email inbound: Otter transcript processed")
            _notify_telegram("Otter transcript processed — pipeline updated.")
            return jsonify({"ok": True, "message": result})
        except Exception as e:
            logger.error(f"Otter transcript (email) error: {e}")
            return jsonify({"error": "Internal processing error"}), 500

    try:
        result = process_email_lead({
            "from": sender,
            "subject": subject,
            "body": body,
        })
        logger.info(f"Email inbound: processed successfully (body_len={len(body)})")
        _notify_telegram(result)
        return jsonify({"ok": True, "message": result})
    except Exception as e:
        logger.error(f"Email inbound error: {e}")
        return jsonify({"error": "Internal processing error"}), 500


def _is_otter_content(subject: str, body: str) -> bool:
    """Detect Otter.ai transcript by content markers."""
    text = (subject + " " + body).lower()
    markers = ["abstract summary:", "action items:", "outline:", "title:"]
    return sum(1 for m in markers if m in text) >= 2


def _process_otter_transcript(transcript: str) -> str:
    """Process an Otter.ai transcript received via Zapier webhook."""
    import asyncio
    import sys
    import db

    # Store the raw transcript as an interaction
    db.add_interaction({
        "prospect": "",
        "source": "otter_transcript",
        "raw_text": transcript[:5000],
    })

    # Use the voice handler to extract and update pipeline
    try:
        from voice_handler import extract_and_update

        # Run the async function
        main_mod = sys.modules.get("__main__")
        bot_event_loop = getattr(main_mod, "bot_event_loop", None)
        if bot_event_loop:
            future = asyncio.run_coroutine_threadsafe(
                extract_and_update(transcript, source="otter_transcript"),
                bot_event_loop,
            )
            result = future.result(timeout=60)
        else:
            result = asyncio.run(extract_and_update(transcript, source="otter_transcript"))
        return result
    except Exception as e:
        logger.error(f"Otter transcript extract_and_update failed: {e}")
        return f"Transcript stored but extraction failed: {e}"


def _strip_html(html: str) -> str:
    """Convert HTML email to readable plain text."""
    # Remove style and script blocks
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert <br>, <p>, <div>, <tr> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode all HTML entities
    text = html_module.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@intake_bp.route("/api/sms-reply", methods=["POST"])
def sms_reply():
    """Receive inbound SMS replies from Twilio.
    Twilio POST fields: From, Body, MessageSid, To.
    Always returns 204 so Twilio does not retry on errors.
    """
    if not _validate_twilio_signature():
        logger.warning("SMS webhook: invalid Twilio signature — rejected")
        return "", 403

    from_number = request.form.get("From", "").strip()
    body = request.form.get("Body", "").strip()
    message_sid = request.form.get("MessageSid", "").strip()

    if not from_number or not body:
        logger.warning("SMS reply webhook: missing From or Body")
        return "", 400

    try:
        prospect = _db.get_prospect_by_phone(from_number)
        prospect_id = prospect["id"] if prospect else None
        prospect_name = prospect["name"] if prospect else ""

        # Handle opt-out keywords — update CRM, cancel sequences, don't reply
        if body.strip().lower() in sms_conversations.OPT_OUT_KEYWORDS:
            logger.info("Opt-out received from %s", from_number[-4:])
            sms_conversations.handle_opt_out(
                phone=from_number, prospect_id=prospect_id, prospect_name=prospect_name
            )
            return "", 204

        sms_conversations.log_message(
            phone=from_number,
            body=body,
            direction="inbound",
            prospect_id=prospect_id,
            prospect_name=prospect_name,
            twilio_sid=message_sid,
        )

        # Skip if prospect has previously opted out
        if sms_conversations.is_opted_out(prospect):
            logger.info("Skipping reply — prospect opted out (%s)", from_number[-4:])
            return "", 204

        # Don't auto-reply to completely unknown numbers with no prior thread
        if prospect is None:
            thread = sms_conversations.get_recent_thread(from_number, limit=1)
            if not thread:
                logger.info("SMS from unknown number %s with no prior thread — not auto-replying", from_number[-4:])
                _notify_telegram(f"📱 Unknown number texted: ...{from_number[-4:]} — \"{body[:100]}\"")
                return "", 204

        # Route to SMS agent if there's an active mission for this number
        if _sms_agent.get_active_agent(from_number):
            ok = _sms_agent.handle_reply(
                phone=from_number,
                inbound_body=body,
                prospect=prospect,
            )
            if not ok:
                logger.warning("sms_agent.handle_reply returned False for ...%s", str(from_number)[-4:])
            return "", 204

        sms_conversations.generate_reply(
            phone=from_number,
            inbound_body=body,
            prospect=prospect,
        )
    except Exception:
        logger.exception("SMS reply processing failed")

    return "", 204


def _notify_telegram(message: str):
    """Send a notification to the Telegram bot chat. Best-effort, non-blocking."""
    try:
        import asyncio
        import sys

        # bot.py runs as __main__, so globals live there (not in 'bot' module)
        main_mod = sys.modules.get("__main__")
        telegram_app = getattr(main_mod, "telegram_app", None)
        bot_event_loop = getattr(main_mod, "bot_event_loop", None)

        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not telegram_app or not bot_event_loop or not chat_id:
            return

        async def send():
            await telegram_app.bot.send_message(chat_id=chat_id, text=f"New lead intake:\n{message}")

        asyncio.run_coroutine_threadsafe(send(), bot_event_loop)
    except Exception as e:
        logger.warning(f"Could not notify Telegram: {e}")
