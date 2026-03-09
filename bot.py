import os
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
import openpyxl
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
PIPELINE_PATH = os.environ.get("PIPELINE_PATH", "pipeline.xlsx")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Pipeline Excel helpers ──

DATA_START = 5  # row where data begins
MAX_ROWS = 80   # max rows to scan

PIPELINE_COLS = {
    "name": 1, "phone": 2, "email": 3, "source": 4,
    "priority": 5, "stage": 6, "product": 7,
    "aum": 8, "revenue": 9, "first_contact": 10,
    "next_followup": 11, "days_open": 12, "notes": 13,
}

LOG_COLS = {
    "date": 1, "prospect": 2, "action": 3,
    "outcome": 4, "next_step": 5, "notes": 6,
}


def read_pipeline():
    """Read all prospects from pipeline."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]
    prospects = []
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue
        prospect = {"row": r}
        for field, col in PIPELINE_COLS.items():
            val = ws.cell(row=r, column=col).value
            if val is not None:
                prospect[field] = str(val)
            else:
                prospect[field] = ""
        prospects.append(prospect)
    wb.close()
    return prospects


def add_prospect(data: dict) -> str:
    """Add a new prospect to the first empty row."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    # Find first empty row
    target_row = None
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Pipeline is full! No empty rows available."

    field_map = {
        "name": 1, "phone": 2, "email": 3, "source": 4,
        "priority": 5, "stage": 6, "product": 7,
        "aum": 8, "revenue": 9, "first_contact": 10,
        "next_followup": 11, "notes": 13,
    }

    for field, col in field_map.items():
        if field in data and data[field]:
            val = data[field]
            if field == "aum" or field == "revenue":
                try:
                    val = float(str(val).replace("$", "").replace(",", ""))
                except ValueError:
                    pass
            ws.cell(row=target_row, column=col, value=val)

    # Default first_contact to today if not set
    if not data.get("first_contact"):
        ws.cell(row=target_row, column=10, value=date.today().strftime("%Y-%m-%d"))

    # Default stage to New Lead if not set
    if not data.get("stage"):
        ws.cell(row=target_row, column=6, value="New Lead")

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Added {data.get('name', 'prospect')} to pipeline (row {target_row})."


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by name (partial match)."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    target_row = None
    matched_name = None
    name_lower = name.lower()

    for r in range(DATA_START, DATA_START + MAX_ROWS):
        cell_val = ws.cell(row=r, column=1).value
        if cell_val and name_lower in str(cell_val).lower():
            target_row = r
            matched_name = cell_val
            break

    if not target_row:
        wb.close()
        return f"Could not find prospect matching '{name}'."

    field_map = {
        "name": 1, "phone": 2, "email": 3, "source": 4,
        "priority": 5, "stage": 6, "product": 7,
        "aum": 8, "revenue": 9, "first_contact": 10,
        "next_followup": 11, "notes": 13,
    }

    changes = []
    for field, value in updates.items():
        if field in field_map and value:
            col = field_map[field]
            if field in ("aum", "revenue"):
                try:
                    value = float(str(value).replace("$", "").replace(",", ""))
                except ValueError:
                    pass
            ws.cell(row=target_row, column=col, value=value)
            changes.append(f"{field} → {value}")

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Updated {matched_name}: {', '.join(changes)}"


def add_activity(data: dict) -> str:
    """Add entry to Activity Log sheet."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Activity Log"]

    target_row = None
    for r in range(3, 103):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Activity log is full!"

    field_map = {"date": 1, "prospect": 2, "action": 3, "outcome": 4, "next_step": 5, "notes": 6}

    for field, col in field_map.items():
        if field in data and data[field]:
            ws.cell(row=target_row, column=col, value=data[field])

    if not data.get("date"):
        ws.cell(row=target_row, column=1, value=date.today().strftime("%Y-%m-%d"))

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Logged activity for {data.get('prospect', 'unknown')}."


def get_overdue():
    """Get prospects with overdue follow-ups."""
    prospects = read_pipeline()
    today = date.today()
    overdue = []

    for p in prospects:
        if p["next_followup"] and p["stage"] not in ("Closed-Won", "Closed-Lost", ""):
            try:
                fu_date = datetime.strptime(p["next_followup"].split(" ")[0], "%Y-%m-%d").date()
                if fu_date < today:
                    days_late = (today - fu_date).days
                    overdue.append(f"• {p['name']} — {days_late} days overdue (was {p['next_followup']})")
            except (ValueError, IndexError):
                pass

    if not overdue:
        return "No overdue follow-ups. You're on top of it."
    return f"Overdue follow-ups ({len(overdue)}):\n" + "\n".join(overdue)


def get_pipeline_summary():
    """Get a summary of the current pipeline."""
    prospects = read_pipeline()

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]

    total_aum = 0
    total_rev = 0
    for p in active:
        try:
            total_aum += float(p["aum"].replace("$", "").replace(",", "")) if p["aum"] else 0
        except ValueError:
            pass
        try:
            total_rev += float(p["revenue"].replace("$", "").replace(",", "")) if p["revenue"] else 0
        except ValueError:
            pass

    hot = len([p for p in active if p["priority"].lower() == "hot"])

    stages = {}
    for p in active:
        s = p["stage"]
        stages[s] = stages.get(s, 0) + 1

    stage_breakdown = "\n".join(f"  • {s}: {c}" for s, c in sorted(stages.items()))
    overdue_info = get_overdue()

    return (
        f"Pipeline Summary:\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Active deals: {len(active)}\n"
        f"Pipeline value: ${total_aum:,.0f}\n"
        f"Est. revenue: ${total_rev:,.0f}\n"
        f"Hot leads: {hot}\n"
        f"Deals won: {len(won)}\n\n"
        f"By stage:\n{stage_breakdown}\n\n"
        f"{overdue_info}"
    )


# ── Available tools for Claude ──

TOOLS = [
    {
        "name": "read_pipeline",
        "description": "Read all prospects from the sales pipeline. Returns a list of all prospects with their details.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_prospect",
        "description": "Add a new prospect to the pipeline. Use 'New Lead' as default stage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prospect's full name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "source": {"type": "string", "enum": ["Referral", "Website", "Social Media", "Seminar", "Cold Outreach", "LinkedIn", "Podcast", "Networking", "Centre of Influence", "Other"]},
                "priority": {"type": "string", "enum": ["Hot", "Warm", "Cold"]},
                "stage": {"type": "string", "enum": ["New Lead", "Contacted", "Discovery Call", "Needs Analysis", "Plan Presentation", "Proposal Sent", "Negotiation", "Closed-Won", "Closed-Lost", "Nurture"]},
                "product": {"type": "string", "enum": ["Life Insurance", "Wealth Management", "Life Insurance + Wealth", "Disability Insurance", "Critical Illness", "Group Benefits", "Estate Planning", "Other"]},
                "aum": {"type": "string", "description": "Estimated premium or AUM value"},
                "revenue": {"type": "string", "description": "Estimated annual revenue from this client"},
                "next_followup": {"type": "string", "description": "Next follow-up date in YYYY-MM-DD format"},
                "notes": {"type": "string", "description": "Any notes about the prospect"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_prospect",
        "description": "Update an existing prospect's information. Search by name (partial match works).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prospect name to search for (partial match)"},
                "updates": {
                    "type": "object",
                    "description": "Fields to update",
                    "properties": {
                        "stage": {"type": "string"},
                        "priority": {"type": "string"},
                        "next_followup": {"type": "string"},
                        "notes": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "product": {"type": "string"},
                        "aum": {"type": "string"},
                        "revenue": {"type": "string"},
                        "source": {"type": "string"},
                    },
                },
            },
            "required": ["name", "updates"],
        },
    },
    {
        "name": "add_activity",
        "description": "Log an activity/touchpoint in the Activity Log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect": {"type": "string", "description": "Prospect name"},
                "action": {"type": "string", "description": "What was done (e.g., Phone Call, Email, Meeting)"},
                "outcome": {"type": "string", "description": "Result of the activity"},
                "next_step": {"type": "string", "description": "What to do next"},
                "notes": {"type": "string", "description": "Additional notes"},
            },
            "required": ["prospect", "action"],
        },
    },
    {
        "name": "get_overdue",
        "description": "Get all prospects with overdue follow-up dates.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_pipeline_summary",
        "description": "Get a full summary of the current pipeline: active deals, value, stages, overdue items.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_FUNCTIONS = {
    "read_pipeline": lambda _: json.dumps(read_pipeline(), default=str),
    "add_prospect": lambda args: add_prospect(args),
    "update_prospect": lambda args: update_prospect(args["name"], args["updates"]),
    "add_activity": lambda args: add_activity(args),
    "get_overdue": lambda _: get_overdue(),
    "get_pipeline_summary": lambda _: get_pipeline_summary(),
}

SYSTEM_PROMPT = """You are Calm Money Pipeline Bot — a sales assistant for Matthew, a financial planner in London, Ontario who sells life insurance and wealth management.

Your job is to manage his sales pipeline via an Excel spreadsheet. You receive natural language messages and use tools to read/write the pipeline.

Key rules:
- Be concise. This is a text chat, not an email. Short replies.
- When adding prospects, default stage to "New Lead" and first_contact to today unless specified.
- When the user says "move X to Y" — update the prospect's stage.
- When the user says "mark X as hot/warm/cold" — update priority.
- When dates are relative ("friday", "next week", "tomorrow"), calculate the actual YYYY-MM-DD date. Today is """ + date.today().strftime("%Y-%m-%d") + """.
- For "log:" messages, add to the Activity Log AND update the prospect's next_followup if a next step involves a date.
- For "pipeline", "update", "summary", "how's it going" — give a pipeline summary.
- For "overdue", "who's late", "follow-ups" — check overdue follow-ups.
- After any write action, confirm what you did in 1-2 lines.
- Use $ for dollar amounts, keep it casual and friendly.
- If something is ambiguous, make your best guess and confirm what you did so Matthew can correct if needed.
"""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming Telegram messages."""
    user_msg = update.message.text
    if not user_msg:
        return

    logger.info(f"Received: {user_msg}")

    try:
        # Call Claude with tools
        messages = [{"role": "user", "content": user_msg}]

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Process tool calls in a loop
        while response.stop_reason == "tool_use":
            tool_results = []
            assistant_content = response.content

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    logger.info(f"Tool call: {tool_name}({json.dumps(tool_input)})")

                    func = TOOL_FUNCTIONS.get(tool_name)
                    if func:
                        result = func(tool_input)
                    else:
                        result = f"Unknown tool: {tool_name}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Extract final text response
        reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                reply += block.text

        if not reply:
            reply = "Done! (no message returned)"

        await update.message.reply_text(reply)
        logger.info(f"Replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the pipeline Excel file to the user."""
    try:
        if Path(PIPELINE_PATH).exists():
            await update.message.reply_document(
                document=open(PIPELINE_PATH, "rb"),
                filename=f"CalmMoney_Pipeline_{date.today().strftime('%Y-%m-%d')}.xlsx",
                caption="Here's your current pipeline file."
            )
        else:
            await update.message.reply_text("Pipeline file not found.")
    except Exception as e:
        await update.message.reply_text(f"Error sending file: {str(e)[:200]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded Excel files — replace the pipeline."""
    doc = update.message.document
    if not doc.file_name.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("That's not an Excel file. Send me an .xlsx file to update the pipeline.")
        return

    try:
        file = await doc.get_file()
        await file.download_to_drive(PIPELINE_PATH)

        # Verify it's valid
        wb = openpyxl.load_workbook(PIPELINE_PATH)
        sheets = wb.sheetnames
        wb.close()

        await update.message.reply_text(
            f"Pipeline updated from your file.\n"
            f"Sheets: {', '.join(sheets)}\n"
            f"All changes are live now."
        )
        logger.info(f"Pipeline replaced from uploaded file: {doc.file_name}")
    except Exception as e:
        await update.message.reply_text(f"Error processing file: {str(e)[:200]}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey Matthew! I'm your Calm Money pipeline bot.\n\n"
        "Just text me naturally:\n"
        "• \"add John Smith, $300K wealth, hot, referral\"\n"
        "• \"move Sarah to discovery call\"\n"
        "• \"log: called Michael, no answer\"\n"
        "• \"who's overdue?\"\n"
        "• \"pipeline update\"\n"
        "• /export — download your pipeline Excel\n"
        "• Send me an Excel file to update the pipeline\n\n"
        "I'll handle the rest."
    )


def main():
    # Initialize pipeline file if it doesn't exist
    if not Path(PIPELINE_PATH).exists():
        logger.info(f"Pipeline file not found at {PIPELINE_PATH}. Please provide one.")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
