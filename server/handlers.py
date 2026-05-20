from email import policy
from email.parser import BytesParser
import hashlib
import hmac
import json
import mimetypes
import os
import re
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .bizyUpImage import BizyUpImage
from .config import APP_VERSION, PROJECT_ROOT, now_iso
from .logging_utils import redact
from .schemas import ALLOWED_UPLOAD_EXTENSIONS, ALLOWED_UPLOAD_MIME_PREFIXES, MODEL_SCHEMAS, validate_params
from .upstream_client import UpstreamClient, summarize_account

class LanGatewayHandler(BaseHTTPRequestHandler):
    server: object

    def do_OPTIONS(self) -> None:
        self._send_json({"status": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/health":
                self._handle_health()
            elif parsed.path == "/api/config":
                self._require_auth("config")
                self._send_json({"status": True, "data": self._public_config()})
            elif parsed.path == "/api/models":
                self._require_auth("models")
                self._send_json({"status": True, "data": MODEL_SCHEMAS})
            elif parsed.path == "/api/inputs":
                self._require_auth("inputs")
                self._handle_inputs(parsed)
            elif parsed.path == "/api/account":
                self._require_auth("account")
                self._handle_account()
            elif parsed.path == "/api/image-cache":
                self._handle_image_cache(parsed)
            elif parsed.path.startswith("/api/images/"):
                self._handle_local_image(parsed.path)
            elif parsed.path == "/api/jobs":
                self._require_auth("jobs:list")
                self._send_json({"status": True, "data": self.server.db.list_jobs()})
            elif parsed.path.startswith("/api/jobs/"):
                self._require_auth("jobs:get")
                job_id = parsed.path.removeprefix("/api/jobs/").strip("/")
                if "/" in job_id or not job_id:
                    self._send_json({"status": False, "message": "Not found"}, 404)
                    return
                self._send_json({"status": True, "data": self.server.db.get_job(job_id)})
            elif parsed.path.startswith("/api/"):
                self._send_json({"status": False, "message": "Not found"}, 404)
            else:
                self._serve_static(parsed.path)
        except PermissionError as exc:
            self._send_json({"status": False, "message": str(exc)}, 401)
        except KeyError as exc:
            self._send_json({"status": False, "message": str(exc)}, 404)
        except ValueError as exc:
            self._send_json({"status": False, "message": str(exc)}, 400)
        except Exception as exc:
            self.server.logger.exception("GET 处理失败: %s", redact(exc, self.server.config))
            self._send_json({"status": False, "message": redact(exc, self.server.config)}, 500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/images/"):
                self._require_auth("images:delete")
                image_id = parsed.path.removeprefix("/api/images/").strip("/")
                if "/" in image_id or not re.fullmatch(r"[a-f0-9]{32}", image_id):
                    self._send_json({"status": False, "message": "Not found"}, 404)
                    return
                image = self.server.db.delete_job_image(image_id)
                self._delete_local_image_file(image)
                self._audit("images:delete", "ok", {"image_id": image_id})
                self._send_json({"status": True, "data": {"id": image_id}})
            else:
                self._send_json({"status": False, "message": "Not found"}, 404)
        except PermissionError as exc:
            self._send_json({"status": False, "message": str(exc)}, 401)
        except KeyError as exc:
            self._send_json({"status": False, "message": str(exc)}, 404)
        except ValueError as exc:
            self._send_json({"status": False, "message": str(exc)}, 400)
        except Exception as exc:
            self.server.logger.exception("DELETE 处理失败: %s", redact(exc, self.server.config))
            self._send_json({"status": False, "message": redact(exc, self.server.config)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/upload":
                self._require_auth("upload")
                self._handle_upload()
            elif parsed.path == "/api/jobs":
                self._require_auth("jobs:create")
                self._handle_create_job()
            elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                self._require_auth("jobs:cancel")
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/")
                job = self.server.db.cancel_job(job_id)
                self._audit("jobs:cancel", "ok", {"job_id": job_id})
                self._send_json({"status": True, "data": job})
            elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/retry"):
                self._require_auth("jobs:create")
                job_id = parsed.path.removeprefix("/api/jobs/").removesuffix("/retry").strip("/")
                job = self.server.db.retry_job(job_id)
                self.server.runner.enqueue_job(job)
                self._audit("jobs:retry", "ok", {"job_id": job_id})
                self._send_json({"status": True, "data": job})
            else:
                self._send_json({"status": False, "message": "Not found"}, 404)
        except PermissionError as exc:
            self._send_json({"status": False, "message": str(exc)}, 401)
        except KeyError as exc:
            self._send_json({"status": False, "message": str(exc)}, 404)
        except ValueError as exc:
            self._send_json({"status": False, "message": str(exc)}, 400)
        except Exception as exc:
            self.server.logger.exception("POST 处理失败: %s", redact(exc, self.server.config))
            self._send_json({"status": False, "message": redact(exc, self.server.config)}, 500)

    def _handle_health(self) -> None:
        self._send_json(
            {
                "status": True,
                "data": {
                    "version": APP_VERSION,
                    "time": now_iso(),
                    "queue_length": self.server.runner.size(),
                    "storage": "ready",
                },
            }
        )

    def _handle_inputs(self, parsed) -> None:
        query = parse_qs(parsed.query)
        current = max(1, int(query.get("current", ["1"])[0]))
        page_size = min(max(1, int(query.get("page_size", ["20"])[0])), 100)
        key_results = []
        for key in self.server.config.bizyair_keys:
            client = BizyUpImage(api_key=key.api_key)
            key_results.append({"id": key.id, "label": key.label or key.id, "data": client.list_inputs(current=current, page_size=page_size)})
        self._audit("inputs:list", "ok", {"current": current, "page_size": page_size, "keys": len(key_results)})
        data = key_results[0]["data"] if len(key_results) == 1 else {"keys": key_results}
        self._send_json({"status": True, "data": data})

    def _handle_image_cache(self, parsed) -> None:
        query = parse_qs(parsed.query)
        url = query.get("url", [""])[0]
        if not url:
            raise ValueError("缺少图片 URL")
        path, content_type = self.server.image_cache.get(url)
        data = path.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_local_image(self, request_path: str) -> None:
        tail = request_path.removeprefix("/api/images/").strip("/")
        download = tail.endswith("/download")
        image_id = tail.removesuffix("/download").strip("/")
        if not re.fullmatch(r"[a-f0-9]{32}", image_id):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        image = self.server.db.get_job_image(image_id)
        file_path = Path(image["local_path"]).resolve()
        result_dir = self.server.config.result_image_dir.resolve()
        if result_dir != file_path.parent or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = file_path.read_bytes()
        content_type = image.get("content_type") or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            filename = f"bizyair-{str(image.get('job_id') or '')[:8]}-{int(image.get('image_index') or 0) + 1}{image.get('extension') or file_path.suffix}"
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _delete_local_image_file(self, image: dict) -> None:
        try:
            file_path = Path(image.get("local_path") or "").resolve()
            result_dir = self.server.config.result_image_dir.resolve()
            if result_dir == file_path.parent and file_path.is_file():
                file_path.unlink()
        except Exception as exc:
            self.server.logger.warning("本地图片文件删除失败: %s", redact(exc, self.server.config))

    def _handle_account(self) -> None:
        summaries = []
        for key in self.server.config.bizyair_keys:
            wallet, metadata, wallet_data, metadata_data = self.server.upstream_client.get_account(key)
            if not wallet.ok or not metadata.ok:
                summaries.append({"id": key.id, "label": key.label or key.id, "status": "查询失败"})
                continue
            summary = summarize_account(wallet_data, metadata_data)
            summary.update({"id": key.id, "label": key.label or key.id})
            summaries.append(summary)
        self._audit("account:get", "ok", {"keys": len(summaries)})
        if len(summaries) == 1:
            self._send_json({"status": True, "data": summaries[0]})
            return
        self._send_json({"status": True, "data": {"keys": summaries}})

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
            key = self.server.runner.key_pool.pick()
            client = BizyUpImage(api_key=key.api_key)
            data = client.upload(temp_path, file_name=filename)
            self._audit("upload", "ok", {"filename_hash": hashlib.sha256(filename.encode("utf-8", "ignore")).hexdigest()[:16], "key_id": key.id})
            self._send_json({"status": True, "data": data})
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

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
            return filename, part.get_content_type() or "", file_data
        raise ValueError("缺少上传文件字段 file")

    def _handle_create_job(self) -> None:
        body = self._read_json_body()
        model = str(body.get("model", "")).strip()
        if model not in MODEL_SCHEMAS:
            raise ValueError("不支持的模型")
        prompts = body.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError("prompts 必须是数组")
        prompts = [str(prompt).strip() for prompt in prompts if str(prompt).strip()]
        if not prompts:
            raise ValueError("请输入提示词")
        if len(prompts) > 100:
            raise ValueError("单个批次最多 100 条提示词")
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params 必须是对象")
        params = validate_params(model, params)
        job = self.server.db.create_job(model, prompts, params)
        self.server.runner.enqueue_job(job)
        self._audit("jobs:create", "ok", {"job_id": job["id"], "model": model, "total": len(prompts)})
        self._send_json({"status": True, "data": job}, 201)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}
        if content_length > 2 * 1024 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _require_auth(self, action: str) -> None:
        auth = self.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if not token or not hmac.compare_digest(token, self.server.config.admin_token):
            self._audit(action, "denied", {})
            raise PermissionError("未授权")

    def _audit(self, action: str, status: str, detail: dict) -> None:
        safe_detail = json.loads(json.dumps(detail, ensure_ascii=False)) if detail else {}
        ip = self.client_address[0] if self.client_address else "unknown"
        try:
            self.server.db.insert_audit(ip, action, status, safe_detail)
        except Exception:
            self.server.logger.exception("审计日志写入数据库失败")
        self.server.audit_logger.info(json.dumps({"timestamp": now_iso(), "ip": ip, "action": action, "status": status, "detail": safe_detail}, ensure_ascii=False))

    def _serve_static(self, request_path: str) -> None:
        path = unquote(request_path.split("?", 1)[0])
        if path in ("", "/"):
            file_path = PROJECT_ROOT / "index.html"
        else:
            relative = Path(path.lstrip("/"))
            if ".." in relative.parts or relative.parts[:1] not in [("static",), ("assets",)]:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_path = PROJECT_ROOT / relative
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        resolved = file_path.resolve()
        if resolved != (PROJECT_ROOT / "index.html").resolve() and PROJECT_ROOT.resolve() not in resolved.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin and origin in self.server.config.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _public_config(self) -> dict:
        return {
            "version": APP_VERSION,
            "poll_interval_seconds": self.server.config.poll_interval_seconds,
            "max_upload_mb": self.server.config.max_upload_bytes // 1024 // 1024,
            "worker_threads": self.server.config.worker_threads,
            "bizyair_keys": [
                {"id": key.id, "label": key.label or key.id}
                for key in self.server.config.bizyair_keys
            ],
            "models": list(MODEL_SCHEMAS.keys()),
        }

    @staticmethod
    def _json_response(response: requests.Response) -> dict:
        return UpstreamClient.json_response(response)

    def log_message(self, fmt: str, *args) -> None:
        self.server.logger.info("%s - %s", self.address_string(), fmt % args)
