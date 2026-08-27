"""Private binary storage; DB stores metadata only. No public URLs or client keys."""
from __future__ import annotations

import io
import os
import re
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.settings import get_settings

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 24_000_000
KEY_PATTERN = re.compile(r"study-evidence/(dev|test|staging|production)/[0-9]+/[a-f0-9]{32}\.(jpg|png)\Z")


class EvidenceStorageError(ValueError):
    pass


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
        self.backend = os.getenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
        if self.backend == "local":
            if self.environment not in {"dev", "test"}:
                raise EvidenceStorageError("非本地环境禁止使用本地合影存储")
            configured = os.getenv("STUDY_EVIDENCE_LOCAL_ROOT", "")
            if not configured:
                raise EvidenceStorageError("请配置独立的本地合影目录")
            self.root = Path(configured).resolve()
            self.namespace = str(self.root)
        elif self.backend == "cos":
            from qcloud_cos import CosConfig, CosS3Client
            self.bucket = os.getenv("STUDY_EVIDENCE_COS_BUCKET", "")
            region = os.getenv("STUDY_EVIDENCE_COS_REGION", "")
            secret_id = os.getenv("STUDY_EVIDENCE_COS_SECRET_ID", "")
            secret_key = os.getenv("STUDY_EVIDENCE_COS_SECRET_KEY", "")
            if not all((self.bucket, region, secret_id, secret_key)):
                raise EvidenceStorageError("学习合影私有存储尚未配置")
            self.namespace = f"{region}/{self.bucket}"
            self.client = CosS3Client(CosConfig(
                Region=region, SecretId=secret_id, SecretKey=secret_key,
                Token=os.getenv("STUDY_EVIDENCE_COS_SESSION_TOKEN") or None,
                Scheme="https", Timeout=15,
            ))
        else:
            raise EvidenceStorageError("未知合影存储类型")

    def _key(self, key: str) -> str:
        if not KEY_PATTERN.fullmatch(key) or key.split("/")[1] != self.environment:
            raise EvidenceStorageError("合影存储路径无效")
        return key

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
                                       ContentType=content_type, ACL="private", EnableMD5=True)
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
            raise EvidenceStorageError("合影清理未完成，可稍后重试") from exc
