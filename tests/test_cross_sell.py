"""Tests for the cross-sell engine."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


def test_product_matrix_life_has_disability():
    from cross_sell import PRODUCT_MATRIX
    assert "life" in PRODUCT_MATRIX
    assert "disability" in PRODUCT_MATRIX["life"]


def test_get_crosssell_recommendations_life():
    from cross_sell import get_crosssell_recommendations
    recs = get_crosssell_recommendations("life")
    assert len(recs) > 0
    assert any("disability" in r["product"] for r in recs)


def test_get_crosssell_recommendations_unknown_product():
    from cross_sell import get_crosssell_recommendations
    recs = get_crosssell_recommendations("nonexistent_product")
    assert recs == []


def test_is_in_cooldown_fresh_prospect():
    from cross_sell import is_in_cooldown
    # A prospect with no cross-sell activity is not in cooldown
    assert is_in_cooldown({"id": 1}, "disability") is False


def test_format_crosssell_task_contains_product():
    from cross_sell import format_crosssell_task
    prospect = {"name": "Sarah Chen", "id": 1}
    result = format_crosssell_task(prospect, {"product": "disability", "message": "Consider disability coverage"})
    assert "Sarah Chen" in result or "disability" in result.lower()


def test_run_crosssell_on_close_creates_task(monkeypatch):
    from cross_sell import run_crosssell_on_close
    import db

    monkeypatch.setattr(db, "add_task", MagicMock(return_value=1))
    monkeypatch.setattr(db, "get_tags", MagicMock(return_value=["closed_life"]))

    prospect = {"id": 1, "name": "Sarah Chen", "stage": "Closed Won"}
    run_crosssell_on_close(prospect, closed_product="life")
    # Should attempt to create at least one cross-sell task
    db.add_task.assert_called()
