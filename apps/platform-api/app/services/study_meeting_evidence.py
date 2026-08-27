"""One private, expiring photo per meeting. No attendance/credit writes."""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta

from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import user_context
from app.services.study_evidence_storage import EvidenceStorage, EvidenceStorageError, normalize_image
from app.services.study_meetings import (
    StudyMeetingError, StudyMeetingFeatureDisabled, StudyMeetingPermissionError,
    _db_timestamp, _lock_session, _operation_scope_allows, _require_write, _serialize,
    _target_for_member,
)


def _enabled() -> None:
    if not get_settings().study_meeting_evidence_enabled:
        raise StudyMeetingFeatureDisabled("学习合影功能尚未开启")


def evidence_metadata(connection, session_id: int) -> dict | None:
    row = execute(connection,
        "SELECT id, content_type, file_size, uploaded_at, expires_at FROM study_meeting_evidence "
        "WHERE study_meeting_session_id=? AND active_slot=1 AND deleted_at IS NULL "
        "AND storage_deleted_at IS NULL AND expires_at>?",
        (session_id, _db_timestamp(connection))).fetchone()
    return {key: _serialize(value) for key, value in dict(row).items()} if row else None


def _member_authorized(member_id: int, row: dict) -> None:
    _target_for_member(member_id, row["study_group_org_unit_id"])


def upload_evidence(*, member_id: int, session_id: int, content: bytes, content_type: str) -> dict:
    _enabled()
    _require_write()
    row = fetch_one("SELECT * FROM study_meeting_sessions WHERE id=?", (session_id,))
    if not row:
        raise StudyMeetingError("学习会记录不存在")
    _member_authorized(member_id, row)
    if row["status"] != "DRAFT":
        raise StudyMeetingError("仅能上传或替换草稿学习会的合影")
    storage: EvidenceStorage | None = None
    key: str | None = None
    put_attempted = False
    evidence_id: int | None = None
    try:
        clean, media_type, extension = normalize_image(content, content_type)
        storage = EvidenceStorage()
        key = storage.make_key(session_id=session_id, extension=extension)
        # Reserve metadata BEFORE the external write. Failed/abandoned uploads
        # remain discoverable for cleanup, rather than becoming orphan objects.
        with transaction() as connection:
            row = _lock_session(connection, session_id)
            _member_authorized(member_id, row)
            if row["status"] != "DRAFT":
                raise StudyMeetingError("学习会已提交，请刷新")
            now = _db_timestamp(connection)
            expiry = _db_timestamp(connection, datetime.now(UTC) + timedelta(hours=get_settings().study_evidence_retention_hours))
            cursor = execute(connection,
                "INSERT INTO study_meeting_evidence(study_meeting_session_id, storage_key, storage_backend, "
                "storage_namespace, content_type, file_size, sha256, uploaded_by_member_id, uploaded_at, "
                "expires_at, active_slot, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (session_id, key, storage.backend, storage.namespace, media_type, len(clean),
                 hashlib.sha256(clean).hexdigest(), member_id, now, expiry, now, now))
            evidence_id = int(cursor.lastrowid)
        try:
            # A put can time out after the remote service accepted the object;
            # treat every attempted key as a compensation candidate.
            put_attempted = True
            storage.put(key, clean, media_type)
            with transaction() as connection:
                row = _lock_session(connection, session_id)
                _member_authorized(member_id, row)
                _enabled()
                _require_write()
                if row["status"] != "DRAFT":
                    raise StudyMeetingError("学习会已提交，请刷新")
                before = evidence_metadata(connection, session_id)
                now = _db_timestamp(connection)
                execute(connection, "UPDATE study_meeting_evidence SET active_slot=NULL, deleted_at=?, updated_at=? "
                        "WHERE study_meeting_session_id=? AND active_slot=1", (now, now, session_id))
                activated = execute(connection, "UPDATE study_meeting_evidence SET active_slot=1, updated_at=? "
                                    "WHERE id=? AND storage_deleted_at IS NULL AND expires_at>?", (now, evidence_id, now))
                if activated.rowcount != 1:
                    raise StudyMeetingError("合影已过期，请重新上传")
                result = evidence_metadata(connection, session_id)
                write_audit(connection, actor_user_id=None, action="study_meeting.evidence_upload",
                            resource_type="study_meeting_session", resource_id=str(session_id),
                            org_unit_id=row["class_org_unit_id"], before=before,
                            after={"evidence": result, "uploaded_by_member_id": member_id})
                return result
        except Exception:
            try:
                with transaction() as connection:
                    now = _db_timestamp(connection)
                    execute(connection, "UPDATE study_meeting_evidence SET deleted_at=?, updated_at=? WHERE id=? AND active_slot IS NULL",
                            (now, now, evidence_id))
                    write_audit(
                        connection,
                        actor_user_id=None,
                        action="study_meeting.evidence_upload_failed",
                        resource_type="study_meeting_session",
                        resource_id=str(session_id),
                        org_unit_id=row["class_org_unit_id"],
                        after={"evidence_id": evidence_id, "storage_key": key, "compensation": "pending"},
                        result="FAILED",
                    )
            except Exception:
                # Compensation must still be attempted if the failure-marking
                # transaction itself encounters a transient database problem.
                pass
            if put_attempted and storage is not None and key is not None:
                try:
                    storage.delete(key)
                except EvidenceStorageError:
                    # The row remains eligible for the bounded cleanup retry.
                    pass
            raise
    except EvidenceStorageError as exc:
        raise StudyMeetingError(str(exc)) from exc


def read_evidence(*, session_id: int, member_id: int | None = None,
                  actor_user_id: int | None = None) -> tuple[bytes, str]:
    _enabled()
    try:
        with transaction() as connection:
            row = _lock_session(connection, session_id)
            if actor_user_id is not None:
                user = user_context(actor_user_id) or {}
                if "plans:read" not in user.get("permissions", []) or not _operation_scope_allows(actor_user_id, row["class_org_unit_id"]):
                    raise StudyMeetingPermissionError("无权查看该学习会合影")
            elif member_id is not None:
                _member_authorized(member_id, row)
            else:
                raise StudyMeetingPermissionError("需要登录")
            metadata = evidence_metadata(connection, session_id)
            if not metadata:
                raise StudyMeetingError("合影未上传或已到期清理")
            evidence = dict(execute(connection, "SELECT * FROM study_meeting_evidence WHERE id=?", (metadata["id"],)).fetchone())
            storage = EvidenceStorage()
            storage.check_namespace(evidence)
            content = storage.get(evidence["storage_key"])
            if len(content) != evidence["file_size"] or hashlib.sha256(content).hexdigest() != evidence["sha256"]:
                raise StudyMeetingError("合影完整性核验未通过")
            write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.evidence_view",
                        resource_type="study_meeting_session", resource_id=str(session_id),
                        org_unit_id=row["class_org_unit_id"], after={"evidence_id": evidence["id"], "member_id": member_id})
            return content, evidence["content_type"]
    except EvidenceStorageError as exc:
        raise StudyMeetingError(str(exc)) from exc


def cleanup_evidence(*, apply: bool = False, limit: int = 500) -> dict:
    """Bounded, idempotent physical cleanup with an explicit production gate.

    Production/staging only process rows whose business expiry has passed.  A
    short grace period avoids racing a late object write after a worker timeout;
    API access still stops exactly at ``expires_at``.  The legacy dev/test rule
    also cleans abandoned replacement reservations so existing isolated tests
    and local cleanup workflows remain compatible.
    """
    settings = get_settings()
    backend = os.getenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local").strip().lower()
    if settings.app_env not in {"dev", "test"}:
        if not settings.study_evidence_cleanup_enabled:
            raise StudyMeetingPermissionError("生产/预发布合影清理未显式开启")
        if backend != "cloudbase" or settings.study_evidence_cleanup_prefix != "study-meetings/":
            raise StudyMeetingPermissionError("合影清理仅允许 CloudBase 内置存储的 study-meetings/ 前缀")
    if apply:
        _require_write()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.study_evidence_cleanup_grace_seconds)
    if settings.app_env in {"dev", "test"}:
        expiry_clause = "(deleted_at IS NOT NULL OR expires_at<=?)"
    else:
        # Production must never remove a freshly failed/non-expired reservation
        # merely because it is logically inactive.
        expiry_clause = "expires_at<=?"
    candidates = fetch_all("SELECT id, study_meeting_session_id FROM study_meeting_evidence "
                           f"WHERE storage_deleted_at IS NULL AND {expiry_clause} "
                           "ORDER BY id LIMIT ?", (cutoff.isoformat(), min(max(limit, 1), 500)))
    report = {"candidates": len(candidates), "deleted": 0, "errors": 0, "apply": apply}
    if not apply:
        return report
    storage = EvidenceStorage()
    for candidate in candidates:
        try:
            with transaction() as connection:
                session = _lock_session(connection, candidate["study_meeting_session_id"])
                now = _db_timestamp(connection)
                row = execute(connection, "SELECT * FROM study_meeting_evidence WHERE id=? AND storage_deleted_at IS NULL "
                              f"AND {expiry_clause}", (candidate["id"], _db_timestamp(connection, cutoff))).fetchone()
                if not row:
                    continue
                row = dict(row)
                storage.check_namespace(row)
                storage.delete(row["storage_key"])
                execute(connection, "UPDATE study_meeting_evidence SET active_slot=NULL, deleted_at=COALESCE(deleted_at, ?), "
                        "storage_deleted_at=?, updated_at=? WHERE id=?", (now, now, now, row["id"]))
                write_audit(connection, actor_user_id=None, action="study_meeting.evidence_cleanup",
                            resource_type="study_meeting_session", resource_id=str(candidate["study_meeting_session_id"]),
                            org_unit_id=session["class_org_unit_id"], after={"evidence_id": row["id"], "storage_deleted": True})
            report["deleted"] += 1
        except Exception:
            # One broken object, stale row, or transient DB error must not stop
            # the rest of a bounded batch.  The metadata remains retryable and
            # no raw SDK error or credential value enters the report.
            report["errors"] += 1
    return report
