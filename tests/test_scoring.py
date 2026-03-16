import os
import sys
from datetime import date

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_scoring"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import scoring


_SCORING_DATA_DIR = "/tmp/test_calm_bot_scoring"
_SCORING_DB_PATH = os.path.join(_SCORING_DATA_DIR, "pipeline.db")


def setup_function():
    os.environ["DATA_DIR"] = _SCORING_DATA_DIR
    db.DB_PATH = _SCORING_DB_PATH
    if os.path.exists(_SCORING_DB_PATH):
        os.remove(_SCORING_DB_PATH)
    db.init_db()


def _make_prospect(**kwargs):
    base = {
        "name": "Test Prospect",
        "stage": "Contacted",
        "priority": "warm",
        "product": "Life Insurance Term 20",
        "aum": 0,
        "revenue": 2000,
        "first_contact": date.today().strftime("%Y-%m-%d"),
        "next_followup": date.today().strftime("%Y-%m-%d"),
        "notes": "",
    }
    base.update(kwargs)
    return base


def test_score_prospect_basic():
    p = _make_prospect()
    result = scoring.score_prospect(p)
    assert "score" in result
    assert 0 <= result["score"] <= 100
    assert "reasons" in result
    assert "action" in result


def test_score_prospect_hot_priority():
    p = _make_prospect(priority="hot")
    result = scoring.score_prospect(p)
    assert "Hot priority" in result["reasons"]
    assert result["priority_score"] == 10


def test_score_prospect_high_aum():
    p = _make_prospect(aum=600000, revenue=0)
    result = scoring.score_prospect(p)
    assert any("AUM" in r for r in result["reasons"])


def test_get_actual_win_rates_empty():
    rates = scoring.get_actual_win_rates()
    assert isinstance(rates, dict)
    assert len(rates) == 0


def test_get_actual_win_rates_insufficient_data():
    """Products with fewer than 5 data points should not appear."""
    with db.get_db() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'Test', 'Won', 'Good fit', 'Life Insurance')"
            )
        conn.execute(
            "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
            "VALUES (date('now'), 'Test', 'Lost', 'Too expensive', 'Life Insurance')"
        )
    # Only 4 records — below threshold of 5
    rates = scoring.get_actual_win_rates()
    assert "Life Insurance" not in rates


def test_get_actual_win_rates_with_enough_data():
    """Products with 5+ data points should compute a win rate."""
    with db.get_db() as conn:
        for i in range(6):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'TestA', 'Won', 'Great', 'Disability Insurance')"
            )
        for i in range(4):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'TestB', 'Lost', 'Price', 'Disability Insurance')"
            )
    rates = scoring.get_actual_win_rates()
    assert "Disability Insurance" in rates
    # 6 wins / 10 total = 0.6
    assert abs(rates["Disability Insurance"] - 0.6) < 0.01


def test_score_prospect_win_rate_boost():
    """A product with a known high win rate should receive a score boost."""
    # Seed win_loss_log with enough records for "Critical Illness"
    with db.get_db() as conn:
        for i in range(8):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'X', 'Won', 'Great', 'Critical Illness')"
            )
        for i in range(2):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'Y', 'Lost', 'Price', 'Critical Illness')"
            )
    # win_rate = 0.8, boost factor = 1 + (0.8 - 0.5) * 0.2 = 1.06

    p = _make_prospect(product="Critical Illness", aum=200000, revenue=3000)
    result_with_data = scoring.score_prospect(p)

    # Score should reflect the win-rate boost — reasons should mention it
    assert any("win-rate boost" in r.lower() for r in result_with_data["reasons"])


def test_score_prospect_no_boost_when_insufficient_data():
    """No win-rate boost when fewer than 5 data points exist."""
    # Only 3 records
    with db.get_db() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) "
                "VALUES (date('now'), 'Z', 'Won', 'Great', 'Group Benefits')"
            )

    p = _make_prospect(product="Group Benefits")
    result = scoring.score_prospect(p)
    # No win-rate boost reason
    assert not any("win-rate" in r.lower() for r in result["reasons"])
