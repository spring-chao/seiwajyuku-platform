"""Verify independently issued, time bounded R3 approvals; never issue them.

The signer key is preconfigured outside the checkout/output directory. Holding
cloud/DB credentials, an approval reference, or a CLI flag is not authorization.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import sqlite3
from pathlib import Path

from r3_read_evidence import ReadFailure, _Issuer, fingerprint


def verify_authorization(document, *, key, purpose, scope, commit, now):
    if not isinstance(document, dict) or not isinstance(key, bytes) or len(key) < 32:
        raise ReadFailure("AUTHORIZATION_REQUIRED")
    data = dict(document)
    seal = data.pop("seal", None)
    if not isinstance(seal, str):
        raise ReadFailure("AUTHORIZATION_SIGNATURE_INVALID")
    try:
        expected = _Issuer(key)._mac(data)
    except (TypeError, ValueError):
        raise ReadFailure("AUTHORIZATION_INVALID") from None
    if not hmac.compare_digest(seal, expected):
        raise ReadFailure("AUTHORIZATION_SIGNATURE_INVALID")
    if (
        type(data.get("schema_version")) is not int
        or data.get("schema_version") != 1
        or data.get("purpose") != purpose
        or data.get("scope") != scope
        or data.get("controller_commit") != commit
        or not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(data.get("approval_ref"), str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", data["approval_ref"])
        or not isinstance(data.get("approved_by"), str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", data["approved_by"])
        or data.get("realm") != "live"
    ):
        raise ReadFailure("AUTHORIZATION_SCOPE_MISMATCH")
    start, end = data.get("not_before"), data.get("expires_at")
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end, now)):
        raise ReadFailure("AUTHORIZATION_TIME_INVALID")
    if not start <= now < end or not 0 < end - start <= 3600:
        raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
    return data


def read_document(path):
    """Do not echo path contents, JSON parser exceptions or credentials."""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 65536:
            raise ReadFailure("AUTHORIZATION_TOO_LARGE")
        return json.loads(raw)
    except ReadFailure:
        raise
    except (OSError, ValueError, TypeError):
        raise ReadFailure("AUTHORIZATION_FILE_INVALID") from None


class ReadAttemptJournal:
    """One query batch per approval, spent durably before opening a connection.

    This is local collector bookkeeping, separate from the production controller
    journal. A process crash, failed connection or unknown result cannot replay.
    The canonical private journal must be retained, never recreated to retry.
    """

    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise ReadFailure("READ_JOURNAL_LINK_DISALLOWED")
        try:
            existed = self.path.exists()
            with sqlite3.connect(self.path, timeout=2) as connection:
                connection.execute("PRAGMA synchronous=FULL")
                if existed:
                    tables = {row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    if connection.execute("PRAGMA user_version").fetchone()[0] != 1 or tables != {"read_attempts"}:
                        raise ReadFailure("READ_JOURNAL_CORRUPT")
                else:
                    connection.execute("PRAGMA user_version=1")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS read_attempts("
                    "approval_ref TEXT PRIMARY KEY, grant_fingerprint TEXT NOT NULL,"
                    "started_at REAL NOT NULL)"
                )
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ReadFailure("READ_JOURNAL_CORRUPT")
        except sqlite3.Error:
            raise ReadFailure("READ_JOURNAL_UNAVAILABLE") from None

    def reserve(self, grant, now):
        if grant.get("journal_fingerprint") != fingerprint(self.path.resolve().as_posix()):
            raise ReadFailure("READ_JOURNAL_TARGET_MISMATCH")
        try:
            with sqlite3.connect(self.path, timeout=2) as connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO read_attempts VALUES(?,?,?)",
                    (grant["approval_ref"], fingerprint(grant), now),
                )
        except sqlite3.IntegrityError:
            raise ReadFailure("DB_READ_REPLAY_DISALLOWED") from None
        except sqlite3.Error:
            raise ReadFailure("READ_JOURNAL_UNAVAILABLE") from None
