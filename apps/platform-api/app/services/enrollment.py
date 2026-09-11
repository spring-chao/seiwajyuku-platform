from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.privacy import decrypt_text, encrypt_text, protected_phone
from app.core.security import create_token, decode_token
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context
from app.services.organization_policy import is_valid_member_primary_org
from app.services.members import create_member
from app.services.shuku_business_config import (
    ANNUAL_MEMBER_SERVICE_FEE_APPLIES_TO,
    normalize_fee_amount,
)


PUBLIC_SUCCESS_MESSAGE = "申请已提交，请等待工作人员联系。"
ACTIVE_APPLICATION_STATUSES = {"SUBMITTED", "APPROVED"}
EDITABLE_APPLICATION_STATUSES = {"SUBMITTED", "APPROVED"}
FINANCIAL_FIELDS = {"annual_sales", "profit_margin"}
INVOICE_FIELDS = {
    "company_tax_id",
    "invoice_info",
    "invoice_type",
    "invoice_title",
    "invoice_tax_id",
    "invoice_registered_address",
    "invoice_phone",
    "invoice_bank",
    "invoice_account",
}
INVOICE_SENSITIVE_FIELDS = INVOICE_FIELDS - {"invoice_type"}
INDUSTRY_OPTIONS = (
    "制造业",
    "纺织 / 服装",
    "商贸 / 零售",
    "服务业",
    "建筑 / 工程",
    "信息技术 / 软件",
    "餐饮 / 文旅",
    "医疗 / 健康",
    "教育",
    "金融 / 投资",
    "房地产",
    "其他",
)
POLITICAL_STATUS_OPTIONS = ("群众", "党员")
POSITION_OPTIONS = (
    "法人代表",
    "董事长",
    "合伙人",
    "股东",
    "总经理",
    "经营者夫妻",
    "经营者二代",
)
POLITICAL_STATUS_ALIASES = {
    "群众": "群众",
    "党员": "党员",
    "中共党员": "党员",
    "中国共产党党员": "党员",
}
INVOICE_TYPE_LABELS = {
    "NORMAL": "普票",
    "SPECIAL": "专票",
    "NONE": "无需开票",
}
INVOICE_TYPE_ALIASES = {
    "普票": "NORMAL",
    "普通发票": "NORMAL",
    "增值税普通发票": "NORMAL",
    "专票": "SPECIAL",
    "增值税专用发票": "SPECIAL",
    "无需开票": "NONE",
    "不需要发票": "NONE",
}
PROFIT_MARGIN_LABELS = {
    "GE_10_PERCENT": "10%及以上",
    "LT_10_PERCENT": "0%～10%以下",
    "LOSS": "亏损",
}
PROFIT_MARGIN_ALIASES = {
    "10%及以上": "GE_10_PERCENT",
    "0%～10%以下": "LT_10_PERCENT",
    "0%~10%以下": "LT_10_PERCENT",
    "亏损": "LOSS",
}
GROWTH_TARGET_OPTIONS = ("1.5", "2", "3", "5")
LEGACY_REQUIRED_FIELDS = (
    "books_read",
    "enrollment_reason_philosophy",
    "enrollment_reason_change",
    "enrollment_reason_other",
)
REVIEW_FIELDS = {
    "name",
    "gender",
    "birthday",
    "district",
    "political_status",
    "social_role",
    "company_name",
    "company_tax_id",
    "company_address",
    "email",
    "position",
    "referrer",
    "invoice_info",
    "invoice_type",
    "invoice_title",
    "invoice_tax_id",
    "invoice_registered_address",
    "invoice_phone",
    "invoice_bank",
    "invoice_account",
    "industry_category",
    "industry_other",
    "industry",
    "company_products",
    "employee_count",
    "books_read",
    "enrollment_reason_philosophy",
    "enrollment_reason_change",
    "enrollment_reason_other",
    "learning_years_goal",
    "learning_participation_goal",
    "business_goal",
    "other_goal",
    "goal_years",
    "revenue_growth_target",
    "profit_growth_target",
    "notes",
    "target_shuku_org_unit_id",
    "org_unit_id",
    "join_date",
}


class EnrollmentRateLimitError(ValueError):
    pass


class EnrollmentValidationError(ValueError):
    """A public form value is syntactically valid but violates business rules."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_from_connection(
    connection: Any, statement: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    row = execute(connection, statement, params).fetchone()
    return dict(row) if row else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in row.items()}


def _clean_optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _canonical_profit_margin(value: str | None) -> str | None:
    cleaned = _clean_optional(value)
    if not cleaned:
        return None
    canonical = PROFIT_MARGIN_ALIASES.get(cleaned, cleaned)
    if canonical not in PROFIT_MARGIN_LABELS:
        raise EnrollmentValidationError("利润率请选择标准选项")
    return canonical


def _canonical_goal_value(
    value: str | None,
    *,
    label: str,
    integer_only: bool = False,
    allow_unset: bool = True,
) -> str | None:
    cleaned = _clean_optional(value)
    if not cleaned:
        return None
    if cleaned in {"UNSET", "暂不设定"}:
        if not allow_unset:
            raise EnrollmentValidationError(f"{label}请选择具体目标")
        return "UNSET"
    pattern = r"^\d+$" if integer_only else r"^\d+(?:\.\d+)?$"
    if not re.fullmatch(pattern, cleaned):
        suffix = "或选择暂不设定" if allow_unset else ""
        raise EnrollmentValidationError(f"{label}请输入正数{suffix}")
    number = Decimal(cleaned)
    if number <= 0 or number > 100:
        raise EnrollmentValidationError(f"{label}超出允许范围")
    if integer_only:
        return str(int(number))
    return format(number.normalize(), "f")


def _canonical_industry(payload: dict[str, Any]) -> dict[str, str | None]:
    selected = _clean_optional(payload.get("industry_category"))
    legacy_value = _clean_optional(payload.get("industry"))
    if not selected:
        selected = legacy_value
    aliases = {"纺织服装": "纺织 / 服装", "商贸零售": "商贸 / 零售"}
    selected = aliases.get(selected or "", selected)
    if selected not in INDUSTRY_OPTIONS:
        raise EnrollmentValidationError("所属行业请选择标准选项")
    if selected == "其他":
        supplement = _clean_optional(payload.get("industry_other"))
        if not supplement and legacy_value and legacy_value not in INDUSTRY_OPTIONS:
            supplement = legacy_value
        if not supplement:
            raise EnrollmentValidationError("请选择或填写其他行业")
        return {
            "industry_category": "其他",
            "industry": supplement,
            "industry_other": supplement,
        }
    return {
        "industry_category": selected,
        "industry": selected,
        "industry_other": None,
    }


def _canonical_political(
    payload: dict[str, Any], *, require_standard: bool = False
) -> dict[str, str | None]:
    """Normalize new choices while keeping historical free-text values readable."""
    status = _clean_optional(payload.get("political_status"))
    status = POLITICAL_STATUS_ALIASES.get(status or "", status)
    if require_standard and status not in POLITICAL_STATUS_OPTIONS:
        raise EnrollmentValidationError("政治面貌请选择标准选项")
    role = _clean_optional(payload.get("social_role"))
    if status != "党员":
        role = None
    return {"political_status": status, "social_role": role}


def _legacy_invoice_values(value: str | None) -> dict[str, str]:
    cleaned = _clean_optional(value)
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        parts = [part.strip() for part in cleaned.split("|")]
        keys = ("invoice_title", "invoice_tax_id", "invoice_registered_address")
        return {key: part for key, part in zip(keys, parts) if part}
    return (
        {str(key): str(item).strip() for key, item in parsed.items() if item not in (None, "")}
        if isinstance(parsed, dict)
        else {}
    )


def _canonical_invoice(
    payload: dict[str, Any], *, preserve_detail_fields: bool = True
) -> dict[str, str | None]:
    raw_type = _clean_optional(payload.get("invoice_type"))
    invoice_type = INVOICE_TYPE_ALIASES.get(raw_type or "", raw_type)
    if invoice_type not in INVOICE_TYPE_LABELS:
        raise EnrollmentValidationError("发票类型请选择普票、专票或无需开票")
    if invoice_type == "NONE":
        return {
            "invoice_type": "NONE",
            "invoice_info": None,
            "company_tax_id": _clean_optional(payload.get("company_tax_id")),
            "invoice_title": None,
            "invoice_tax_id": None,
            "invoice_registered_address": None,
            "invoice_phone": None,
            "invoice_bank": None,
            "invoice_account": None,
        }
    legacy = _legacy_invoice_values(payload.get("invoice_info"))
    get_value = lambda key: _clean_optional(payload.get(key)) or legacy.get(key)
    company_tax_id = _clean_optional(payload.get("company_tax_id"))
    tax_id = get_value("invoice_tax_id") or company_tax_id
    title = get_value("invoice_title")
    if preserve_detail_fields:
        registered_address = get_value("invoice_registered_address")
        phone = get_value("invoice_phone")
        bank = get_value("invoice_bank")
        account = get_value("invoice_account")
    else:
        registered_address = None
        phone = None
        bank = None
        account = None
    if not title or not tax_id:
        raise EnrollmentValidationError("普票或专票必须填写发票抬头和税号")
    structured = {
        "invoice_type": invoice_type,
        "invoice_title": title,
        "invoice_tax_id": tax_id,
    }
    if preserve_detail_fields:
        structured.update(
            {
                "invoice_registered_address": registered_address,
                "invoice_phone": phone,
                "invoice_bank": bank,
                "invoice_account": account,
            }
        )
    return {
        **structured,
        "company_tax_id": company_tax_id or tax_id,
        "invoice_info": json.dumps(structured, ensure_ascii=False),
        # Keep the existing nullable columns available to the insert/update
        # paths; the public form deliberately leaves them empty.
        "invoice_registered_address": registered_address,
        "invoice_phone": phone,
        "invoice_bank": bank,
        "invoice_account": account,
    }


def _invoice_fields_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "invoice_type",
            "invoice_info",
            "company_tax_id",
            "invoice_title",
            "invoice_tax_id",
            "invoice_registered_address",
            "invoice_phone",
            "invoice_bank",
            "invoice_account",
        )
    }


def _financial_ciphertext(
    annual_sales: str | None, profit_margin: str | None
) -> str | None:
    values = {
        key: cleaned
        for key, value in {
            "annual_sales": annual_sales,
            "profit_margin": profit_margin,
        }.items()
        if (cleaned := _clean_optional(value))
    }
    return encrypt_text(json.dumps(values, ensure_ascii=False)) if values else None


def _financial_values(ciphertext: str | None) -> dict[str, str]:
    if not ciphertext:
        return {}
    values = json.loads(decrypt_text(ciphertext))
    if not isinstance(values, dict):
        raise ValueError("企业敏感资料格式无效")
    return {
        key: str(value)
        for key, value in values.items()
        if key in FINANCIAL_FIELDS and value not in (None, "")
    }


def _application_number() -> str:
    return f"ENR-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4).upper()}"


def _public_result() -> dict[str, Any]:
    return {"accepted": True, "message": PUBLIC_SUCCESS_MESSAGE}


def _link_public_metadata(row: dict[str, Any]) -> dict[str, Any]:
    safe = _json_safe_row(row)
    safe.pop("token_hash", None)
    safe.pop("active_slot", None)
    return safe


def _target_shuku_options(connection: Any | None = None) -> list[dict[str, Any]]:
    """Return the public application targets from the formal org tree.

    ``org-jiangnan`` is a service umbrella, not an application destination.
    Only active ROOT nodes directly below it are selectable.  The query uses
    stable IDs and parentage; display names are presentation data only.
    """

    sql = (
        "SELECT id, unit_code, name FROM org_units "
        "WHERE is_active=1 AND unit_type='ROOT' AND parent_id='org-jiangnan' "
        "ORDER BY unit_code, id"
    )
    rows = (
        fetch_all(sql)
        if connection is None
        else [dict(row) for row in execute(connection, sql).fetchall()]
    )
    return [
        {"id": row["id"], "unit_code": row["unit_code"], "name": row["name"]}
        for row in rows
        if str(row.get("id") or "") != "org-jiangnan"
    ]


def _validate_target_shuku(
    target_shuku_org_unit_id: str | None, *, connection: Any | None = None
) -> dict[str, Any] | None:
    """Validate one of the formal shuku roots and return its metadata."""

    target = _clean_optional(target_shuku_org_unit_id)
    if not target:
        return None
    sql = (
        "SELECT id, unit_code, name, unit_type, parent_id, is_active "
        "FROM org_units WHERE id=?"
    )
    row = (
        fetch_one(sql, (target,))
        if connection is None
        else _row_from_connection(connection, sql, (target,))
    )
    if (
        not row
        or not row.get("is_active")
        or str(row.get("unit_type") or "").upper() != "ROOT"
        or row.get("parent_id") != "org-jiangnan"
        or row.get("id") == "org-jiangnan"
    ):
        raise EnrollmentValidationError("申请加入的塾无效，请重新选择")
    return row


def get_active_enrollment_link() -> dict[str, Any] | None:
    row = fetch_one(
        "SELECT l.id, l.name, l.status, l.created_by, l.created_at, l.updated_at, "
        "l.target_shuku_org_unit_id, o.name AS target_shuku_name, "
        "disabled_at, last_rotated_at FROM member_enrollment_links "
        "l LEFT JOIN org_units o ON o.id=l.target_shuku_org_unit_id "
        "WHERE l.status='ACTIVE' ORDER BY l.id DESC LIMIT 1"
    )
    return _link_public_metadata(row) if row else None


def get_public_portal() -> dict[str, Any]:
    """Return the public mini-program landing configuration.

    The long-lived enrollment token is intentionally never returned.  Instead
    the home page receives a short-lived, signed handoff that is resolved
    against the currently active enrollment link on every request.
    """

    link = fetch_one(
        "SELECT id, name, status, token_hash, updated_at FROM member_enrollment_links "
        "WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1"
    )
    if not link:
        return {
            "enrollment_available": False,
            "enrollment_entry": None,
        }
    handoff_token = create_token(
        int(link["id"]),
        1,
        "enrollment_handoff",
        timedelta(minutes=10),
        extra_claims={"link_version": _token_hash(str(link["token_hash"]))},
    )
    return {
        "enrollment_available": True,
        "enrollment_entry": {
            "page": "pages/enrollment/index",
            "handoff_token": handoff_token,
            "expires_in": 600,
            "link_name": link["name"],
        },
    }


def create_enrollment_link(
    actor_user_id: int,
    name: str,
    target_shuku_org_unit_id: str | None = None,
) -> dict[str, Any]:
    # 微信小程序码的 scene 最多承载 32 个可见字符；128 bit 随机值已经
    # 足够作为公开入口令牌，同时保留 H5 旧令牌的兼容解析能力。
    raw_token = secrets.token_urlsafe(16)
    now = _now()
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("二维码名称不能为空")
    with transaction() as connection:
        target = _validate_target_shuku(
            target_shuku_org_unit_id, connection=connection
        )
        execute(
            connection,
            "UPDATE member_enrollment_links SET status='DISABLED', active_slot=NULL, "
            "disabled_at=?, updated_at=? WHERE status='ACTIVE'",
            (now, now),
        )
        cursor = execute(
            connection,
            "INSERT INTO member_enrollment_links"
            "(name, token_hash, status, active_slot, created_by, created_at, updated_at, "
            "target_shuku_org_unit_id) VALUES (?, ?, 'ACTIVE', 1, ?, ?, ?, ?)",
            (
                cleaned_name,
                _token_hash(raw_token),
                actor_user_id,
                now,
                now,
                target["id"] if target else None,
            ),
        )
        link_id = int(cursor.lastrowid)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.link.create",
            resource_type="member_enrollment_link",
            resource_id=str(link_id),
            after={
                "name": cleaned_name,
                "status": "ACTIVE",
                "target_shuku_org_unit_id": target["id"] if target else None,
            },
        )
    return {
        "id": link_id,
        "name": cleaned_name,
        "status": "ACTIVE",
        "target_shuku_org_unit_id": target["id"] if target else None,
        "target_shuku_name": target["name"] if target else None,
        "raw_token": raw_token,
        "created_at": now,
        "updated_at": now,
    }


def rotate_enrollment_link(actor_user_id: int, link_id: int) -> dict[str, Any]:
    raw_token = secrets.token_urlsafe(16)
    now = _now()
    with transaction() as connection:
        row = _row_from_connection(
            connection,
            "SELECT id, name, status FROM member_enrollment_links WHERE id=?",
            (link_id,),
        )
        if not row:
            raise ValueError("入塾申请二维码不存在")
        if row["status"] != "ACTIVE":
            raise ValueError("已停用二维码不能轮换，请创建新的二维码")
        execute(
            connection,
            "UPDATE member_enrollment_links SET token_hash=?, last_rotated_at=?, "
            "updated_at=? WHERE id=?",
            (_token_hash(raw_token), now, now, link_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.link.rotate",
            resource_type="member_enrollment_link",
            resource_id=str(link_id),
            after={"status": "ACTIVE", "rotated": True},
        )
    return {
        "id": link_id,
        "name": row["name"],
        "status": "ACTIVE",
        "raw_token": raw_token,
        "last_rotated_at": now,
        "updated_at": now,
    }


def disable_enrollment_link(actor_user_id: int, link_id: int) -> dict[str, Any]:
    now = _now()
    with transaction() as connection:
        row = _row_from_connection(
            connection,
            "SELECT id, status FROM member_enrollment_links WHERE id=?",
            (link_id,),
        )
        if not row:
            raise ValueError("入塾申请二维码不存在")
        if row["status"] == "ACTIVE":
            execute(
                connection,
                "UPDATE member_enrollment_links SET status='DISABLED', active_slot=NULL, "
                "disabled_at=?, updated_at=? WHERE id=?",
                (now, now, link_id),
            )
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="enrollment.link.disable",
                resource_type="member_enrollment_link",
                resource_id=str(link_id),
                after={"status": "DISABLED"},
            )
    return {"id": link_id, "status": "DISABLED", "disabled_at": now}


_MINIPROGRAM_SCENE_RE = re.compile(r"^[A-Za-z0-9!#$&'()*+,/:;=?@._~-]{1,32}$")


def generate_wechat_miniprogram_code(
    actor_user_id: int, link_id: int, raw_token: str
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.wechat_miniprogram_app_id or not settings.wechat_miniprogram_app_secret:
        raise ValueError("微信小程序 AppID 或 AppSecret 尚未配置")
    if not _MINIPROGRAM_SCENE_RE.fullmatch(raw_token or ""):
        raise ValueError("小程序码入口令牌格式无效，请先轮换入口")

    link = fetch_one(
        "SELECT id, name, token_hash, status FROM member_enrollment_links "
        "WHERE id=? LIMIT 1",
        (link_id,),
    )
    if not link or link["status"] != "ACTIVE":
        raise ValueError("入塾申请入口不存在或已停用")
    if _token_hash(raw_token) != link["token_hash"]:
        raise ValueError("当前浏览器中的入口令牌已失效，请轮换后重新生成")

    try:
        with httpx.Client(timeout=20.0) as client:
            access_response = client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": settings.wechat_miniprogram_app_id,
                    "secret": settings.wechat_miniprogram_app_secret,
                },
            )
            try:
                access_data = access_response.json()
            except ValueError:
                access_data = {}
            access_token = access_data.get("access_token")
            if access_response.status_code != 200 or not access_token:
                message = access_data.get("errmsg", "获取微信接口凭证失败")
                raise ValueError(f"微信接口凭证获取失败：{message}")

            code_response = client.post(
                "https://api.weixin.qq.com/wxa/getwxacodeunlimit",
                params={"access_token": access_token},
                json={
                    "scene": raw_token,
                    "page": settings.wechat_miniprogram_page,
                    "width": 430,
                },
            )
    except httpx.HTTPError as exc:
        raise ValueError("微信小程序码服务暂时不可用，请稍后重试") from exc

    content_type = code_response.headers.get("content-type", "")
    if code_response.status_code != 200 or "image/" not in content_type:
        try:
            error_data = code_response.json()
        except ValueError:
            error_data = {}
        message = error_data.get("errmsg", "生成微信小程序码失败")
        raise ValueError(f"微信小程序码生成失败：{message}")

    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.miniprogram_code.generate",
            resource_type="member_enrollment_link",
            resource_id=str(link_id),
            after={"page": settings.wechat_miniprogram_page, "status": "generated"},
        )

    return {
        "link_id": link_id,
        "name": link["name"],
        "page": settings.wechat_miniprogram_page,
        "image_data_url": "data:image/png;base64,"
        + base64.b64encode(code_response.content).decode("ascii"),
        "generated_at": _now(),
    }


def _resolve_public_link(token: str) -> dict[str, Any] | None:
    # Raw enrollment scenes are short, while the signed unified-home handoff
    # is a compact JWT.  Keep a bounded upper limit for both forms.
    if not token or len(token) > 512:
        return None
    raw_link = fetch_one(
        "SELECT id, name, token_hash, target_shuku_org_unit_id "
        "FROM member_enrollment_links "
        "WHERE token_hash=? AND status='ACTIVE' LIMIT 1",
        (_token_hash(token),),
    )
    if raw_link:
        return raw_link

    # Unified-home navigation uses a short-lived signed handoff.  It points
    # to the active link row rather than carrying the enrollment secret.  A
    # rotation/disable updates the row and therefore invalidates older
    # handoffs immediately (with a small second-level tolerance for MySQL
    # DATETIME precision).
    try:
        payload = decode_token(token, "enrollment_handoff")
        link_id = int(payload["sub"])
        issued_at = datetime.fromtimestamp(int(payload["iat"]), UTC)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    link = fetch_one(
        "SELECT id, name, token_hash, status, updated_at, target_shuku_org_unit_id "
        "FROM member_enrollment_links "
        "WHERE id=? AND status='ACTIVE' LIMIT 1",
        (link_id,),
    )
    if not link:
        return None
    link_version = payload.get("link_version")
    if link_version:
        if not hmac.compare_digest(
            str(link_version), _token_hash(str(link["token_hash"]))
        ):
            return None
    updated_at = link.get("updated_at")
    if not link_version and updated_at:
        if isinstance(updated_at, datetime):
            updated_dt = updated_at
        else:
            text = str(updated_at).replace("Z", "+00:00")
            try:
                updated_dt = datetime.fromisoformat(text)
            except ValueError:
                updated_dt = None
        if updated_dt:
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=UTC)
            if updated_dt.astimezone(UTC) > issued_at + timedelta(seconds=2):
                return None
    return link


def _public_shuku_profile(target_shuku_org_unit_id: str | None) -> dict[str, Any]:
    """Return the configured, public-facing profile for one target shuku.

    Payment and contact details are business-owned configuration.  An empty
    or not-yet-migrated profile is deliberately represented as a missing
    configuration instead of falling back to a value embedded in the client.
    """

    missing = {
        "status": "BUSINESS_CONFIG_REQUIRED",
        "code": "BUSINESS_CONFIG_REQUIRED",
        "display_name": None,
        "joining_notice": None,
        "payment_instructions": None,
        "payment": None,
        "fee_amount": None,
        "fee_unit": None,
        "fee_applies_to": [],
        "service_address": None,
        "contact": None,
        "contacts": None,
    }
    if not target_shuku_org_unit_id:
        return missing
    try:
        # SELECT * keeps the endpoint compatible while 0062 is rolling out:
        # 0061 clients/databases only have the original profile columns.
        row = fetch_one(
            "SELECT * FROM enrollment_shuku_profiles "
            "WHERE shuku_org_unit_id=? AND is_active=1 LIMIT 1",
            (target_shuku_org_unit_id,),
        )
    except Exception as exc:
        # Keep rolling deployments safe when the additive profile migration is
        # still being applied.  Do not turn a missing table into a 500.
        message = str(exc).lower()
        if "no such table" not in message and "doesn't exist" not in message:
            raise
        return missing
    if not row or not str(row.get("joining_notice") or "").strip():
        return missing
    payment = {
        key: row.get(key)
        for key in ("payee_name", "bank_name", "bank_account")
        if row.get(key)
    }
    def _missing_table(exc: Exception) -> bool:
        message = str(exc).lower()
        return "no such table" in message or "doesn't exist" in message

    terms: dict[str, Any] = {}
    try:
        terms = fetch_one(
            "SELECT fee_amount, fee_unit, service_address "
            "FROM enrollment_shuku_profile_terms "
            "WHERE shuku_org_unit_id=? AND is_active=1 LIMIT 1",
            (target_shuku_org_unit_id,),
        ) or {}
    except Exception as exc:
        if not _missing_table(exc):
            raise

    contact_rows: list[dict[str, Any]] = []
    try:
        contact_rows = fetch_all(
            "SELECT contact_name, contact_phone, sort_order "
            "FROM enrollment_shuku_contacts "
            "WHERE shuku_org_unit_id=? AND is_active=1 "
            "ORDER BY sort_order, id",
            (target_shuku_org_unit_id,),
        )
    except Exception as exc:
        if not _missing_table(exc):
            raise

    contacts = [
        {
            "name": item.get("contact_name"),
            "phone": item.get("contact_phone"),
            "sort_order": int(item.get("sort_order") or 0),
        }
        for item in contact_rows
        if item.get("contact_name") or item.get("contact_phone")
    ]
    # 0061 stored one optional contact on the profile row. Keep it as a
    # compatibility fallback until all environments have the 0062 table.
    if not contacts and (row.get("contact_name") or row.get("contact_phone")):
        contacts = [
            {
                "name": row.get("contact_name"),
                "phone": row.get("contact_phone"),
                "sort_order": 0,
            }
        ]
    first_contact = contacts[0] if contacts else None
    contact = {
        "contact_name": first_contact.get("name")
        if first_contact
        else row.get("contact_name"),
        "contact_phone": first_contact.get("phone")
        if first_contact
        else row.get("contact_phone"),
        "contact_address": row.get("contact_address")
        or terms.get("service_address"),
    }
    contact = {key: value for key, value in contact.items() if value}

    fee_amount = terms.get("fee_amount")
    if fee_amount in (None, ""):
        fee_amount = row.get("fee_amount")
    fee_amount = normalize_fee_amount(fee_amount)
    fee_unit = terms.get("fee_unit") or row.get("fee_unit")
    service_address = terms.get("service_address") or row.get("service_address")
    fee_applies_to = (
        list(ANNUAL_MEMBER_SERVICE_FEE_APPLIES_TO)
        if fee_amount and fee_unit
        else []
    )
    return {
        "status": "READY",
        "code": None,
        "display_name": row.get("display_name"),
        "joining_notice": row.get("joining_notice"),
        "payment_instructions": row.get("payment_instructions"),
        "payment": payment or None,
        "fee_amount": fee_amount,
        "fee_unit": fee_unit,
        "fee_applies_to": fee_applies_to,
        "service_address": service_address,
        "contact": contact or None,
        "contacts": contacts or None,
    }


def get_public_enrollment_form(
    token: str,
    requested_target_shuku_org_unit_id: str | None = None,
) -> dict[str, Any]:
    link = _resolve_public_link(token)
    if not link:
        raise ValueError("申请链接无效或已停用")
    locked_target_id = link.get("target_shuku_org_unit_id")
    if locked_target_id and requested_target_shuku_org_unit_id:
        if str(locked_target_id) != str(requested_target_shuku_org_unit_id):
            raise EnrollmentValidationError("该申请入口已锁定申请塾，不能更改")
    target = _validate_target_shuku(
        requested_target_shuku_org_unit_id or locked_target_id
    )
    target_options = _target_shuku_options()
    shuku_profile = _public_shuku_profile(target["id"] if target else None)
    return {
        "title": "新学长入塾申请",
        "link_name": link["name"],
        "subtitle": "欢迎您填写入塾申请资料",
        "notice": "提交资料不代表已经正式入塾。工作人员审核资料、确认所属分中心及会费后，才会建立正式学员档案。本页面不进行任何转账或收费。",
        "privacy_notice": "所填资料仅用于入塾审核与后续服务。手机号、税号和企业财务资料将按权限使用。",
        "required_fields": [
            "name",
            "target_shuku_org_unit_id",
            "phone",
            "birthday",
            "gender",
            "political_status",
            "referrer",
            "company_name",
            "company_address",
            "position",
            "invoice_type",
            "industry_category",
            "company_products",
            "employee_count",
            "annual_sales",
            "profit_margin",
            "goal_years",
            "revenue_growth_target",
            "profit_growth_target",
            "rules_acknowledged",
            "privacy_consent",
        ],
        "optional_fields": [
            "district",
            "social_role",
            "email",
            "industry_other",
            "invoice_title",
            "invoice_tax_id",
            "notes",
        ],
        "industry_options": list(INDUSTRY_OPTIONS),
        "political_status_options": list(POLITICAL_STATUS_OPTIONS),
        "position_options": list(POSITION_OPTIONS),
        "invoice_types": [
            {"value": value, "label": label}
            for value, label in INVOICE_TYPE_LABELS.items()
        ],
        "profit_margin_options": [
            {"value": value, "label": label}
            for value, label in PROFIT_MARGIN_LABELS.items()
        ],
        "growth_target_options": [
            {"value": value, "label": f"{value}倍"}
            for value in GROWTH_TARGET_OPTIONS
        ],
        "goal_year_options": ["1", "2", "3", "5", "OTHER"],
        "collects_organization": False,
        "target_shuku_options": target_options,
        "target_shuku_org_unit_id": target["id"] if target else None,
        "target_shuku_name": target["name"] if target else None,
        "target_shuku_locked": bool(locked_target_id),
        "business_config_status": shuku_profile["status"],
        "business_config_code": shuku_profile["code"],
        "shuku_profile": shuku_profile,
        # Keep the most useful fields at the top level for older clients and
        # make the profile object the canonical source for new clients.
        "joining_notice": shuku_profile.get("joining_notice"),
        "payment_instructions": shuku_profile.get("payment_instructions"),
        "payment": shuku_profile.get("payment"),
        "contact": shuku_profile.get("contact"),
        "fee_amount": shuku_profile.get("fee_amount"),
        "fee_unit": shuku_profile.get("fee_unit"),
        "fee_applies_to": shuku_profile.get("fee_applies_to"),
        "service_address": shuku_profile.get("service_address"),
        "contacts": shuku_profile.get("contacts"),
    }


def _check_submission_rate_limit(token_hash: str, client_address: str) -> None:
    settings = get_settings()
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    client_key = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"{token_hash}:{client_address or 'unknown'}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    with transaction() as connection:
        row = _row_from_connection(
            connection,
            "SELECT window_started_at, attempt_count "
            "FROM member_enrollment_submission_guards WHERE guard_key=?",
            (client_key,),
        )
        window_start = None
        if row:
            value = row["window_started_at"]
            window_start = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=UTC)
        expired = (
            window_start is None
            or now_dt - window_start
            >= timedelta(seconds=settings.public_enrollment_rate_window_seconds)
        )
        if row and not expired and int(row["attempt_count"]) >= settings.public_enrollment_rate_limit:
            raise EnrollmentRateLimitError("提交过于频繁，请稍后再试")
        if row:
            execute(
                connection,
                "UPDATE member_enrollment_submission_guards SET window_started_at=?, "
                "attempt_count=?, updated_at=? WHERE guard_key=?",
                (
                    now if expired else row["window_started_at"],
                    1 if expired else int(row["attempt_count"]) + 1,
                    now,
                    client_key,
                ),
            )
        else:
            execute(
                connection,
                "INSERT INTO member_enrollment_submission_guards"
                "(guard_key, window_started_at, attempt_count, updated_at) VALUES (?, ?, 1, ?)",
                (client_key, now, now),
            )
        cleanup_before = (
            now_dt
            - timedelta(seconds=settings.public_enrollment_rate_window_seconds * 4)
        ).isoformat()
        execute(
            connection,
            "DELETE FROM member_enrollment_submission_guards WHERE updated_at<?",
            (cleanup_before,),
        )


def _prepare_public_values(payload: dict[str, Any]) -> dict[str, Any]:
    legacy_submission = (
        payload.get("rules_acknowledged") is None
        and all(_clean_optional(payload.get(field)) for field in LEGACY_REQUIRED_FIELDS)
    )
    if payload.get("rules_acknowledged") is not True and not legacy_submission:
        raise EnrollmentValidationError("请先阅读并确认加入守则与缴费说明")
    gender = _clean_optional(payload.get("gender"))
    if gender not in {"MALE", "FEMALE"}:
        raise EnrollmentValidationError("性别请选择男或女")
    industry = _canonical_industry(payload)
    political = _canonical_political(payload, require_standard=True)
    invoice = _canonical_invoice(payload, preserve_detail_fields=False)
    values: dict[str, Any] = {
        key: _clean_optional(payload.get(key))
        for key in (
            "gender",
            "birthday",
            "district",
            "company_name",
            "company_address",
            "email",
            "position",
            "referrer",
            "company_products",
            "books_read",
            "enrollment_reason_philosophy",
            "enrollment_reason_change",
            "enrollment_reason_other",
            "learning_years_goal",
            "learning_participation_goal",
            "business_goal",
            "other_goal",
            "notes",
        )
    }
    values["gender"] = gender
    values.update(political)
    position = values["position"]
    if position not in POSITION_OPTIONS:
        raise EnrollmentValidationError("职务请选择标准选项")
    values["employee_count"] = payload.get("employee_count")
    values["annual_sales"] = _clean_optional(payload.get("annual_sales"))
    profit_margin = _canonical_profit_margin(payload.get("profit_margin"))
    if not profit_margin:
        raise EnrollmentValidationError("利润率请选择标准选项")
    values["profit_margin"] = profit_margin
    goal_years = _canonical_goal_value(
        payload.get("goal_years"),
        label="计划学习年限",
        integer_only=True,
        allow_unset=False,
    )
    if not goal_years:
        raise EnrollmentValidationError("计划学习年限请选择具体年限")
    values["goal_years"] = goal_years
    revenue_growth_target = _canonical_goal_value(
        payload.get("revenue_growth_target"),
        label="业绩目标",
        allow_unset=False,
    )
    if not revenue_growth_target:
        raise EnrollmentValidationError("业绩提升目标请选择具体目标")
    values["revenue_growth_target"] = revenue_growth_target
    profit_growth_target = _canonical_goal_value(
        payload.get("profit_growth_target"),
        label="利润目标",
        allow_unset=False,
    )
    if not profit_growth_target:
        raise EnrollmentValidationError("利润提升目标请选择具体目标")
    values["profit_growth_target"] = profit_growth_target
    values["_legacy_submission"] = legacy_submission
    values.update(industry)
    values.update(invoice)
    return values


def submit_public_enrollment(
    token: str, payload: dict[str, Any], client_address: str
) -> dict[str, Any]:
    link = _resolve_public_link(token)
    if not link:
        raise ValueError("申请链接无效或已停用")
    _check_submission_rate_limit(link["token_hash"], client_address)

    submitted_target = _clean_optional(payload.get("target_shuku_org_unit_id"))
    linked_target = _clean_optional(link.get("target_shuku_org_unit_id"))
    if linked_target and submitted_target != linked_target:
        raise EnrollmentValidationError("该入口已锁定申请塾，不能更改")
    target = _validate_target_shuku(submitted_target or linked_target)
    if not target:
        raise EnrollmentValidationError("请选择申请加入的塾")

    name = payload["name"].strip()
    if not name:
        raise ValueError("姓名不能为空")
    values = _prepare_public_values(payload)
    phone_fields = protected_phone(payload["phone"])
    existing_application = fetch_one(
        "SELECT id FROM member_enrollment_applications "
        "WHERE active_phone_guard=? LIMIT 1",
        (phone_fields["phone_hash"],),
    )
    if existing_application:
        return _public_result()

    duplicate_member = fetch_one(
        "SELECT id FROM members WHERE phone_hash=? LIMIT 1",
        (phone_fields["phone_hash"],),
    )
    financial_ciphertext = _financial_ciphertext(
        values["annual_sales"], values["profit_margin"]
    )
    now = _now()
    application_no = _application_number()
    values.update(
        {
            "application_no": application_no,
            "link_id": link["id"],
            "target_shuku_org_unit_id": target["id"],
            "phone_ciphertext": phone_fields["phone_ciphertext"],
            "phone_hash": phone_fields["phone_hash"],
            "phone_last4": phone_fields["phone_last4"],
            "phone_masked": phone_fields["phone_masked"],
            "active_phone_guard": phone_fields["phone_hash"],
            "name": name,
            "enterprise_financial_ciphertext": financial_ciphertext,
            "privacy_consent_at": now,
            "rules_acknowledged": 0 if values.pop("_legacy_submission", False) else 1,
            "rules_acknowledged_at": now,
            "application_status": "SUBMITTED",
            "payment_status": "UNCONFIRMED",
            "duplicate_member_risk": 1 if duplicate_member else 0,
            "org_unit_id": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    insert_columns = [
        "application_no",
        "link_id",
        "target_shuku_org_unit_id",
        "phone_ciphertext",
        "phone_hash",
        "phone_last4",
        "phone_masked",
        "active_phone_guard",
        "name",
        "gender",
        "birthday",
        "district",
        "political_status",
        "social_role",
        "company_name",
        "company_tax_id",
        "company_address",
        "email",
        "position",
        "referrer",
        "invoice_type",
        "invoice_info",
        "invoice_title",
        "invoice_tax_id",
        "invoice_registered_address",
        "invoice_phone",
        "invoice_bank",
        "invoice_account",
        "industry_category",
        "industry",
        "industry_other",
        "company_products",
        "employee_count",
        "books_read",
        "enrollment_reason_philosophy",
        "enrollment_reason_change",
        "enrollment_reason_other",
        "learning_years_goal",
        "learning_participation_goal",
        "business_goal",
        "other_goal",
        "goal_years",
        "revenue_growth_target",
        "profit_growth_target",
        "enterprise_financial_ciphertext",
        "notes",
        "privacy_consent_at",
        "rules_acknowledged",
        "rules_acknowledged_at",
        "application_status",
        "payment_status",
        "duplicate_member_risk",
        "org_unit_id",
        "created_at",
        "updated_at",
    ]
    insert_values = tuple(values[column] for column in insert_columns)
    try:
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO member_enrollment_applications("
                + ", ".join(insert_columns)
                + ") VALUES ("
                + ", ".join("?" for _ in insert_values)
                + ")",
                insert_values,
            )
            application_id = int(cursor.lastrowid)
            write_audit(
                connection,
                actor_user_id=None,
                action="enrollment.application.submit",
                resource_type="member_enrollment_application",
                resource_id=str(application_id),
                after={
                    "application_no": application_no,
                    "phone": phone_fields["phone_masked"],
                    "duplicate_member_risk": bool(duplicate_member),
                    "link_id": link["id"],
                    "target_shuku_org_unit_id": target["id"],
                    "rules_acknowledged": bool(values["rules_acknowledged"]),
                },
            )
    except Exception:
        # A concurrent request with the same phone can win the unique active
        # guard. It must receive the same generic result without learning that
        # an application already exists.
        if fetch_one(
            "SELECT id FROM member_enrollment_applications "
            "WHERE active_phone_guard=? LIMIT 1",
            (phone_fields["phone_hash"],),
        ):
            return _public_result()
        raise
    return _public_result()


def _computed_status(row: dict[str, Any]) -> str:
    if row["application_status"] == "REJECTED":
        return "REJECTED"
    if row["application_status"] == "CANCELLED":
        return "CANCELLED"
    if row["application_status"] == "ENROLLED":
        return "ENROLLED"
    if row["application_status"] != "APPROVED":
        return "PENDING_REVIEW"
    if row["payment_status"] != "PAID":
        return "PENDING_PAYMENT"
    if not row.get("org_unit_id"):
        return "PENDING_CENTER"
    return "PENDING_ENROLLMENT"


def _scope_condition(actor_user_id: int) -> tuple[str, tuple[Any, ...]]:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        return "", ()
    actor = user_context(actor_user_id) or {"permissions": []}
    can_review_unassigned = "enrollment:unassigned_review" in actor.get("permissions", [])
    if not allowed:
        return (
            " AND a.org_unit_id IS NULL AND a.target_shuku_org_unit_id IS NULL"
            if can_review_unassigned
            else " AND 1=0",
            (),
        )
    placeholders = ",".join("?" for _ in allowed)
    condition = (
        f"(a.org_unit_id IN ({placeholders}) "
        f"OR a.target_shuku_org_unit_id IN ({placeholders}))"
    )
    params = tuple(sorted(allowed)) + tuple(sorted(allowed))
    if can_review_unassigned:
        condition = (
            f"(a.org_unit_id IS NULL AND a.target_shuku_org_unit_id IS NULL "
            f"OR {condition})"
        )
    return f" AND {condition}", params


def list_enrollment_applications(
    actor_user_id: int,
    *,
    application_status: str | None = None,
    payment_status: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    actor = user_context(actor_user_id) or {"permissions": []}
    can_view_contact = "enrollment:read" in actor["permissions"]
    conditions = ["1=1"]
    params: list[Any] = []
    if application_status:
        conditions.append("a.application_status=?")
        params.append(application_status)
    if payment_status:
        conditions.append("a.payment_status=?")
        params.append(payment_status)
    cleaned_query = (query or "").strip()
    if cleaned_query:
        conditions.append(
            "(a.application_no LIKE ? OR a.name LIKE ? OR a.phone_last4=?)"
        )
        params.extend((f"%{cleaned_query}%", f"%{cleaned_query}%", cleaned_query[-4:]))
    scope_sql, scope_params = _scope_condition(actor_user_id)
    params.extend(scope_params)
    params.append(limit)
    rows = fetch_all(
        "SELECT a.id, a.application_no, a.name, a.phone_masked, a.phone_ciphertext, "
        "a.company_name, "
        "a.application_status, a.payment_status, a.duplicate_member_risk, "
        "a.org_unit_id, o.name AS org_unit_name, "
        "a.target_shuku_org_unit_id, target_o.name AS target_shuku_name, "
        "a.join_date, a.converted_member_id, "
        "a.created_at, a.updated_at FROM member_enrollment_applications a "
        "LEFT JOIN org_units o ON o.id=a.org_unit_id "
        "LEFT JOIN org_units target_o ON target_o.id=a.target_shuku_org_unit_id WHERE "
        + " AND ".join(conditions)
        + scope_sql
        + " ORDER BY CASE a.application_status WHEN 'SUBMITTED' THEN 1 "
        "WHEN 'APPROVED' THEN 2 ELSE 3 END, a.created_at DESC LIMIT ?",
        tuple(params),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        safe = _json_safe_row(row)
        safe.pop("phone_ciphertext", None)
        if can_view_contact:
            safe["phone"] = decrypt_text(row["phone_ciphertext"])
        safe["duplicate_member_risk"] = bool(safe["duplicate_member_risk"])
        safe["computed_status"] = _computed_status(row)
        result.append(safe)
    return result


def _application_row(
    application_id: int, connection: Any | None = None, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = (
        "SELECT a.*, o.name AS org_unit_name, "
        "target_o.name AS target_shuku_name, l.name AS link_name, "
        "reviewer.display_name AS reviewer_name, payer.display_name AS payment_confirmer_name, "
        "converter.display_name AS converter_name "
        "FROM member_enrollment_applications a "
        "JOIN member_enrollment_links l ON l.id=a.link_id "
        "LEFT JOIN org_units o ON o.id=a.org_unit_id "
        "LEFT JOIN org_units target_o ON target_o.id=a.target_shuku_org_unit_id "
        "LEFT JOIN app_users reviewer ON reviewer.id=a.reviewed_by "
        "LEFT JOIN app_users payer ON payer.id=a.payment_confirmed_by "
        "LEFT JOIN app_users converter ON converter.id=a.converted_by "
        "WHERE a.id=?"
    )
    if lock and connection is not None and not isinstance(connection, sqlite3.Connection):
        sql += " FOR UPDATE"
    if connection is None:
        return fetch_one(sql, (application_id,))
    return _row_from_connection(connection, sql, (application_id,))


def _assert_application_scope(actor_user_id: int, row: dict[str, Any]) -> None:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        return
    actor = user_context(actor_user_id) or {"permissions": []}
    if (
        not row.get("org_unit_id")
        and not row.get("target_shuku_org_unit_id")
        and "enrollment:unassigned_review" in actor.get("permissions", [])
    ):
        return
    if row.get("org_unit_id"):
        if row["org_unit_id"] not in allowed:
            raise PermissionError("入塾申请不在当前组织授权范围内")
        return
    if (
        not row.get("target_shuku_org_unit_id")
        or row["target_shuku_org_unit_id"] not in allowed
    ):
        raise PermissionError("入塾申请不在当前组织授权范围内")


def _missing_enrollment_gates(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if row["application_status"] != "APPROVED":
        missing.append("申请尚未审核通过")
    if row["payment_status"] != "PAID":
        missing.append("尚未确认收款")
    target = row.get("target_shuku_org_unit_id")
    if not target:
        missing.append("尚未选择申请加入的塾")
    else:
        try:
            _validate_target_shuku(str(target))
        except EnrollmentValidationError:
            missing.append("申请加入的塾无效或已停用")
    if not row.get("org_unit_id"):
        missing.append("尚未选择正式管理单元")
    else:
        org = fetch_one(
            "SELECT unit_type, parent_id, is_active FROM org_units WHERE id=?",
            (row["org_unit_id"],),
        )
        if (
            not org
            or not org["is_active"]
            or not is_valid_member_primary_org(
                org_unit_id=str(row["org_unit_id"]),
                unit_type=org["unit_type"],
                parent_id=org.get("parent_id"),
            )
        ):
            missing.append("正式管理单元无效或已停用")
    if not (row.get("name") or "").strip():
        missing.append("姓名无效")
    if not row.get("phone_hash"):
        missing.append("手机号无效")
    duplicate = fetch_one(
        "SELECT id FROM members WHERE phone_hash=? LIMIT 1", (row.get("phone_hash"),)
    )
    if duplicate and int(duplicate["id"]) != int(row.get("converted_member_id") or 0):
        missing.append("疑似已有正式学员档案，需先人工核对")
    return missing


def get_enrollment_application(actor_user_id: int, application_id: int) -> dict[str, Any]:
    row = _application_row(application_id)
    if not row:
        raise ValueError("入塾申请不存在")
    _assert_application_scope(actor_user_id, row)
    user = user_context(actor_user_id) or {"permissions": []}
    can_view_contact = "enrollment:read" in user["permissions"]
    can_view_financial = "members:enterprise_view" in user["permissions"]
    can_view_payment_detail = (
        "enrollment:payment_confirm" in user["permissions"]
    )
    financial_values: dict[str, str] = {}
    if row.get("enterprise_financial_ciphertext") and can_view_financial:
        financial_values = _financial_values(row["enterprise_financial_ciphertext"])
        with transaction() as connection:
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="enrollment.application.financial_view",
                resource_type="member_enrollment_application",
                resource_id=str(application_id),
                org_unit_id=row.get("org_unit_id"),
                after={"fields": sorted(financial_values)},
            )
    excluded = {
        "phone_ciphertext",
        "phone_hash",
        "phone_last4",
        "active_phone_guard",
        "enterprise_financial_ciphertext",
        *INVOICE_SENSITIVE_FIELDS,
    }
    safe = _json_safe_row({key: value for key, value in row.items() if key not in excluded})
    if not can_view_payment_detail:
        safe.pop("payment_amount", None)
        safe.pop("payment_note", None)
    safe["duplicate_member_risk"] = bool(row["duplicate_member_risk"])
    if can_view_contact:
        safe["phone"] = decrypt_text(row["phone_ciphertext"])
    safe["computed_status"] = _computed_status(row)
    safe["has_enterprise_financial_data"] = bool(row.get("enterprise_financial_ciphertext"))
    safe["financial_fields_visible"] = can_view_financial
    safe["annual_sales"] = financial_values.get("annual_sales")
    safe["profit_margin"] = financial_values.get("profit_margin")
    safe["invoice_fields_visible"] = can_view_financial
    if can_view_financial:
        safe.update(
            {
                key: row.get(key)
                for key in INVOICE_SENSITIVE_FIELDS
                if key != "invoice_info"
            }
        )
        safe["invoice_info"] = row.get("invoice_info")
    safe["rules_acknowledged"] = bool(row.get("rules_acknowledged"))
    safe["missing_gates"] = _missing_enrollment_gates(row)
    safe["can_enroll"] = (
        row["application_status"] != "ENROLLED" and not safe["missing_gates"]
    )
    return safe


def _organization_is_descendant(
    org_unit_id: str, target_shuku_org_unit_id: str
) -> bool:
    rows = fetch_all(
        "WITH RECURSIVE descendants(id) AS ("
        " SELECT id FROM org_units WHERE id=? AND is_active=1 "
        " UNION ALL SELECT o.id FROM org_units o JOIN descendants d ON o.parent_id=d.id "
        " WHERE o.is_active=1"
        ") SELECT id FROM descendants WHERE id=?",
        (target_shuku_org_unit_id, org_unit_id),
    )
    return bool(rows)


def _validate_target_org(
    actor_user_id: int,
    org_unit_id: str | None,
    target_shuku_org_unit_id: str | None = None,
) -> None:
    if not org_unit_id:
        return
    org = fetch_one(
        "SELECT unit_type, parent_id, is_active FROM org_units WHERE id=?", (org_unit_id,)
    )
    if (
        not org
        or not org["is_active"]
        or not is_valid_member_primary_org(
            org_unit_id=org_unit_id,
            unit_type=org["unit_type"],
            parent_id=org.get("parent_id"),
        )
    ):
        raise ValueError("只能选择有效的正式管理组织")
    if target_shuku_org_unit_id:
        _validate_target_shuku(target_shuku_org_unit_id)
        if not _organization_is_descendant(org_unit_id, target_shuku_org_unit_id):
            raise ValueError("最终管理组织必须属于申请加入的塾")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and org_unit_id not in allowed:
        raise PermissionError("不能选择授权范围外的分中心")


def review_enrollment_application(
    actor_user_id: int,
    application_id: int,
    *,
    decision: str,
    updates: dict[str, Any],
    review_note: str | None,
) -> dict[str, Any]:
    current = _application_row(application_id)
    if not current:
        raise ValueError("入塾申请不存在")
    _assert_application_scope(actor_user_id, current)
    if current["application_status"] not in EDITABLE_APPLICATION_STATUSES:
        raise ValueError("当前申请状态不能再审核或修改")
    if decision not in {"SAVE", "APPROVE"}:
        raise ValueError("审核动作无效")
    incoming = dict(updates)
    unknown = set(incoming) - REVIEW_FIELDS - FINANCIAL_FIELDS
    if unknown:
        raise ValueError("包含不允许修改的申请字段")
    target_id = _clean_optional(
        incoming.get("target_shuku_org_unit_id", current.get("target_shuku_org_unit_id"))
    )
    if "target_shuku_org_unit_id" in incoming:
        target = _validate_target_shuku(target_id)
        incoming["target_shuku_org_unit_id"] = target["id"] if target else None
    elif target_id:
        _validate_target_shuku(target_id)
    if decision == "APPROVE" and not target_id:
        raise ValueError("审核通过前必须先选择申请加入的塾")
    if "org_unit_id" in incoming:
        if incoming.get("org_unit_id") and not target_id:
            raise ValueError("选择正式管理组织前必须先选择申请加入的塾")
        _validate_target_org(
            actor_user_id, incoming.get("org_unit_id"), target_id
        )
    elif current.get("org_unit_id") and target_id:
        _validate_target_org(actor_user_id, current.get("org_unit_id"), target_id)
    if "name" in incoming and not (incoming.get("name") or "").strip():
        raise ValueError("姓名不能为空")
    if "gender" in incoming and incoming.get("gender") not in {None, "MALE", "FEMALE"}:
        raise ValueError("性别只能选择男或女")

    political_input_fields = {"political_status", "social_role"}.intersection(incoming)
    if political_input_fields:
        political_payload = {
            "political_status": current.get("political_status"),
            "social_role": current.get("social_role"),
        }
        political_payload.update(
            {key: incoming[key] for key in political_input_fields if key in incoming}
        )
        political_values = _canonical_political(political_payload)
        for key in political_input_fields:
            incoming.pop(key, None)
        incoming.update(political_values)

    user = user_context(actor_user_id) or {"permissions": []}
    invoice_input_fields = INVOICE_FIELDS.intersection(incoming)
    if invoice_input_fields:
        invoice_payload = _invoice_fields_from_row(current)
        invoice_payload.update(
            {key: incoming[key] for key in invoice_input_fields if key in incoming}
        )
        invoice_values = _canonical_invoice(invoice_payload)
        for key in INVOICE_FIELDS:
            incoming.pop(key, None)
        incoming.update(invoice_values)
    financial_changes = FINANCIAL_FIELDS.intersection(incoming)
    if (financial_changes or invoice_input_fields) and "members:enterprise_view" not in user["permissions"]:
        raise PermissionError("当前角色不能维护企业敏感财务资料")

    industry_input_fields = {"industry", "industry_category", "industry_other"}.intersection(
        incoming
    )
    if industry_input_fields:
        industry_payload = {
            "industry_category": current.get("industry_category"),
            "industry": current.get("industry"),
            "industry_other": current.get("industry_other"),
        }
        industry_payload.update(
            {key: incoming[key] for key in industry_input_fields if key in incoming}
        )
        industry_values = _canonical_industry(industry_payload)
        for key in industry_input_fields:
            incoming.pop(key, None)
        incoming.update(industry_values)
    if "profit_margin" in incoming:
        incoming["profit_margin"] = _canonical_profit_margin(incoming["profit_margin"])
    for key, label, integer_only in (
        ("goal_years", "计划学习年限", True),
        ("revenue_growth_target", "业绩目标", False),
        ("profit_growth_target", "利润目标", False),
    ):
        if key in incoming:
            incoming[key] = _canonical_goal_value(
                incoming[key], label=label, integer_only=integer_only
            )

    financial_ciphertext = current.get("enterprise_financial_ciphertext")
    if financial_changes:
        financial = _financial_values(financial_ciphertext)
        for key in financial_changes:
            value = _clean_optional(incoming.pop(key))
            if value:
                financial[key] = value
            else:
                financial.pop(key, None)
        financial_ciphertext = (
            encrypt_text(json.dumps(financial, ensure_ascii=False)) if financial else None
        )

    now = _now()
    assignments: list[str] = []
    params: list[Any] = []
    changed_fields = set(incoming) | financial_changes | invoice_input_fields
    target_changed = (
        "target_shuku_org_unit_id" in incoming
        and incoming.get("target_shuku_org_unit_id")
        != current.get("target_shuku_org_unit_id")
    )
    for field in sorted(incoming):
        value = incoming[field]
        if isinstance(value, str):
            value = _clean_optional(value)
        assignments.append(f"{field}=?")
        params.append(value)
    if financial_changes:
        assignments.append("enterprise_financial_ciphertext=?")
        params.append(financial_ciphertext)
    if decision == "APPROVE":
        assignments.extend(
            ["application_status='APPROVED'", "reviewed_by=?", "reviewed_at=?", "review_note=?"]
        )
        params.extend((actor_user_id, now, _clean_optional(review_note)))
    assignments.append("updated_at=?")
    params.append(now)
    params.append(application_id)
    with transaction() as connection:
        execute(
            connection,
            "UPDATE member_enrollment_applications SET "
            + ", ".join(assignments)
            + " WHERE id=?",
            tuple(params),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=(
                "enrollment.application.approve"
                if decision == "APPROVE"
                else "enrollment.application.edit"
            ),
            resource_type="member_enrollment_application",
            resource_id=str(application_id),
            org_unit_id=incoming.get("org_unit_id", current.get("org_unit_id")),
            before={
                "status": current["application_status"],
                **(
                    {
                        "target_shuku_org_unit_id": current.get(
                            "target_shuku_org_unit_id"
                        )
                    }
                    if target_changed
                    else {}
                ),
            },
            after={
                "status": "APPROVED" if decision == "APPROVE" else current["application_status"],
                "changed_fields": sorted(changed_fields),
                **(
                    {
                        "target_shuku_org_unit_id": incoming.get(
                            "target_shuku_org_unit_id"
                        ),
                        "target_shuku_changed": True,
                    }
                    if target_changed
                    else {}
                ),
            },
        )
    return get_enrollment_application(actor_user_id, application_id)


def confirm_enrollment_payment(
    actor_user_id: int,
    application_id: int,
    *,
    payment_status: str,
    amount: Decimal | None,
    note: str | None,
) -> dict[str, Any]:
    if payment_status != "PAID":
        raise ValueError("V1 仅开放已收款确认；减免或特批需后续业务授权")
    current = _application_row(application_id)
    if not current:
        raise ValueError("入塾申请不存在")
    _assert_application_scope(actor_user_id, current)
    if current["application_status"] not in ACTIVE_APPLICATION_STATUSES:
        raise ValueError("当前申请状态不能确认收款")
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE member_enrollment_applications SET payment_status='PAID', "
            "payment_amount=?, payment_note=?, payment_confirmed_by=?, "
            "payment_confirmed_at=?, updated_at=? WHERE id=?",
            (
                str(amount) if amount is not None else None,
                _clean_optional(note),
                actor_user_id,
                now,
                now,
                application_id,
            ),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.application.payment_confirm",
            resource_type="member_enrollment_application",
            resource_id=str(application_id),
            org_unit_id=current.get("org_unit_id"),
            after={"payment_status": "PAID"},
        )
    return get_enrollment_application(actor_user_id, application_id)


def reject_enrollment_application(
    actor_user_id: int, application_id: int, reason: str
) -> dict[str, Any]:
    current = _application_row(application_id)
    if not current:
        raise ValueError("入塾申请不存在")
    _assert_application_scope(actor_user_id, current)
    if current["application_status"] == "ENROLLED":
        raise ValueError("已正式入塾的申请不能驳回")
    if current["application_status"] == "REJECTED":
        return get_enrollment_application(actor_user_id, application_id)
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE member_enrollment_applications SET application_status='REJECTED', "
            "active_phone_guard=NULL, rejected_by=?, rejected_at=?, rejection_reason=?, "
            "updated_at=? WHERE id=?",
            (actor_user_id, now, reason.strip(), now, application_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.application.reject",
            resource_type="member_enrollment_application",
            resource_id=str(application_id),
            org_unit_id=current.get("org_unit_id"),
            before={"status": current["application_status"]},
            after={"status": "REJECTED", "reason_recorded": True},
        )
    return get_enrollment_application(actor_user_id, application_id)


def enroll_application(actor_user_id: int, application_id: int) -> dict[str, Any]:
    with transaction() as connection:
        row = _application_row(application_id, connection, lock=True)
        if not row:
            raise ValueError("入塾申请不存在")
        _assert_application_scope(actor_user_id, row)
        if row["application_status"] == "ENROLLED" and row.get("converted_member_id"):
            return {
                "application_id": application_id,
                "member_id": int(row["converted_member_id"]),
                "idempotent": True,
            }
        missing = _missing_enrollment_gates(row)
        if missing:
            raise ValueError("；".join(missing))
        member_id = create_member(
            actor_user_id,
            member_code=None,
            name=row["name"],
            org_unit_id=row["org_unit_id"],
            development_org_unit_id=None,
            phone=None,
            company_name=row.get("company_name"),
            gender=row.get("gender"),
            district=row.get("district"),
            company_address=row.get("company_address"),
            birthday=_json_safe(row.get("birthday")),
            join_date=_json_safe(row.get("join_date")),
            status="ACTIVE",
            position=row.get("position"),
            referrer=row.get("referrer"),
            industry_category=row.get("industry_category"),
            industry=row.get("industry"),
            company_products=row.get("company_products"),
            employee_count=row.get("employee_count"),
            notes=row.get("notes"),
            connection=connection,
            source_type="ENROLLMENT_APPLICATION",
            audit_action="members.create_from_enrollment",
            protected_phone_fields={
                "phone_ciphertext": row["phone_ciphertext"],
                "phone_hash": row["phone_hash"],
                "phone_last4": row["phone_last4"],
                "phone_masked": row["phone_masked"],
            },
            enterprise_financial_ciphertext=row.get("enterprise_financial_ciphertext"),
        )
        now = _now()
        execute(
            connection,
            "UPDATE member_enrollment_applications SET application_status='ENROLLED', "
            "active_phone_guard=NULL, converted_member_id=?, converted_by=?, converted_at=?, "
            "updated_at=? WHERE id=? AND converted_member_id IS NULL",
            (member_id, actor_user_id, now, now, application_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="enrollment.application.enroll",
            resource_type="member_enrollment_application",
            resource_id=str(application_id),
            org_unit_id=row["org_unit_id"],
            before={"status": row["application_status"]},
            after={"status": "ENROLLED", "member_id": member_id},
        )
        return {
            "application_id": application_id,
            "member_id": int(member_id),
            "idempotent": False,
        }
