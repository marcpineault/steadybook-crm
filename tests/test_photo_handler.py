"""Tests for business card photo handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_parse_card_response_extracts_fields():
    from photo_handler import parse_card_response
    gpt_response = """Name: Sarah Chen
Title: CFO
Company: Maple Ridge Construction
Email: sarah@mapleridge.com
Phone: 519-555-1234"""
    result = parse_card_response(gpt_response)
    assert result["name"] == "Sarah Chen"
    assert result["email"] == "sarah@mapleridge.com"
    assert result["phone"] == "519-555-1234"
    assert result["company"] == "Maple Ridge Construction"


def test_parse_card_response_handles_missing_fields():
    from photo_handler import parse_card_response
    result = parse_card_response("Name: Bob Smith")
    assert result["name"] == "Bob Smith"
    assert result.get("email", "") == ""
    assert result.get("phone", "") == ""


def test_parse_card_response_empty_returns_empty_dict():
    from photo_handler import parse_card_response
    result = parse_card_response("")
    assert result == {}


def test_format_confirmation_message_includes_fields():
    from photo_handler import format_confirmation_message
    card = {"name": "Sarah Chen", "email": "sarah@mapleridge.com", "phone": "519-555-1234", "company": "Maple Ridge Construction"}
    msg = format_confirmation_message(card)
    assert "Sarah Chen" in msg
    assert "sarah@mapleridge.com" in msg
    assert "Save to pipeline" in msg or "save" in msg.lower()


def test_format_confirmation_message_skips_empty_fields():
    from photo_handler import format_confirmation_message
    card = {"name": "Bob Smith", "email": "", "phone": "", "company": ""}
    msg = format_confirmation_message(card)
    assert "Bob Smith" in msg
    assert "email" not in msg.lower() or "@" not in msg
