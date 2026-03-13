"""
Voice note handler for the Telegram bot.
Handles: voice message download, Whisper transcription, AI extraction of prospect data, and pipeline updates.
"""

import json
import logging
import os
import tempfile
from datetime import datetime

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def build_extraction_prompt(transcript: str) -> str:
    """Build the prompt for extracting prospect data from a voice note transcript."""
    return f"""You are a sales assistant for Marc, a financial advisor at Co-operators in London, Ontario.

Analyze this voice note transcript and extract ALL prospects mentioned (including referrals).

TRANSCRIPT:
{transcript}

Return a JSON object with this exact structure:
{{
  "prospects": [
    {{
      "name": "Full Name",
      "product": "Life Insurance / Disability Insurance / Wealth Management / Commercial Insurance / Auto Insurance / Home Insurance / etc.",
      "notes": "Key details from the conversation",
      "action_items": "Specific next steps with dates if mentioned",
      "source": "voice_note or referral (if this person was mentioned as a referral)",
      "phone": "",
      "email": "",
      "priority": "Hot / Warm / Cold (based on interest level)",
      "stage": "New Lead / Contacted / Discovery Call / Needs Analysis (based on context)"
    }}
  ]
}}

Rules:
- Extract ALL people mentioned, including referrals ("his brother", "her friend", etc.)
- For referrals, set source to "referral" and include who referred them in notes
- If no specific name is given for a referral, use a placeholder like "John's Brother"
- Guess stage from context: just met = "Discovery Call", wants quote = "Needs Analysis", initial mention = "New Lead"
- Return ONLY valid JSON, no other text"""


def parse_extraction_response(raw: str) -> list[dict]:
    """Parse the AI extraction response into a list of prospect dicts."""
    if not raw:
        logger.warning("AI returned empty/null response")
        return []

    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rstrip()
            if text.endswith("```"):
                text = text[:-3].rstrip()
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        data = json.loads(text)

        # Handle {"prospects": [...]} format
        if isinstance(data, dict):
            prospects = data.get("prospects", [])
            if not isinstance(prospects, list):
                return []
            return prospects

        # Handle direct list format: [{"name": "...", ...}]
        if isinstance(data, list):
            return data

        return []
    except (json.JSONDecodeError, AttributeError, KeyError) as e:
        logger.warning(f"Failed to parse extraction response ({e}): {raw[:300]}")
        return []


async def transcribe_voice(file_path: str) -> str:
    """Transcribe a voice note file using OpenAI Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return transcript.text


async def extract_and_update(transcript: str, bot=None, source: str = "voice_note", coworker: str = "") -> str:
    """Extract prospect data from transcript, update pipeline, return summary."""
    prompt = build_extraction_prompt(transcript)

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_completion_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content
        logger.info(f"AI extraction raw response ({len(raw) if raw else 0} chars): {(raw or '')[:300]}")
        prospects = parse_extraction_response(raw)
    except Exception as e:
        logger.error(f"AI extraction failed: {e}")
        return "AI extraction failed — transcript was saved.\n\nTry again or add manually with /add."

    source_label = "Otter transcript" if source == "otter_transcript" else "voice note"
    tag = "[Otter]" if source == "otter_transcript" else "[Voice]"
    if coworker:
        tag = f"[Voice from {coworker}]"

    if not prospects:
        return f"Could not extract prospect data from your {source_label}.\n\nTry again or add manually with /add."

    results = []
    for p in prospects:
        name = p.get("name", "").strip()
        if not name:
            continue

        existing = db.get_prospect_by_name(name)
        if existing:
            old_notes = existing.get("notes", "")
            new_notes = p.get("notes", "")
            action_items = p.get("action_items", "")
            combined = f"{old_notes} | {tag} {new_notes}"
            if action_items:
                combined += f" | Action: {action_items}"

            updates = {"notes": combined.strip(" |")}
            if p.get("stage") and p["stage"] != "New Lead":
                updates["stage"] = p["stage"]
            if p.get("priority"):
                updates["priority"] = p["priority"]

            db.update_prospect(name, updates)
            results.append(f"Updated {existing['name']} — added {source_label} details")
        else:
            prospect_source = f"Referral from {coworker}" if coworker else p.get("source", source)
            notes = p.get("notes", "")
            if coworker:
                notes = f"{notes} | Added by {coworker}" if notes else f"Added by {coworker}"
            db.add_prospect({
                "name": name,
                "phone": p.get("phone", ""),
                "email": p.get("email", ""),
                "source": prospect_source,
                "priority": p.get("priority", "Warm"),
                "stage": p.get("stage", "New Lead"),
                "product": p.get("product", ""),
                "notes": notes,
            })
            # Score and schedule follow-up for new prospects
            from intake import _score_and_schedule
            _score_and_schedule(name)
            results.append(f"New prospect: {name} — {p.get('product', '?')}")

        db.add_interaction({
            "prospect": name,
            "source": source,
            "raw_text": transcript,
            "summary": p.get("notes", ""),
            "action_items": p.get("action_items", ""),
        })

        db.add_activity({
            "prospect": name,
            "action": f"{source_label.title()} processed",
            "outcome": p.get("notes", ""),
            "next_step": p.get("action_items", ""),
        })

        # Extract client intelligence into Memory Engine
        try:
            import memory_engine
            prospect_obj = db.get_prospect_by_name(name)
            if prospect_obj:
                memory_engine.extract_facts_from_interaction(
                    prospect_name=name,
                    prospect_id=prospect_obj["id"],
                    interaction_text=transcript,
                    source=f"{source}_{datetime.now().strftime('%Y-%m-%d')}",
                )
        except Exception:
            logger.exception("Memory extraction failed for %s (non-blocking)", name)

        # Auto-draft follow-up email
        try:
            import follow_up as fu
            activity_summary = f"Voice note ({source}): {transcript[:300]}"
            fu_draft = fu.generate_follow_up_draft(
                prospect_name=name,
                activity_summary=activity_summary,
                activity_type=f"Voice note ({source})",
            )
            if fu_draft:
                logger.info("Follow-up draft generated for %s (queue #%s)", name, fu_draft["queue_id"])
        except Exception:
            logger.exception("Follow-up draft failed for %s (non-blocking)", name)

        # Check for urgency signals in transcript
        urgency_keywords = ["urgent", "asap", "emergency", "right away", "immediately", "time sensitive", "deadline"]
        if any(kw in transcript.lower() for kw in urgency_keywords):
            logger.info("URGENCY detected in voice note for %s", name)

    summary = f"{source_label.title()} processed:\n" + "\n".join(f"  {r}" for r in results)
    return summary


async def handle_voice_message(update, context):
    """Telegram handler for voice messages. Admin gets full processing, coworkers can add leads."""
    admin_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    # Only grant admin if TELEGRAM_CHAT_ID is configured and matches the sender.
    # When TELEGRAM_CHAT_ID is unset, no one gets admin privileges.
    is_admin = bool(admin_id) and str(update.effective_chat.id) == str(admin_id)
    coworker_name = "" if is_admin else (update.effective_user.first_name or "Coworker")

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    chat_id = update.effective_chat.id
    logger.info(f"Voice message received from {'admin' if is_admin else coworker_name}, duration: {voice.duration}s")

    await update.message.reply_text("Got your voice note, processing...")

    tmp_path = None
    try:
        file = await voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        transcript = await transcribe_voice(tmp_path)
        logger.info(f"Transcription: {transcript[:200]}")

        if not transcript.strip():
            await update.message.reply_text("Couldn't make out what you said. Try again?")
            return

        # Save raw transcript before extraction so nothing is lost on failure
        db.add_interaction({
            "prospect": "",
            "source": f"voice_note_raw{'_' + coworker_name if coworker_name else ''}",
            "raw_text": transcript,
        })

        result = await extract_and_update(transcript, coworker=coworker_name)
        await update.message.reply_text(result)

        # Notify Marc when a coworker adds leads via voice
        if coworker_name and admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"New lead added by {coworker_name} (voice note):\n{result}"
                )
            except Exception as e:
                logger.warning(f"Could not notify admin: {e}")

    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await update.message.reply_text("Error processing voice note. Please try again.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
