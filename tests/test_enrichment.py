"""Tests for the prospect enrichment engine."""
import pytest
from unittest.mock import patch, MagicMock


def test_parse_google_result_extracts_linkedin():
    from enrichment import parse_google_result
    snippet = 'Sarah Chen - CFO at Maple Ridge Construction | LinkedIn\nhttps://linkedin.com/in/sarah-chen-cfo'
    result = parse_google_result(snippet, "Sarah Chen", "Maple Ridge Construction")
    assert "linkedin.com" in result.get("linkedin_url", "")


def test_parse_google_result_extracts_instagram():
    from enrichment import parse_google_result
    snippet = 'Sarah Chen (@sarahchen_cfo) • Instagram photos and videos'
    result = parse_google_result(snippet, "Sarah Chen", "")
    assert result.get("instagram_handle", "") != ""


def test_parse_google_result_no_match_returns_empty():
    from enrichment import parse_google_result
    result = parse_google_result("nothing useful here", "Unknown Person", "")
    assert result.get("linkedin_url", "") == ""


def test_build_search_query_with_company():
    from enrichment import build_search_query
    q = build_search_query("Sarah Chen", "Maple Ridge Construction")
    assert "Sarah Chen" in q
    assert "Maple Ridge" in q
    assert "linkedin.com" in q


def test_build_search_query_without_company():
    from enrichment import build_search_query
    q = build_search_query("John Smith", "")
    assert "John Smith" in q


def test_should_skip_enrichment_maxed_attempts():
    from enrichment import should_skip_enrichment
    record = {"attempts": 5, "linkedin_url": ""}
    assert should_skip_enrichment(record) is True


def test_should_skip_enrichment_already_done():
    from enrichment import should_skip_enrichment
    record = {"attempts": 1, "linkedin_url": "https://linkedin.com/in/sarah", "status": "done"}
    assert should_skip_enrichment(record) is True


def test_should_skip_enrichment_pending():
    from enrichment import should_skip_enrichment
    record = {"attempts": 0, "linkedin_url": "", "status": "pending"}
    assert should_skip_enrichment(record) is False
