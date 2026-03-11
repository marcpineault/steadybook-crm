"""
Webhook endpoint for external lead intake.
Flask blueprint that receives payloads from Power Automate (Outlook Bookings) and Zapier (email forwarding, Otter transcripts in Phase 2).
All requests must include X-Webhook-Secret header matching INTAKE_WEBHOOK_SECRET env var.
"""

import hmac
import logging
import os

from flask import Blueprint, jsonify, request

from intake import process_booking, process_email_lead

logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("INTAKE_WEBHOOK_SECRET", "")

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

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    intake_type = payload.get("type", "")
    data = payload.get("data", {})

    if not data:
        return jsonify({"error": "Missing 'data' field"}), 400

    try:
        if intake_type == "booking":
            result = process_booking(data)
        elif intake_type == "email_lead":
            result = process_email_lead(data)
        else:
            return jsonify({"error": f"Unknown intake type: {intake_type}"}), 400

        logger.info(f"Intake webhook ({intake_type}): {result}")
        _notify_telegram(result)
        return jsonify({"ok": True, "message": result})

    except Exception as e:
        logger.error(f"Intake webhook error: {e}")
        return jsonify({"error": str(e)[:200]}), 500


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
