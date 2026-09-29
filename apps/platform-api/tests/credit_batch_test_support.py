"""Shared identities for settlement-batch approval tests."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db import execute, transaction


def create_credit_batch_reviewer() -> int:
    """Create a distinct database principal for independent-approval tests."""

    now = datetime.now(UTC).isoformat()
    username = f"credit-reviewer-{uuid4().hex}"
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO app_users(username,display_name,password_hash,created_at,updated_at) "
            "VALUES (?, '学分独立审批测试用户', 'test-only-not-a-password', ?, ?)",
            (username, now, now),
        )
        return int(cursor.lastrowid)
