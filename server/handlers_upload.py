"""上传相关的 HTTP handler mixin。

从 handlers.py 拆出：文件上传、multipart 解析、BizyAir/ImgBB 分发、本地归档。
所有方法都依赖 LanGatewayHandler 上的 _send_json、_audit、_absolute_url
等基础方法，因此以 mixin 形式挂到 LanGatewayHandler 上使用。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .bizyUpImage import BizyUpImage
from .imgbb_uploader import ImgBBUploader
from .input_images import store_input_image
from .logging_utils import redact
from .schemas import ALLOWED_UPLOAD_EXTENSIONS, ALLOWED_UPLOAD_MIME_PREFIXES


def extract_upload_url(data) -> str:
    """递归从各种上游返回中提取图片 URL。"""
    if isinstance(data, str) and data.startswith(("http://", "https://")):
        return data
    if isinstance(data, list):
        for item in data:
            url = extract_upload_url(item)
            if url:
                return url
    if isinstance(data, dict):
        for key in ("url", "uri", "src", "download_url", "resource_url", "object_url"):
            url = extract_upload_url(data.get(key))
            if url:
                return url
        for value in data.values():
            url = extract_upload_url(value)
            if url:
                return url
    return ""


class UploadHandlerMixin:
    def _handle_upload(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            raise ValueError("上传内容为空")
        if content_length > self.server.config.max_upload_bytes:
            raise ValueError(f"上传文件不能超过 {self.server.config.max_upload_bytes // 1024 // 1024} MB")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("Content-Type 必须是 multipart/form-data")
        filename, mime_type, file_data = self._read_multipart_file(content_length, content_type)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValueError("只允许上传 png、jpg、jpeg、webp、gif 图片")
        if mime_type and not mime_type.startswith(ALLOWED_UPLOAD_MIME_PREFIXES):
            raise ValueError("只允许上传图片文件")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_path = temp_file.name
                temp_file.write(file_data)
            if self._should_store_upload_locally():
                data = self._store_input_image(file_data, filename, mime_type, suffix)
                provider, key_id = "local", ""
            else:
                data, provider, key_id = self._upload_input_image(temp_path, filename)
            self._audit(
                "upload",
                "ok",
                {
                    "filename_hash": hashlib.sha256(filename.encode("utf-8", "ignore")).hexdigest()[:16],
                    "provider": provider,
                    "key_id": key_id,
                },
            )
            self._send_json({"status": True, "data": data})
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _should_store_upload_locally(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        return query.get("storage", [""])[0] == "local"

    def _store_input_image(self, file_data: bytes, filename: str, mime_type: str, suffix: str) -> dict:
        image = store_input_image(self.server.config.input_image_dir, file_data, filename, mime_type, suffix)
        return {
            "id": image.id,
            "url": self._absolute_url(f"/api/input-images/{image.id}/file"),
            "provider": "local",
            "filename": image.filename,
            "mime_type": image.content_type,
            "size": image.size,
            "sha256": image.sha256,
        }

    def _upload_input_image(self, temp_path: str, filename: str) -> tuple[dict, str, str]:
        default_error: Exception | None = None
        key_id = ""
        try:
            key = self.server.runner.key_pool.pick()
            key_id = key.id
            client = BizyUpImage(
                api_key=key.api_key,
                retry_attempts=self.server.config.upload_retry_attempts,
                retry_delay_seconds=self.server.config.upload_retry_delay_seconds,
            )
            data = client.upload(temp_path, file_name=filename)
            if not extract_upload_url(data):
                raise RuntimeError("BizyAir 上传成功但未能解析出图片 URL")
            return data, "bizyair", key_id
        except Exception as exc:
            default_error = exc
            self.server.logger.warning("默认上传失败，准备切换到 ImgBB: %s", redact(exc, self.server.config))

        if not self.server.config.imgbb_api_key:
            raise RuntimeError(f"默认上传失败，且未配置 IMGBB_API_KEY: {default_error}") from default_error

        fallback = ImgBBUploader(
            api_key=self.server.config.imgbb_api_key,
            timeout=self.server.config.imgbb_timeout_seconds,
        )
        data = fallback.upload(temp_path, name=filename)
        if not extract_upload_url(data):
            raise RuntimeError("ImgBB 上传成功但未能解析出图片 URL")
        self.server.logger.info("ImgBB 兜底上传完成: %s", filename)
        return data, "imgbb", key_id

    def _read_multipart_file(self, content_length: int, content_type: str) -> tuple[str, str, bytes]:
        raw_body = self.rfile.read(content_length)
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=policy.default).parsebytes(header + raw_body)
        if not message.is_multipart():
            raise ValueError("Content-Type 必须是 multipart/form-data")
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") != "file":
                continue
            filename = part.get_filename()
            if not filename:
                raise ValueError("缺少上传文件字段 file")
            file_data = part.get_payload(decode=True) or b""
            if not file_data:
                raise ValueError("上传内容为空")
            self._debug_log_multipart(
                {},
                [{"field": "file", "filename": filename, "mime": part.get_content_type() or "", "data": file_data}],
            )
            return filename, part.get_content_type() or "", file_data
        raise ValueError("缺少上传文件字段 file")

    def _read_multipart_form(self, content_length: int, content_type: str) -> tuple[dict[str, str], list[dict]]:
        raw_body = self.rfile.read(content_length)
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=policy.default).parsebytes(header + raw_body)
        if not message.is_multipart():
            raise ValueError("Content-Type 必须是 multipart/form-data")
        text_fields: dict[str, str] = {}
        files: list[dict] = []
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if not field_name:
                continue
            filename = part.get_filename()
            if filename:
                file_data = part.get_payload(decode=True) or b""
                if not file_data:
                    continue
                files.append(
                    {
                        "field": field_name,
                        "filename": filename,
                        "mime": part.get_content_type() or "",
                        "data": file_data,
                    }
                )
            else:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text_fields[field_name] = payload.decode("utf-8", errors="replace").strip()
        self._debug_log_multipart(text_fields, files)
        return text_fields, files

    @staticmethod
    def _extract_upload_url(data) -> str:
        return extract_upload_url(data)
