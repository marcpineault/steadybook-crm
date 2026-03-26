"""
Business card photo handler.
When a user sends a photo in Telegram, attempts to extract contact information
using GPT-4o Vision. Shows extracted data to user for confirmation before
saving to the pipeline.
"""
import logging
import os
import base64
from io import BytesIO

from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import db

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

CARD_PROMPT = """Extract contact information from this business card image.
Return ONLY these fields, one per line, exactly as shown (skip any field not visible):
Name: [full name]
Title: [job title]
Company: [company name]
Email: [email address]
Phone: [phone number]
Website: [website]
LinkedIn: [linkedin url or handle]

If this is not a business card, respond with: NOT_A_CARD"""

FIELD_ORDER = ["name", "title", "company", "email", "phone", "website", "linkedin"]
FIELD_LABELS = {
    "name": "Name",
    "title": "Title",
    "company": "Company",
    "email": "Email",
    "phone": "Phone",
    "website": "Website",
    "linkedin": "LinkedIn",
}


def parse_card_response(text: str) -> dict:
    """Parse GPT response into a dict of contact fields."""
    if not text or text.strip() == "NOT_A_CARD":
        return {}
    result = {}
    for line in text.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key in FIELD_LABELS and value:
            result[key] = value
    return result


def format_confirmation_message(card: dict) -> str:
    """Format extracted card data for Telegram confirmation message."""
    lines = ["📇 *Business Card Extracted*\n"]
    for field in FIELD_ORDER:
        value = card.get(field, "")
        if value:
            lines.append(f"*{FIELD_LABELS[field]}:* {value}")
    lines.append("\nSave to pipeline?")
    return "\n".join(lines)


async def extract_card_from_image(image_bytes: bytes) -> dict:
    """Send image to GPT-4o Vision and parse the card fields."""
    b64 = base64.b64encode(image_bytes).decode()
    response = await _client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CARD_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        max_tokens=300,
    )
    raw = response.choices[0].message.content or ""
    return parse_card_response(raw)


async def save_card_to_pipeline(card: dict, tenant_id: int = 1) -> int:
    """Save extracted card as a new prospect. Returns prospect_id."""
    notes_parts = []
    if card.get("title"):
        notes_parts.append(f"Title: {card['title']}")
    if card.get("website"):
        notes_parts.append(f"Website: {card['website']}")
    if card.get("linkedin"):
        notes_parts.append(f"LinkedIn: {card['linkedin']}")
    notes = "\n".join(notes_parts)

    db.add_prospect(
        {
            "name": card.get("name", "Unknown"),
            "phone": card.get("phone", ""),
            "email": card.get("email", ""),
            "company": card.get("company", ""),
            "source": "business_card",
            "stage": "New Lead",
            "priority": "Warm",
            "notes": notes,
        },
        tenant_id=tenant_id,
    )

    prospect = db.get_prospect_by_name(card.get("name", "Unknown"))
    if not prospect:
        return 0

    prospect_id = prospect["id"]
    db.apply_tag(prospect_id, "new_lead")
    db.apply_tag(prospect_id, "source_card")
    db.queue_enrichment(prospect_id)
    return prospect_id


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photo message — attempt business card extraction."""
    if not update.message or not update.message.photo:
        return

    await update.message.reply_text("📇 Reading business card...")

    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()

        card = await extract_card_from_image(image_bytes)

        if not card or not card.get("name"):
            await update.message.reply_text(
                "I couldn't read a business card from that photo. "
                "Try a clearer, well-lit shot of the card."
            )
            return

        context.user_data["pending_card"] = card
        msg = format_confirmation_message(card)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Save to Pipeline", callback_data="card_confirm"),
                InlineKeyboardButton("❌ Discard", callback_data="card_discard"),
            ]
        ])
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        logger.error("Photo handler error: %s", e)
        await update.message.reply_text("Something went wrong reading that photo. Please try again.")


async def handle_card_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button response to card confirmation."""
    query = update.callback_query
    await query.answer()

    if query.data == "card_discard":
        context.user_data.pop("pending_card", None)
        await query.edit_message_text("Card discarded.")
        return

    card = context.user_data.pop("pending_card", None)
    if not card:
        await query.edit_message_text("No card data found. Please send the photo again.")
        return

    tenant_id = context.bot_data.get("tenant_id", 1)
    try:
        prospect_id = await save_card_to_pipeline(card, tenant_id)
        await query.edit_message_text(
            f"✅ *{card.get('name')}* saved to pipeline!\n"
            f"Lead ID: #{prospect_id}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Card save error: %s", e)
        await query.edit_message_text("Failed to save. Please try again.")
