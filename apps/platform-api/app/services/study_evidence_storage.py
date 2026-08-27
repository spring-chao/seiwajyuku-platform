"""Private binary storage; DB stores metadata only. No public URLs or client keys."""
from __future__ import annotations

import io
import os
import re
import warnings
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.settings import get_settings

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 24_000_000
LEGACY_KEY_PATTERN = re.compile(
    r"study-evidence/(dev|test|staging|production)/[0-9]+/[a-f0-9]{32}\.(jpg|png)\Z"
)
# Backward-compatible export for callers that used the legacy local/COS key
# validator directly.
KEY_PATTERN = LEGACY_KEY_PATTERN
DEFAULT_CLOUDBASE_PREFIX = "study-meetings/"


class EvidenceStorageError(ValueError):
    pass


class _EnvironmentCredential:
    """A refresh-friendly credential view for the COS SDK.

    CloudRun/CloudBase can rotate temporary credentials without restarting the
    process.  The SDK reads these properties for each request, so the adapter
    never needs to copy a token into application state.  The object deliberately
    exposes no repr/str implementation, preventing accidental secret logging.
    """

    def __init__(self, secret_id_name: str, secret_key_name: str, token_name: str) -> None:
        self._secret_id_name = secret_id_name
        self._secret_key_name = secret_key_name
        self._token_name = token_name

    @property
    def secret_id(self) -> str:
        return os.getenv(self._secret_id_name, "")

    @property
    def secret_key(self) -> str:
        return os.getenv(self._secret_key_name, "")

    @property
    def token(self) -> str:
        return os.getenv(self._token_name, "")


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _missing_object_error(exc: Exception) -> bool:
    """Return True only for an object-not-found response, never a missing bucket."""

    code: str | None = None
    getter = getattr(exc, "get_error_code", None)
    if callable(getter):
        try:
            value = getter()
            if value not in (None, "", "Unknown"):
                code = str(value)
        except Exception:
            code = None
    if not code:
        code = str(getattr(exc, "code", "") or getattr(exc, "error_code", "")) or None
    if code and code.lower() in {"nosuchkey", "nosuchobject", "notfound", "no_such_key"}:
        return True
    status: int | None = None
    getter = getattr(exc, "get_status_code", None)
    if callable(getter):
        try:
            status = int(getter())
        except Exception:
            status = None
    if status is None:
        raw_status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        try:
            status = int(raw_status) if raw_status is not None else None
        except (TypeError, ValueError):
            status = None
    # A bare 404 from a compatible SDK is an object miss.  If COS supplied a
    # different error code (for example NoSuchBucket), it must remain a failure.
    return status == 404 and not code


def normalize_image(content: bytes, declared_type: str) -> tuple[bytes, str, str]:
    """Decode then re-encode pixels only (discard EXIF/GPS/trailing payloads)."""
    if not content or len(content) > MAX_BYTES:
        raise EvidenceStorageError("合影不能为空且不能超过5MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                fmt = probe.format
                expected = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(fmt)
                declared = declared_type.lower().split(";", 1)[0].strip()
                if not expected or declared not in {expected, "", "application/octet-stream"}:
                    raise EvidenceStorageError("请上传真实的 JPG/JPEG/PNG 图片")
                if probe.width * probe.height > MAX_PIXELS or getattr(probe, "n_frames", 1) != 1:
                    raise EvidenceStorageError("图片尺寸过大或包含多帧，请重新选择合影")
                probe.verify()
            with Image.open(io.BytesIO(content)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                clean = Image.new("RGB", oriented.size)
                clean.paste(oriented)
                output = io.BytesIO()
                clean.save(output, format=fmt, **({"quality": 88} if fmt == "JPEG" else {}))
                result = output.getvalue()
                if len(result) > MAX_BYTES:
                    raise EvidenceStorageError("图片处理后超过5MB，请压缩后重试")
                return result, expected, "jpg" if fmt == "JPEG" else "png"
    except EvidenceStorageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise EvidenceStorageError("图片损坏或格式无效，请重新选择合影") from exc


class EvidenceStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.environment = settings.app_env
        self.backend = os.getenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local").strip().lower()
        if self.backend == "local":
            if self.environment not in {"dev", "test"}:
                raise EvidenceStorageError("非本地环境禁止使用本地合影存储")
            configured = os.getenv("STUDY_EVIDENCE_LOCAL_ROOT", "")
            if not configured:
                raise EvidenceStorageError("请配置独立的本地合影目录")
            self.root = Path(configured).resolve()
            self.namespace = str(self.root)
        elif self.backend in {"cos", "cloudbase"}:
            from qcloud_cos import CosConfig, CosS3Client
            if self.backend == "cloudbase":
                self.bucket = _first_env(
                    "CLOUDBASE_STORAGE_BUCKET", "STUDY_EVIDENCE_COS_BUCKET"
                )
                region = _first_env(
                    "CLOUDBASE_STORAGE_REGION", "STUDY_EVIDENCE_COS_REGION"
                )
                self.prefix = _first_env(
                    "CLOUDBASE_STORAGE_PREFIX"
                ) or DEFAULT_CLOUDBASE_PREFIX
                if self.prefix != DEFAULT_CLOUDBASE_PREFIX:
                    raise EvidenceStorageError(
                        "CloudBase 合影前缀必须固定为 study-meetings/"
                    )
            else:
                self.bucket = os.getenv("STUDY_EVIDENCE_COS_BUCKET", "").strip()
                region = os.getenv("STUDY_EVIDENCE_COS_REGION", "").strip()
                self.prefix = ""
            if not all((self.bucket, region)):
                raise EvidenceStorageError("学习合影私有存储尚未配置")
            self.region = region
            self.namespace = f"{region}/{self.bucket}"
            if self.backend == "cloudbase":
                self.namespace = f"{self.namespace}/{self.prefix}"

            # CloudRun/CloudBase deployments should inject short-lived
            # credentials through the runtime environment.  The property-based
            # CredentialInstance lets the SDK observe rotations between calls.
            runtime_id = _first_env("TENCENTCLOUD_SECRETID", "TENCENTCLOUD_SECRET_ID")
            runtime_key = _first_env("TENCENTCLOUD_SECRETKEY", "TENCENTCLOUD_SECRET_KEY")
            static_id = _first_env(
                "CLOUDBASE_STORAGE_SECRET_ID", "STUDY_EVIDENCE_COS_SECRET_ID"
            )
            static_key = _first_env(
                "CLOUDBASE_STORAGE_SECRET_KEY", "STUDY_EVIDENCE_COS_SECRET_KEY"
            )
            static_token = _first_env(
                "CLOUDBASE_STORAGE_SESSION_TOKEN", "STUDY_EVIDENCE_COS_SESSION_TOKEN"
            )
            config_kwargs: dict[str, object] = {
                "Region": region,
                "Scheme": "https",
                "Timeout": 15,
            }
            if runtime_id and runtime_key:
                self.credential_mode = "runtime"
                config_kwargs["CredentialInstance"] = _EnvironmentCredential(
                    "TENCENTCLOUD_SECRETID"
                    if os.getenv("TENCENTCLOUD_SECRETID")
                    else "TENCENTCLOUD_SECRET_ID",
                    "TENCENTCLOUD_SECRETKEY"
                    if os.getenv("TENCENTCLOUD_SECRETKEY")
                    else "TENCENTCLOUD_SECRET_KEY",
                    "TENCENTCLOUD_SESSIONTOKEN"
                    if os.getenv("TENCENTCLOUD_SESSIONTOKEN")
                    else "TENCENTCLOUD_SESSION_TOKEN",
                )
            elif static_id and static_key:
                self.credential_mode = "static"
                config_kwargs.update(
                    SecretId=static_id,
                    SecretKey=static_key,
                    Token=static_token or None,
                )
            else:
                raise EvidenceStorageError("学习合影私有存储运行凭证尚未配置")
            self.client = CosS3Client(CosConfig(**config_kwargs))
        else:
            raise EvidenceStorageError("未知合影存储类型")

    def _key(self, key: str) -> str:
        if self.backend == "cloudbase":
            pattern = re.compile(
                rf"{re.escape(self.prefix)}(dev|test|staging|production)/"
                r"[0-9]{4}/[0-9]{2}/[a-f0-9]{32}\.(jpg|png)\Z"
            )
            if not pattern.fullmatch(key) or key[len(self.prefix):].split("/")[0] != self.environment:
                raise EvidenceStorageError("合影存储路径无效")
        elif not LEGACY_KEY_PATTERN.fullmatch(key) or key.split("/")[1] != self.environment:
            raise EvidenceStorageError("合影存储路径无效")
        return key

    def make_key(self, *, session_id: int, extension: str) -> str:
        """Generate a backend-safe, non-enumerable object key."""

        if extension not in {"jpg", "png"}:
            raise EvidenceStorageError("合影文件扩展名无效")
        if self.backend == "cloudbase":
            current = datetime.now(UTC)
            key = (
                f"{self.prefix}{self.environment}/{current:%Y}/{current:%m}/"
                f"{uuid4().hex}.{extension}"
            )
        else:
            key = f"study-evidence/{self.environment}/{session_id}/{uuid4().hex}.{extension}"
        return self._key(key)

    def _path(self, key: str) -> Path:
        path = (self.root / self._key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise EvidenceStorageError("合影存储路径越界")
        return path

    def check_namespace(self, row: dict) -> None:
        if row["storage_backend"] != self.backend or row["storage_namespace"] != self.namespace:
            raise EvidenceStorageError("合影存储配置与原记录不一致，请联系管理员")

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self._key(key)
        try:
            if self.backend == "local":
                path = self._path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(content)
            else:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=content,
                                       ContentType=content_type, ACL="private", EnableMD5=True,
                                       IfNoneMatch="*")
        except Exception as exc:
            raise EvidenceStorageError("合影保存失败，请稍后重试") from exc

    def get(self, key: str) -> bytes:
        self._key(key)
        try:
            if self.backend == "local":
                with self._path(key).open("rb") as content:
                    return content.read(MAX_BYTES + 1)
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].get_raw_stream()
            try:
                return body.read(MAX_BYTES + 1)
            finally:
                body.close()
        except Exception as exc:
            raise EvidenceStorageError("合影暂不可用或已清理") from exc

    def delete(self, key: str) -> None:
        self._key(key)
        try:
            if self.backend == "local":
                self._path(key).unlink(missing_ok=True)
            else:
                self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self.backend != "local" and _missing_object_error(exc):
                return
            raise EvidenceStorageError("合影清理未完成，可稍后重试") from exc
