import db


def test_critical_indexes_exist():
    """Verify performance-critical indexes are created."""
    db.init_db()
    with db.get_db() as conn:
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}

    expected = {
        "idx_prospects_email",
        "idx_outcomes_resend_id",
        "idx_tasks_status_due",
        "idx_approval_queue_status",
        "idx_nurture_status",
    }
    assert expected.issubset(index_names), f"Missing indexes: {expected - index_names}"
