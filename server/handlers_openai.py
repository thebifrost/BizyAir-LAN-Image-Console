"""OpenAI 兼容接口的 HTTP handler mixin。

从 handlers.py 拆出：/v1/models、/v1/images/generations、/v1/images/edits、
/v1/chat/completions 及其辅助方法。所有方法都依赖 LanGatewayHandler 上的
_send_json/_audit/_absolute_url/_read_json_body/_read_multipart_form 等基础方法。
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .input_images import store_input_image
from .logging_utils import redact
from .schemas import ALLOWED_UPLOAD_EXTENSIONS, ALLOWED_UPLOAD_MIME_PREFIXES, get_model_schemas, validate_params


class OpenAIHandlerMixin:
    def _handle_openai_models(self) -> None:
        schemas = get_model_schemas(self.server.config)
        data = [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": schemas[model].get("provider", "bizyair"),
                "permission": [],
                "root": model,
                "parent": None,
            }
            for model in schemas
        ]
        self._audit("openai:models", "ok", {"count": len(data)})
        self._send_json({"object": "list", "data": data})

    def _handle_openai_images_generations(self) -> None:
        model = ""
        try:
            body = self._read_json_body()
            model = str(body.get("model", "")).strip()
            schemas = get_model_schemas(self.server.config)
            if model not in schemas:
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
            params = validate_params(model, params, schemas)
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
            schemas = get_model_schemas(self.server.config)
            if model not in schemas:
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
            if self._should_store_openai_edit_images_locally(model):
                urls = self._store_edit_images(image_files)
            else:
                urls = self._upload_edit_images(image_files)
            if not urls:
                self._send_openai_error("图片上传失败", 502, type_="server_error", code="upstream_error")
                return
            body_for_params = {key: value for key, value in text_fields.items() if key not in {"model", "prompt", "response_format", "size"}}
            params = self._openai_params(body_for_params, urls)
            if "size" in text_fields:
                self._apply_openai_image_size(params, text_fields["size"])
            params = validate_params(model, params, schemas)
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

    def _should_store_openai_edit_images_locally(self, model: str) -> bool:
        provider = self.server.config.openai_provider_for_model(model)
        return bool(provider and provider.send_reference_images_as_files)

    def _store_edit_images(self, image_files: list[dict]) -> list[str]:
        urls: list[str] = []
        for item in image_files:
            image = store_input_image(
                self.server.config.input_image_dir,
                item["data"],
                item["filename"],
                item.get("mime") or "",
                self._image_suffix(item),
            )
            urls.append(self._absolute_url(f"/api/input-images/{image.id}/file"))
        return urls

    def _upload_edit_images(self, image_files: list[dict]) -> list[str]:
        urls: list[str] = []
        for item in image_files:
            suffix = self._image_suffix(item)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(item["data"])
                data, _provider, _key_id = self._upload_input_image(temp_path, item["filename"])
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
            schemas = get_model_schemas(self.server.config)
            if model not in schemas:
                self._send_openai_error("不支持的模型", 404, param="model", code="model_not_found")
                return
            prompt, urls = self._openai_messages_to_prompt_and_urls(body.get("messages"))
            params = self._openai_params(body, urls)
            params = validate_params(model, params, schemas)
            if urls and not params.get("urls"):
                self._send_openai_error("当前模型不支持提供的图片 URL", 400, param="messages")
                return
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
        self._enqueue_job_or_cancel(job)
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
        return [url for url in outputs.get("images", []) if isinstance(url, str) and url.startswith(("http://", "https://", "data:"))]

    def _absolute_url(self, url: str) -> str:
        if url.startswith(("http://", "https://", "data:")):
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
                        if not self._is_supported_openai_image_url(url):
                            raise ValueError("image_url.url 只支持 http/https、data:image 或本地图片 URL")
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
        for key in ("aspect_ratio", "resolution", "quality", "variants", "temperature", "top_p", "seed", "max_tokens", "size", "style", "background", "moderation", "output_format", "output_compression"):
            if key in body:
                params[key] = body[key]
        if "variants" not in params and "n" in body:
            params["variants"] = body["n"]
        if urls:
            params["urls"] = urls
        return params

    @staticmethod
    def _is_supported_openai_image_url(url: str) -> bool:
        path = urlparse(url).path if url.startswith(("http://", "https://")) else url
        return (
            url.startswith(("http://", "https://"))
            or url.lower().startswith("data:image/")
            or path.startswith(("/api/input-images/", "/api/images/"))
        )

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
        if url.startswith("data:") and ";base64," in url:
            return url.split(";base64,", 1)[1]
        if url.startswith("/api/images/"):
            image_id = url.removeprefix("/api/images/").split("/", 1)[0]
            image = self.server.db.get_job_image(image_id)
            return base64.b64encode(Path(image["local_path"]).read_bytes()).decode("ascii")
        path, _content_type = self.server.image_cache.get(url)
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def _send_openai_error(self, message: str, status: int = 400, type_: str = "invalid_request_error", param=None, code=None) -> None:
        self._send_json({"error": {"message": message, "type": type_, "param": param, "code": code}}, status)
