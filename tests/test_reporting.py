"""Tests for reporting query functions."""
import pytest
from unittest.mock import patch, MagicMock


def test_get_conversion_by_source_returns_list():
    from db import get_conversion_by_source
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_conversion_by_source()
        assert isinstance(result, list)


def test_get_pipeline_metrics_returns_dict():
    from db import get_pipeline_metrics
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_pipeline_metrics()
        assert isinstance(result, dict)


def test_get_stage_funnel_returns_list():
    from db import get_stage_funnel
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_stage_funnel()
        assert isinstance(result, list)


def test_get_fyc_by_advisor_returns_list():
    from db import get_fyc_by_advisor
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_fyc_by_advisor()
        assert isinstance(result, list)


def test_get_trust_level_defaults_to_1():
    from db import get_trust_level
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_trust_level()
        assert result == 1


def test_create_email_tracking_token_returns_string():
    from db import create_email_tracking_token
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        token = create_email_tracking_token(prospect_id=1, prospect_name="Sarah", email_type="follow_up")
        assert isinstance(token, str)
        assert len(token) > 10
