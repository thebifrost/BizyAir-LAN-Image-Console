from email import policy
from email.parser import BytesParser
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .bizyUpImage import BizyUpImage
from .config import APP_VERSION, PROJECT_ROOT, now_iso, update_env_value
from .logging_utils import redact
from .schemas import ALLOWED_UPLOAD_EXTENSIONS, ALLOWED_UPLOAD_MIME_PREFIXES, MODEL_SCHEMAS, validate_params
from .upstream_client import UpstreamClient, summarize_account

class LanGatewayHandler(BaseHTTPRequestHandler):
    server: object

    def do_OPTIONS(self) -> None:
        self._debug_log_request("OPTIONS")
        self._send_json({"status": True})

    def do_GET(self) -> None:
        self._debug_log_request("GET")
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
            elif parsed.path == "/v1/models":
                self._require_auth("openai:models")
                self._handle_openai_models()
            elif parsed.path == "/api/inputs":
                self._require_auth("inputs")
                self._handle_inputs(parsed)
            elif parsed.path == "/api/account":
                self._require_auth("account")
                self._handle_account()
            elif parsed.path == "/api/admin/runtime":
                self._require_auth("admin:runtime")
                self._handle_admin_runtime()
            elif parsed.path == "/api/logs":
                self._require_auth("logs:get")
                self._handle_logs(parsed)
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
        self._debug_log_request("DELETE")
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
        self._debug_log_request("POST")
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/upload":
                self._require_auth("upload")
                self._handle_upload()
            elif parsed.path == "/v1/chat/completions":
                self._require_auth("openai:chat_completions")
                self._handle_openai_chat_completions()
            elif parsed.path == "/v1/images/generations":
                self._require_auth("openai:images_generations")
                self._handle_openai_images_generations()
            elif parsed.path == "/v1/images/edits":
                self._require_auth("openai:images_edits")
                self._handle_openai_images_edits()
            elif parsed.path == "/api/admin/config":
                self._require_auth("admin:config")
                self._handle_admin_config()
            elif parsed.path == "/api/admin/restart":
                self._require_auth("admin:restart")
                self._handle_admin_restart()
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
                    "queue_length": self.server.db.active_queue_size(),
                    "storage": "ready",
                },
            }
        )

    def _handle_admin_runtime(self) -> None:
        origin = self.headers.get("Origin", "").rstrip("/")
        base_url = f"http://{self.server.config.host}:{self.server.config.port}"
        if origin:
            base_url = origin
        self._send_json(
            {
                "status": True,
                "data": {
                    "host": self.server.config.host,
                    "port": self.server.config.port,
                    "openai_base_url": f"{base_url}/v1",
                    "queue_length": self.server.db.active_queue_size(),
                    "worker_threads": self.server.config.worker_threads,
                    "log_dir": str(self.server.config.log_dir),
                    "app_log": str(self.server.config.log_dir / "app.log"),
                    "audit_log": str(self.server.config.log_dir / "audit.log"),
                },
            }
        )

    def _handle_logs(self, parsed) -> None:
        query = parse_qs(parsed.query)
        log_type = query.get("type", ["app"])[0]
        if log_type not in {"app", "audit"}:
            raise ValueError("日志类型必须是 app 或 audit")
        lines = min(max(1, int(query.get("lines", ["120"])[0])), 500)
        log_path = self.server.config.log_dir / ("audit.log" if log_type == "audit" else "app.log")
        if not log_path.is_file():
            self._send_json({"status": True, "data": {"type": log_type, "path": str(log_path), "lines": []}})
            return
        content = self._read_log_tail(log_path, lines)
        safe_lines = [redact(line, self.server.config) for line in content]
        self._send_json({"status": True, "data": {"type": log_type, "path": str(log_path), "lines": safe_lines}})

    def _read_log_tail(self, log_path: Path, lines: int) -> list[str]:
        chunk_size = 8192
        chunks = []
        newline_count = 0
        with log_path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            position = file.tell()
            while position > 0 and newline_count <= lines:
                read_size = min(chunk_size, position)
                position -= read_size
                file.seek(position)
                chunk = file.read(read_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
        data = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
        return data.splitlines()[-lines:]

    def _handle_admin_config(self) -> None:
        body = self._read_json_body()
        port = int(body.get("port", 0) or 0)
        if port < 1 or port > 65535:
            raise ValueError("端口必须在 1-65535 之间")
        update_env_value("APP_PORT", str(port))
        os.environ["APP_PORT"] = str(port)
        self._audit("admin:config", "ok", {"APP_PORT": port})
        self._send_json({"status": True, "data": {"port": port, "restart_required": port != self.server.config.port}})

    def _handle_admin_restart(self) -> None:
        self._audit("admin:restart", "ok", {"argv": sys.argv[:1]})
        threading.Thread(target=self._restart_process, name="admin-restart", daemon=True).start()
        self._send_json({"status": True, "data": {"message": "服务正在重启"}})

    def _restart_process(self) -> None:
        time.sleep(0.5)
        self.server.runner.stop()
        self.server.shutdown()
        os.execv(sys.executable, [sys.executable, *sys.argv])

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

    def _handle_openai_models(self) -> None:
        data = [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "bizyair",
                "permission": [],
                "root": model,
                "parent": None,
            }
            for model in MODEL_SCHEMAS
        ]
        self._audit("openai:models", "ok", {"count": len(data)})
        self._send_json({"object": "list", "data": data})

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
            self._debug_log_multipart({}, [{"field": "file", "filename": filename, "mime": part.get_content_type() or "", "data": file_data}])
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
                files.append({
                    "field": field_name,
                    "filename": filename,
                    "mime": part.get_content_type() or "",
                    "data": file_data,
                })
            else:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text_fields[field_name] = payload.decode("utf-8", errors="replace").strip()
        self._debug_log_multipart(text_fields, files)
        return text_fields, files

    @staticmethod
    def _extract_upload_url(data) -> str:
        if isinstance(data, str) and data.startswith(("http://", "https://")):
            return data
        if isinstance(data, list):
            for item in data:
                url = LanGatewayHandler._extract_upload_url(item)
                if url:
                    return url
        if isinstance(data, dict):
            for key in ("url", "uri", "src", "download_url", "resource_url", "object_url"):
                url = LanGatewayHandler._extract_upload_url(data.get(key))
                if url:
                    return url
            for value in data.values():
                url = LanGatewayHandler._extract_upload_url(value)
                if url:
                    return url
        return ""

    def _handle_openai_images_generations(self) -> None:
        model = ""
        try:
            body = self._read_json_body()
            model = str(body.get("model", "")).strip()
            if model not in MODEL_SCHEMAS:
                self._send_openai_error("不支持的模型", 404, param="model", code="model_not_found")
                return
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                self._send_openai_error("请输入提示词", 400, param="prompt")
                return
            response_format = str(body.get("response_format") or "b64_json").strip() or "b64_json"
            if response_format not in {"url", "b64_json"}:
                self._send_openai_error("response_format must be url or b64_json", 400, param="response_format")
                return
            params = self._openai_params(body, [])
            if "size" in body:
                self._apply_openai_image_size(params, body["size"])
            params = validate_params(model, params)
            if "n" in body and "variants" not in params:
                try:
                    n_value = int(body["n"])
                except (TypeError, ValueError):
                    n_value = None
                if n_value != 1:
                    self._send_openai_error("n is not supported for this model", 400, param="n")
                    return
            job = self._create_openai_job(model, prompt, params)
            result = self._wait_for_openai_job(job["id"])
            response_body = self._openai_images_response(result, response_format)
        except ValueError as exc:
            self._send_openai_error(str(exc), 400)
            return
        except TimeoutError as exc:
            self._audit("openai:images_generations", "timeout", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 504, type_="server_error", code="upstream_timeout")
            return
        except RuntimeError as exc:
            self._audit("openai:images_generations", "failed", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 502, type_="server_error", code="upstream_error")
            return
        self._audit("openai:images_generations", "ok", {"model": model, "job_id": job["id"], "prompt_length": len(prompt), "response_format": response_format})
        self._send_json(response_body)

    def _handle_openai_images_edits(self) -> None:
        model = ""
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length <= 0:
                self._send_openai_error("请求体为空", 400)
                return
            if content_length > self.server.config.max_upload_bytes:
                self._send_openai_error(
                    f"上传内容不能超过 {self.server.config.max_upload_bytes // 1024 // 1024} MB", 400
                )
                return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_openai_error("Content-Type 必须是 multipart/form-data", 400)
                return
            text_fields, files = self._read_multipart_form(content_length, content_type)
            model = text_fields.get("model", "").strip()
            if model not in MODEL_SCHEMAS:
                self._send_openai_error("不支持的模型", 404, param="model", code="model_not_found")
                return
            prompt = text_fields.get("prompt", "").strip()
            if not prompt:
                self._send_openai_error("请输入提示词", 400, param="prompt")
                return
            response_format = (text_fields.get("response_format") or "b64_json").strip() or "b64_json"
            if response_format not in {"url", "b64_json"}:
                self._send_openai_error("response_format must be url or b64_json", 400, param="response_format")
                return
            image_files = [f for f in files if f["field"] in {"image", "image[]"}]
            if not image_files:
                self._send_openai_error("缺少待编辑的图片字段 image", 400, param="image")
                return
            for item in image_files:
                if item["mime"] and not item["mime"].startswith(ALLOWED_UPLOAD_MIME_PREFIXES):
                    self._send_openai_error("只允许上传图片文件", 400, param="image")
                    return
                suffix = self._image_suffix(item)
                if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
                    self._send_openai_error("只允许上传 png、jpg、jpeg、webp、gif 图片", 400, param="image")
                    return
            urls = self._upload_edit_images(image_files)
            if not urls:
                self._send_openai_error("图片上传失败", 502, type_="server_error", code="upstream_error")
                return
            body_for_params = {key: value for key, value in text_fields.items() if key not in {"model", "prompt", "response_format", "size"}}
            params = self._openai_params(body_for_params, urls)
            if "size" in text_fields:
                self._apply_openai_image_size(params, text_fields["size"])
            params = validate_params(model, params)
            if "n" in text_fields and "variants" not in params:
                try:
                    n_value = int(text_fields["n"])
                except (TypeError, ValueError):
                    n_value = None
                if n_value != 1:
                    self._send_openai_error("n is not supported for this model", 400, param="n")
                    return
            job = self._create_openai_job(model, prompt, params)
            result = self._wait_for_openai_job(job["id"])
            response_body = self._openai_images_response(result, response_format)
        except ValueError as exc:
            self._send_openai_error(str(exc), 400)
            return
        except TimeoutError as exc:
            self._audit("openai:images_edits", "timeout", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 504, type_="server_error", code="upstream_timeout")
            return
        except RuntimeError as exc:
            self._audit("openai:images_edits", "failed", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 502, type_="server_error", code="upstream_error")
            return
        self._audit("openai:images_edits", "ok", {"model": model, "job_id": job["id"], "prompt_length": len(prompt), "response_format": response_format, "image_count": len(image_files)})
        self._send_json(response_body)

    def _image_suffix(self, item: dict) -> str:
        suffix = Path(item.get("filename") or "").suffix.lower()
        if suffix:
            return suffix
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(str(item.get("mime") or "").lower(), "")

    def _upload_edit_images(self, image_files: list[dict]) -> list[str]:
        key = self.server.runner.key_pool.pick()
        client = BizyUpImage(api_key=key.api_key)
        urls: list[str] = []
        for item in image_files:
            suffix = self._image_suffix(item)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(item["data"])
                data = client.upload(temp_path, file_name=item["filename"])
                url = self._extract_upload_url(data)
                if not url:
                    raise RuntimeError("上传成功但未能解析出图片 URL")
                urls.append(url)
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
        return urls

    def _handle_openai_chat_completions(self) -> None:
        model = ""
        try:
            body = self._read_json_body()
            if body.get("stream") is True:
                self._send_openai_error("stream is not supported", 400, param="stream")
                return
            model = str(body.get("model", "")).strip()
            if model not in MODEL_SCHEMAS:
                self._send_openai_error("不支持的模型", 404, param="model", code="model_not_found")
                return
            prompt, urls = self._openai_messages_to_prompt_and_urls(body.get("messages"))
            params = self._openai_params(body, urls)
            params = validate_params(model, params)
            job = self._create_openai_job(model, prompt, params)
            result = self._wait_for_openai_job(job["id"])
        except ValueError as exc:
            self._send_openai_error(str(exc), 400)
            return
        except TimeoutError as exc:
            self._audit("openai:chat_completions", "timeout", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 504, type_="server_error", code="upstream_timeout")
            return
        except RuntimeError as exc:
            self._audit("openai:chat_completions", "failed", {"model": model, "error": redact(exc, self.server.config)})
            self._send_openai_error(str(exc), 502, type_="server_error", code="upstream_error")
            return
        self._audit("openai:chat_completions", "ok", {"model": model, "job_id": job["id"], "prompt_length": len(prompt)})
        self._send_json(self._openai_chat_response(model, job["id"], result))

    def _create_openai_job(self, model: str, prompt: str, params: dict) -> dict:
        job = self.server.db.create_job(model, [prompt], params)
        self.server.runner.enqueue_job(job)
        self.server.logger.info("OpenAI 请求已加入队列 job=%s model=%s", job["id"], model)
        return job

    def _wait_for_openai_job(self, job_id: str) -> dict:
        deadline = time.monotonic() + self.server.config.max_poll_seconds
        while True:
            job = self.server.db.get_job(job_id)
            if job.get("status") == "succeeded":
                item = next((entry for entry in job.get("items", []) if entry.get("status") == "succeeded"), None)
                result = item.get("result") if item else None
                if isinstance(result, dict):
                    return {**result, "job": job}
                raise RuntimeError("任务已完成但没有结果")
            if job.get("status") in {"failed", "cancelled"}:
                error = self._openai_job_error(job)
                raise RuntimeError(error or f"任务失败: {job.get('status')}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"任务等待超时: job_id={job_id}")
            time.sleep(min(self.server.config.poll_interval_seconds, max(deadline - time.monotonic(), 0)))

    def _openai_job_error(self, job: dict) -> str:
        for item in job.get("items", []):
            if item.get("error"):
                return str(item["error"])
        return ""

    def _openai_result_images(self, result: dict) -> list[str]:
        job = result.get("job") if isinstance(result.get("job"), dict) else None
        if job:
            records = job.get("image_records") or []
            urls = [record.get("url") or record.get("display_url") or record.get("original_url") for record in records]
            urls = [url for url in urls if isinstance(url, str) and url]
            if urls:
                return urls
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        return [url for url in outputs.get("images", []) if isinstance(url, str) and url.startswith(("http://", "https://"))]

    def _absolute_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        origin = self.headers.get("Origin", "").rstrip("/")
        host = self.headers.get("Host") or f"{self.server.config.host}:{self.server.config.port}"
        base_url = origin or f"http://{host}"
        return f"{base_url}{url if url.startswith('/') else '/' + url}"

    def _openai_messages_to_prompt_and_urls(self, messages) -> tuple[str, list[str]]:
        if not isinstance(messages, list):
            raise ValueError("messages 必须是数组")
        lines = []
        urls = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("messages 中的每一项都必须是对象")
            role = str(message.get("role") or "user").strip() or "user"
            content = message.get("content")
            text_parts = []
            if isinstance(content, str):
                if content.strip():
                    text_parts.append(content.strip())
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and isinstance(part.get("text"), str) and part["text"].strip():
                        text_parts.append(part["text"].strip())
                    elif part.get("type") == "image_url":
                        image_url = part.get("image_url")
                        url = image_url.get("url") if isinstance(image_url, dict) else None
                        if not isinstance(url, str) or not url:
                            raise ValueError("image_url.url 必须是字符串")
                        if not url.startswith(("http://", "https://")):
                            raise ValueError("image_url.url 只支持 http 或 https URL")
                        urls.append(url)
            elif content is not None:
                raise ValueError("message.content 必须是字符串或数组")
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")
        prompt = "\n".join(lines).strip()
        if not prompt:
            raise ValueError("请输入提示词")
        return prompt, urls

    def _openai_params(self, body: dict, urls: list[str]) -> dict:
        params = {}
        for key in ("aspect_ratio", "resolution", "quality", "variants", "temperature", "top_p", "seed", "max_tokens"):
            if key in body:
                params[key] = body[key]
        if "variants" not in params and "n" in body:
            params["variants"] = body["n"]
        if urls:
            params["urls"] = urls
        return params

    def _apply_openai_image_size(self, params: dict, size) -> None:
        if not isinstance(size, str):
            return
        size_map = {
            "1024x1024": "1:1",
            "1024x1536": "2:3",
            "1536x1024": "3:2",
        }
        aspect_ratio = size_map.get(size.strip().lower())
        if aspect_ratio and "aspect_ratio" not in params:
            params["aspect_ratio"] = aspect_ratio

    def _openai_chat_response(self, model: str, request_id: str, result: dict) -> dict:
        image_lines = [f"![image]({self._absolute_url(url)})" for url in self._openai_result_images(result)]
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        content = "\n".join(image_lines) if image_lines else json.dumps(outputs, ensure_ascii=False)
        return {
            "id": f"chatcmpl_{request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _openai_images_response(self, result: dict, response_format: str) -> dict:
        data = []
        for url in self._openai_result_images(result):
            if response_format == "b64_json":
                data.append({"b64_json": self._download_image_base64(url)})
            else:
                data.append({"url": self._absolute_url(url)})
        return {"created": int(time.time()), "data": data}

    def _download_image_base64(self, url: str) -> str:
        if url.startswith("/api/images/"):
            image_id = url.removeprefix("/api/images/").split("/", 1)[0]
            image = self.server.db.get_job_image(image_id)
            return base64.b64encode(Path(image["local_path"]).read_bytes()).decode("ascii")
        path, _content_type = self.server.image_cache.get(url)
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def _send_openai_error(self, message: str, status: int = 400, type_: str = "invalid_request_error", param=None, code=None) -> None:
        self._send_json({"error": {"message": message, "type": type_, "param": param, "code": code}}, status)

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
            self._debug_log_body_json(raw)
            raise ValueError("请求体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        self._debug_log_body_json(data)
        return data

    def _debug_log_request(self, method: str) -> None:
        if not self.server.config.debug_requests:
            return
        client = f"{self.client_address[0]}:{self.client_address[1]}" if self.client_address else "unknown"
        header_lines = []
        for name, value in self.headers.items():
            if name.lower() == "authorization":
                value = "Bearer [REDACTED]" if value.startswith("Bearer ") else "[REDACTED]"
            header_lines.append(f"  {name}: {value}")
        self.server.logger.debug(
            "[debug] >>> %s %s from %s\n%s",
            method,
            self.path,
            client,
            "\n".join(header_lines) or "  (no headers)",
        )

    def _debug_log_body_json(self, body) -> None:
        if not self.server.config.debug_requests:
            return
        try:
            text = json.dumps(body, ensure_ascii=False, indent=2) if not isinstance(body, str) else body
        except (TypeError, ValueError):
            text = str(body)
        self.server.logger.debug("[debug] request body (json):\n%s", redact(text, self.server.config))

    def _debug_log_multipart(self, text_fields: dict, files: list[dict]) -> None:
        if not self.server.config.debug_requests:
            return
        text_summary = json.dumps(text_fields, ensure_ascii=False, indent=2) if text_fields else "(no text fields)"
        file_summary = [
            f"  - field={item.get('field')} filename={item.get('filename')} mime={item.get('mime')} size={len(item.get('data') or b'')}"
            for item in files
        ]
        self.server.logger.debug(
            "[debug] request body (multipart):\n%s\nfiles:\n%s",
            redact(text_summary, self.server.config),
            "\n".join(file_summary) or "  (no files)",
        )

    def _debug_log_response(self, status: int, payload: dict) -> None:
        if not self.server.config.debug_requests:
            return
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(payload)
        self.server.logger.debug(
            "[debug] <<< %s %s status=%s\n%s",
            self.command,
            self.path,
            status,
            redact(text, self.server.config),
        )

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
        self._debug_log_response(status, payload)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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
