"""One private, expiring photo per meeting. No attendance/credit writes."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    try:
        clean, media_type, extension = normalize_image(content, content_type)
        storage = EvidenceStorage()
        key = f"study-evidence/{get_settings().app_env}/{session_id}/{uuid4().hex}.{extension}"
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
            with transaction() as connection:
                now = _db_timestamp(connection)
                execute(connection, "UPDATE study_meeting_evidence SET deleted_at=?, updated_at=? WHERE id=? AND active_slot IS NULL",
                        (now, now, evidence_id))
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
    """Dry run by default. This release authorizes only isolated dev/test cleanup."""
    settings = get_settings()
    if settings.app_env not in {"dev", "test"}:
        raise StudyMeetingPermissionError("本版本合影清理脚本仅允许独立 dev/test 环境")
    if apply:
        _require_write()
    # Expired in-flight reservations are safe to remove too. Activation checks expiry.
    candidates = fetch_all("SELECT id, study_meeting_session_id FROM study_meeting_evidence "
                           "WHERE storage_deleted_at IS NULL AND (deleted_at IS NOT NULL OR expires_at<=?) "
                           "ORDER BY id LIMIT ?", (datetime.now(UTC).isoformat(), min(max(limit, 1), 1000)))
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
                              "AND (deleted_at IS NOT NULL OR expires_at<=?)", (candidate["id"], now)).fetchone()
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
        except EvidenceStorageError:
            report["errors"] += 1  # Metadata stays retryable; no raw cloud exceptions/credentials in logs.
    return report
