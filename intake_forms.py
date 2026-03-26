"""
Product-specific intake form routes.
Each form collects information relevant to a specific insurance product.
Form responses are stored in intake_form_responses table as JSON.
"""

import json
import logging

from flask import Blueprint, request, jsonify, render_template, abort

import db

logger = logging.getLogger(__name__)

intake_forms_bp = Blueprint("intake_forms", __name__)

# ── Form Definitions ──────────────────────────────────────────────────────────
# Each form type defines: title, description, and list of field groups with fields.
# Field: {name, label, type, required, options (for select/radio)}

FORM_DEFINITIONS = {
    "life": {
        "title": "Life Insurance Application",
        "description": "Help us understand your coverage needs.",
        "groups": [
            {
                "heading": "Personal Information",
                "fields": [
                    {"name": "dob", "label": "Date of Birth", "type": "date", "required": True},
                    {"name": "smoker", "label": "Smoker?", "type": "select", "required": True,
                     "options": ["No", "Yes - quit within 12 months", "Yes - current"]},
                    {"name": "health_conditions", "label": "Any significant health conditions?", "type": "textarea", "required": False},
                ]
            },
            {
                "heading": "Coverage Details",
                "fields": [
                    {"name": "coverage_amount", "label": "Coverage Amount Needed", "type": "select", "required": True,
                     "options": ["$250,000", "$500,000", "$750,000", "$1,000,000", "$1,500,000", "$2,000,000+"]},
                    {"name": "term_length", "label": "Term Length", "type": "select", "required": True,
                     "options": ["10 years", "20 years", "30 years", "Permanent"]},
                    {"name": "beneficiaries", "label": "Primary Beneficiary (name)", "type": "text", "required": False},
                ]
            },
            {
                "heading": "Financial Picture",
                "fields": [
                    {"name": "mortgage_balance", "label": "Mortgage Balance (approx.)", "type": "text", "required": False},
                    {"name": "income", "label": "Annual Household Income", "type": "select", "required": False,
                     "options": ["Under $75K", "$75K–$150K", "$150K–$250K", "$250K–$500K", "Over $500K"]},
                ]
            }
        ]
    },
    "disability": {
        "title": "Disability Insurance Application",
        "description": "Protect your income if you can't work.",
        "groups": [
            {
                "heading": "Occupation",
                "fields": [
                    {"name": "occupation", "label": "Occupation / Job Title", "type": "text", "required": True},
                    {"name": "employer", "label": "Employer", "type": "text", "required": False},
                    {"name": "self_employed", "label": "Are you self-employed?", "type": "select", "required": True,
                     "options": ["No", "Yes"]},
                ]
            },
            {
                "heading": "Income & Coverage",
                "fields": [
                    {"name": "monthly_income", "label": "Gross Monthly Income", "type": "text", "required": True},
                    {"name": "group_coverage", "label": "Do you have group disability through work?", "type": "select", "required": True,
                     "options": ["No", "Yes - short-term only", "Yes - long-term", "Yes - both"]},
                    {"name": "waiting_period", "label": "Preferred Waiting Period", "type": "select", "required": False,
                     "options": ["30 days", "60 days", "90 days", "120 days"]},
                ]
            }
        ]
    },
    "critical_illness": {
        "title": "Critical Illness Application",
        "description": "Lump-sum protection for serious diagnoses.",
        "groups": [
            {
                "heading": "Health Background",
                "fields": [
                    {"name": "family_history", "label": "Family history of cancer, heart disease, or stroke?", "type": "select", "required": True,
                     "options": ["No", "Yes - one parent", "Yes - both parents", "Yes - sibling"]},
                    {"name": "current_health", "label": "Current health status", "type": "select", "required": True,
                     "options": ["Excellent", "Good", "Fair", "Managing a condition"]},
                ]
            },
            {
                "heading": "Coverage",
                "fields": [
                    {"name": "coverage_amount", "label": "Coverage Amount", "type": "select", "required": True,
                     "options": ["$50,000", "$100,000", "$250,000", "$500,000"]},
                ]
            }
        ]
    },
    "group_benefits": {
        "title": "Group Benefits Questionnaire",
        "description": "Design a benefits plan for your team.",
        "groups": [
            {
                "heading": "Business Details",
                "fields": [
                    {"name": "business_name", "label": "Business Name", "type": "text", "required": True},
                    {"name": "num_employees", "label": "Number of Full-Time Employees", "type": "select", "required": True,
                     "options": ["1–5", "6–15", "16–30", "31–50", "51–100", "100+"]},
                    {"name": "industry", "label": "Industry", "type": "text", "required": False},
                ]
            },
            {
                "heading": "Coverage Goals",
                "fields": [
                    {"name": "current_plan", "label": "Do you have an existing group plan?", "type": "select", "required": True,
                     "options": ["No", "Yes - looking to switch", "Yes - looking to expand"]},
                    {"name": "priorities", "label": "Coverage priorities", "type": "textarea", "required": False},
                ]
            }
        ]
    },
    "home_auto": {
        "title": "Home & Auto Insurance Review",
        "description": "Make sure your property and vehicle coverage is right.",
        "groups": [
            {
                "heading": "Home",
                "fields": [
                    {"name": "home_type", "label": "Property Type", "type": "select", "required": True,
                     "options": ["Owned home", "Condo", "Rental/Tenant", "Vacation/Cottage", "Not applicable"]},
                    {"name": "home_value", "label": "Estimated Home Value", "type": "text", "required": False},
                    {"name": "current_insurer_home", "label": "Current Home Insurer", "type": "text", "required": False},
                ]
            },
            {
                "heading": "Auto",
                "fields": [
                    {"name": "num_vehicles", "label": "Number of Vehicles", "type": "select", "required": True,
                     "options": ["0", "1", "2", "3+"]},
                    {"name": "current_insurer_auto", "label": "Current Auto Insurer", "type": "text", "required": False},
                    {"name": "accidents_claims", "label": "Any accidents or claims in last 3 years?", "type": "select", "required": True,
                     "options": ["No", "Yes - 1", "Yes - 2+"]},
                ]
            }
        ]
    },
}


@intake_forms_bp.route("/intake-form/<form_type>")
def intake_form(form_type):
    """Render the intake form for a specific product type."""
    if form_type not in FORM_DEFINITIONS:
        abort(404)
    form_def = FORM_DEFINITIONS[form_type]
    prospect_id = request.args.get("prospect_id", "")
    return render_template(
        "intake_form.html",
        form=form_def,
        form_type=form_type,
        prospect_id=prospect_id,
    )


@intake_forms_bp.route("/api/intake-form-submit", methods=["POST"])
def intake_form_submit():
    """Handle intake form submission."""
    data = request.get_json(silent=True) or {}
    form_type = str(data.get("form_type", "")).strip()
    prospect_id_raw = data.get("prospect_id")
    responses = data.get("responses", {})

    if form_type not in FORM_DEFINITIONS:
        return jsonify({"error": "Unknown form type"}), 400

    if not isinstance(responses, dict) or not responses:
        return jsonify({"error": "Responses required"}), 400

    try:
        prospect_id = int(prospect_id_raw) if prospect_id_raw else None
    except (TypeError, ValueError):
        prospect_id = None

    if not prospect_id:
        return jsonify({"error": "prospect_id required"}), 400

    try:
        db.add_intake_form_response(
            prospect_id=prospect_id,
            form_type=form_type,
            responses=json.dumps(responses),
        )
        return jsonify({"status": "ok"}), 201
    except Exception as e:
        logger.error("Intake form submit error: %s", e)
        return jsonify({"error": "Save failed"}), 500
