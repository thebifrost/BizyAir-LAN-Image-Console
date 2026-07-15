import hmac
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .bizyUpImage import BizyUpImage
from .config import APP_VERSION, PROJECT_ROOT, load_config, now_iso, update_env_value
from .env_store import save_env_config
from .handlers_openai import OpenAIHandlerMixin
from .handlers_upload import UploadHandlerMixin
from .http_routes import dispatch_delete, dispatch_get, dispatch_post
from .input_images import input_image_id_from_url, load_input_image
from .logging_utils import redact
from .schemas import get_model_schemas, validate_params
from .upstream_client import UpstreamClient, summarize_account


class LanGatewayHandler(OpenAIHandlerMixin, UploadHandlerMixin, BaseHTTPRequestHandler):
    server: object

    def do_OPTIONS(self) -> None:
        self._begin_request("OPTIONS")
        self._send_json({"status": True})

    def do_GET(self) -> None:
        self._dispatch("GET", dispatch_get)

    def do_DELETE(self) -> None:
        self._dispatch("DELETE", dispatch_delete)

    def do_POST(self) -> None:
        self._dispatch("POST", dispatch_post)

    def _dispatch(self, method: str, dispatcher) -> None:
        self._begin_request(method)
        parsed = urlparse(self.path)
        try:
            dispatcher(self, parsed)
        except PermissionError as exc:
            self._send_json({"status": False, "message": str(exc)}, 401)
        except KeyError as exc:
            self._send_json({"status": False, "message": str(exc)}, 404)
        except ValueError as exc:
            self._send_json({"status": False, "message": str(exc)}, 400)
        except BrokenPipeError:
            # 客户端断开，无法回写响应，静默即可
            self.server.logger.debug("%s 客户端提前断开: %s", method, self.path)
        except Exception as exc:
            self.server.logger.exception("%s 处理失败: %s", method, redact(exc, self.server.config))
            try:
                self._send_json({"status": False, "message": redact(exc, self.server.config)}, 500)
            except Exception:
                pass

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

    def _handle_admin_env(self) -> None:
        body = self._read_json_body()
        result = save_env_config(body)
        try:
            new_config = load_config()
        except SystemExit as exc:
            raise ValueError(f"配置已保存，但热重载失败: {exc}") from exc
        new_upstream_client = UpstreamClient(new_config)
        self.server.config = new_config
        self.server.upstream_client = new_upstream_client
        self.server.image_cache.config = new_config
        self.server.runner.reload_config(new_config, new_upstream_client)
        result["hot_reloaded"] = True
        self._audit("admin:env", "ok", {"updated": result.get("updated", []), "removed": result.get("removed", [])})
        self.server.logger.info("环境配置已保存 updated=%s removed=%s backup=%s", result.get("updated", []), result.get("removed", []), result.get("backup_path", ""))
        self._send_json({"status": True, "data": result})

    def _handle_admin_restart(self) -> None:
        self._audit("admin:restart", "ok", {"argv": sys.argv[:1]})
        threading.Thread(target=self._restart_process, name="admin-restart", daemon=True).start()
        self._send_json({"status": True, "data": {"message": "服务正在重启"}})

    def _restart_process(self) -> None:
        time.sleep(0.5)
        self.server.runner.stop(timeout_seconds=None, drain=True)
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
        self._stream_file(path, content_type, cache_control="public, max-age=86400")

    def _handle_input_image(self, request_path: str) -> None:
        image_id = input_image_id_from_url(request_path)
        if not image_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            image = load_input_image(self.server.config.input_image_dir, image_id)
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._stream_file(image.path, image.content_type, cache_control="public, max-age=31536000, immutable")

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
        content_type = image.get("content_type") or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        extra_headers: dict[str, str] = {}
        if download:
            filename = f"bizyair-{str(image.get('job_id') or '')[:8]}-{int(image.get('image_index') or 0) + 1}{image.get('extension') or file_path.suffix}"
            extra_headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        self._stream_file(file_path, content_type, cache_control="public, max-age=31536000, immutable", extra_headers=extra_headers)

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


    def _handle_create_job(self) -> None:
        body = self._read_json_body()
        model = str(body.get("model", "")).strip()
        schemas = get_model_schemas(self.server.config)
        if model not in schemas:
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
        params = validate_params(model, params, schemas)
        job = self.server.db.create_job(model, prompts, params)
        self._enqueue_job_or_cancel(job)
        self._audit("jobs:create", "ok", {"job_id": job["id"], "model": model, "total": len(prompts)})
        self._send_json({"status": True, "data": job}, 201)

    def _enqueue_job_or_cancel(self, job: dict) -> None:
        try:
            self.server.runner.enqueue_job(job)
        except RuntimeError as exc:
            try:
                self.server.db.cancel_job(job["id"])
            except Exception:
                self.server.logger.exception("服务停止期间取消新任务失败 job=%s", job.get("id"))
            raise RuntimeError("服务正在重启，暂不接受新任务") from exc

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

    def _begin_request(self, method: str) -> None:
        self.request_id = uuid.uuid4().hex[:12]
        self.request_started_at = time.monotonic()
        self._debug_log_request(method)

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
        # index.html 需要每次拿最新版本（用户改前端时立即生效），其它静态资源允许短缓存
        cache_control = "no-cache" if file_path.name == "index.html" else "public, max-age=300"
        self._stream_file(file_path, content_type, cache_control=cache_control)

    def _stream_file(
        self,
        path: Path,
        content_type: str,
        *,
        cache_control: str = "public, max-age=300",
        extra_headers: dict[str, str] | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None:
        try:
            stat = path.stat()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = stat.st_size
        mtime = int(stat.st_mtime)
        etag = f'W/"{stat.st_ino:x}-{size:x}-{mtime:x}"'
        # 命中 ETag 直接返回 304，历史画廊滚动时可显著减少带宽
        if_none_match = self.headers.get("If-None-Match", "")
        if if_none_match and etag in [tag.strip() for tag in if_none_match.split(",")]:
            self.send_response(304)
            self._send_cors_headers()
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            with path.open("rb") as file:
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断连（大图预览时经常发生）不算错误
            return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._debug_log_response(status, payload)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._log_json_response(status, payload)
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _log_json_response(self, status: int, payload: dict) -> None:
        path = urlparse(self.path).path
        if not path.startswith(("/api/", "/v1/")) and path != "/health":
            return
        duration_ms = int((time.monotonic() - getattr(self, "request_started_at", time.monotonic())) * 1000)
        message = ""
        if isinstance(payload, dict):
            message = payload.get("message") or ""
            error = payload.get("error")
            if not message and isinstance(error, dict):
                message = error.get("message") or ""
        if status >= 400:
            level = logging.WARNING
        elif path in {"/api/admin/runtime", "/api/jobs"}:
            level = logging.DEBUG
        else:
            level = logging.INFO
        self.server.logger.log(
            level,
            "request_id=%s method=%s path=%s status=%s duration_ms=%s message=%s",
            getattr(self, "request_id", "-"),
            self.command,
            path,
            status,
            duration_ms,
            redact(message, self.server.config),
        )

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
            "models": list(get_model_schemas(self.server.config).keys()),
            "openai_providers": [
                {
                    "id": provider.id,
                    "label": provider.label or provider.id,
                    "models": list(provider.models),
                    "concurrency": provider.concurrency,
                    "send_reference_images_as_files": provider.send_reference_images_as_files,
                }
                for provider in self.server.config.openai_providers.values()
            ],
            "openai_compatible_send_reference_images_as_files": (
                self.server.config.openai_compatible.send_reference_images_as_files
                if self.server.config.openai_compatible
                else False
            ),
        }

    @staticmethod
    def _json_response(response: requests.Response) -> dict:
        return UpstreamClient.json_response(response)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        path = urlparse(self.path).path
        if int(code) == 200 and path in {"/api/admin/runtime", "/api/jobs"}:
            return
        super().log_request(code, size)

    def log_message(self, fmt: str, *args) -> None:
        self.server.logger.info("%s - %s", self.address_string(), fmt % args)
