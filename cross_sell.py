"""
Cross-sell engine.
When a prospect closes on a product, this module recommends complementary
products and creates advisor tasks to pursue them.

Called from tag_engine.py (schedule_crosssell action) and directly after
stage changes to Closed Won.
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# ── Product Matrix ─────────────────────────────────────────────────────────────
# Maps closed product → list of recommended product names.
# Use get_crosssell_recommendations() to get full recommendation dicts.

PRODUCT_MATRIX: dict[str, list[str]] = {
    "life": ["disability", "critical_illness"],
    "disability": ["critical_illness"],
    "critical_illness": ["life"],
    "group_benefits": ["life", "disability"],
    "home_auto": ["life"],
}

# Full recommendation details keyed by (closed_product, recommended_product)
_RECOMMENDATION_DETAILS: dict[tuple[str, str], dict] = {
    ("life", "disability"): {
        "product": "disability",
        "message": "Clients with life insurance are often underinsured for income replacement. Disability coverage protects their income if they can't work.",
        "due_days": 30,
    },
    ("life", "critical_illness"): {
        "product": "critical_illness",
        "message": "Critical illness coverage complements life insurance by providing a lump sum for serious diagnoses like cancer or heart attack.",
        "due_days": 60,
    },
    ("disability", "critical_illness"): {
        "product": "critical_illness",
        "message": "With disability in place, critical illness coverage rounds out their protection against major health events.",
        "due_days": 30,
    },
    ("critical_illness", "life"): {
        "product": "life",
        "message": "If they don't have life insurance yet, now is the right time to complete their coverage.",
        "due_days": 30,
    },
    ("group_benefits", "life"): {
        "product": "life",
        "message": "Business owners with group benefits often need personal life insurance beyond what group coverage provides.",
        "due_days": 45,
    },
    ("group_benefits", "disability"): {
        "product": "disability",
        "message": "Key person disability protects the business if the owner can't work.",
        "due_days": 45,
    },
    ("home_auto", "life"): {
        "product": "life",
        "message": "Clients with home insurance often need a life insurance review to ensure their mortgage is protected.",
        "due_days": 30,
    },
}

# Cooldown: don't re-recommend the same product within this many days
CROSSSELL_COOLDOWN_DAYS = 90


def get_crosssell_recommendations(closed_product: str) -> list[dict]:
    """Return list of cross-sell recommendation dicts for a closed product."""
    products = PRODUCT_MATRIX.get(closed_product, [])
    recs = []
    for product in products:
        detail = _RECOMMENDATION_DETAILS.get((closed_product, product))
        if detail:
            recs.append(detail)
        else:
            recs.append({"product": product, "message": "", "due_days": 30})
    return recs


def is_in_cooldown(prospect: dict, product: str) -> bool:
    """
    Check if this prospect has already been pitched this product recently.
    Uses prospect tags as a proxy — a full implementation would use
    a dedicated cross_sell_history table.
    Returns False if tags cannot be retrieved (e.g. table not yet created).
    """
    prospect_id = prospect.get("id")
    if not prospect_id:
        return False
    try:
        tags = db.get_tags(prospect_id)
    except Exception:
        return False
    cooldown_tag = f"crosssell_{product}_pitched"
    return cooldown_tag in tags


def format_crosssell_task(prospect: dict, recommendation: dict) -> str:
    """Format the task description for a cross-sell recommendation."""
    name = prospect.get("name", "this prospect")
    product = recommendation.get("product", "")
    message = recommendation.get("message", "")
    return f"Cross-sell {product} to {name}: {message}"


def run_crosssell_for_prospect(prospect: dict, closed_product: str, tenant_id: int = 1) -> None:
    """Create cross-sell tasks for a prospect who just closed on a product."""
    prospect_id = prospect.get("id")
    if not prospect_id:
        return

    recommendations = get_crosssell_recommendations(closed_product)
    for rec in recommendations:
        product = rec["product"]
        if is_in_cooldown(prospect, product):
            logger.info("Prospect %d already pitched %s — skipping", prospect_id, product)
            continue

        due_date = (datetime.now() + timedelta(days=rec.get("due_days", 30))).strftime("%Y-%m-%d")
        task_description = format_crosssell_task(prospect, rec)

        try:
            db.add_task(
                {
                    "title": f"Cross-sell opportunity: {product}",
                    "prospect": prospect.get("name", ""),
                    "due_date": due_date,
                    "notes": task_description,
                    "created_by": "cross_sell_engine",
                },
                tenant_id=tenant_id,
            )
            # Mark as pitched to prevent re-pitching during cooldown
            try:
                db.apply_tag(prospect_id, f"crosssell_{product}_pitched")
            except Exception as tag_err:
                logger.warning(
                    "Could not apply cooldown tag for prospect %d, product %s: %s",
                    prospect_id,
                    product,
                    tag_err,
                )
            logger.info("Cross-sell task created: %s for prospect %d", product, prospect_id)
        except Exception as e:
            logger.error(
                "Failed to create cross-sell task for prospect %d, product %s: %s",
                prospect_id,
                product,
                e,
            )


def run_crosssell_on_close(prospect: dict, closed_product: str, tenant_id: int = 1) -> None:
    """
    Entry point called when a deal closes.
    Determines which cross-sell products apply and creates tasks.
    """
    try:
        run_crosssell_for_prospect(prospect, closed_product, tenant_id)
    except Exception as e:
        logger.error("run_crosssell_on_close failed for prospect %s: %s", prospect.get("name"), e)
