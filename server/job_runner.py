import logging
import mimetypes
import queue
import contextlib
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import AppConfig, BizyAirKeyConfig
from .database import Database
from .image_data import is_image_data_url, parse_image_data_url
from .input_images import input_image_id_from_url, load_input_image
from .key_pool import BizyAirKeyPool
from .logging_utils import redact
from .schemas import PENDING_UPSTREAM_STATUSES, TERMINAL_STATUSES, is_local_api_url
from .upstream_client import UpstreamClient, normalize_openai_image_result


class JobCancelled(Exception):
    pass


class JobRunner:
    def __init__(self, config: AppConfig, db: Database, logger: logging.Logger, upstream_client: UpstreamClient, image_cache=None):
        self.config = config
        self.db = db
        self.logger = logger
        self.upstream_client = upstream_client
        self.image_cache = image_cache
        self.queue: queue.Queue[str] = queue.Queue()
        self.enqueued: set[str] = set()
        self.enqueued_lock = threading.Lock()
        self.key_pool = BizyAirKeyPool(config.bizyair_keys)
        self.provider_semaphores = {
            provider_id: threading.BoundedSemaphore(provider.concurrency)
            for provider_id, provider in getattr(config, "openai_providers", {}).items()
            if provider.concurrency > 0
        }
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for item_id in self.db.queued_item_ids():
            self.enqueue(item_id)
        for index in range(self.config.worker_threads):
            thread = threading.Thread(target=self._worker, name=f"job-worker-{index + 1}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def enqueue_job(self, job: dict) -> None:
        for item in job.get("items", []):
            if item.get("status") == "queued":
                self.enqueue(item["id"])

    def enqueue(self, item_id: str) -> None:
        with self.enqueued_lock:
            if item_id in self.enqueued:
                return
            self.enqueued.add(item_id)
        self.queue.put(item_id)

    def size(self) -> int:
        return self.queue.qsize()

    def stop(self, timeout_seconds: float = 2) -> None:
        self.stop_event.set()
        while True:
            try:
                item_id = self.queue.get_nowait()
            except queue.Empty:
                break
            with self.enqueued_lock:
                self.enqueued.discard(item_id)
            self.queue.task_done()
        deadline = time.monotonic() + timeout_seconds
        for thread in self.threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    def _worker(self) -> None:
        while True:
            if self.stop_event.is_set() and self.queue.empty():
                return
            try:
                item_id = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.enqueued_lock:
                self.enqueued.discard(item_id)
            try:
                self._process_item(item_id)
            except JobCancelled:
                self.logger.info("子任务已取消: %s", item_id)
            except Exception as exc:
                self.logger.exception("子任务处理失败: %s", redact(exc, self.config))
                try:
                    self.db.finish_item(item_id, "failed", error=redact(exc, self.config))
                except Exception:
                    self.logger.exception("写入失败状态失败")
            finally:
                self.queue.task_done()

    def _process_item(self, item_id: str) -> None:
        item = self.db.get_item_for_processing(item_id)
        if item.get("cancel_requested"):
            self.db.finish_item(item_id, "cancelled")
            return
        if item["status"] in TERMINAL_STATUSES:
            return
        provider_id = self.config.model_providers.get(item["model"], "bizyair")
        self.logger.info("开始处理子任务 item=%s job=%s model=%s provider=%s", item_id, item.get("job_id"), item.get("model"), provider_id)
        if provider_id != "bizyair":
            self._process_openai_compatible_item(item_id, item, provider_id)
            return
        key = self.key_pool.pick(item.get("bizyair_key_id"))
        item = self.db.get_item_for_processing(item_id)
        if item.get("cancel_requested"):
            self.db.finish_item(item_id, "cancelled")
            return
        if item["status"] in TERMINAL_STATUSES:
            return
        if item.get("bizyair_key_id") != key.id:
            self.db.set_item_bizyair_key_id(item_id, key.id)
            item["bizyair_key_id"] = key.id
        self.db.set_item_running(item_id)
        request_id = item.get("upstream_request_id")
        if not request_id:
            request_id = self._create_upstream_task(item, key)
            self.db.set_upstream_request_id(item_id, request_id)
        result = self._poll_upstream_task(item_id, request_id, key)
        self._archive_result_images(item, result)
        self.db.finish_item(item_id, "succeeded", result=result)
        self.logger.info("子任务完成 item=%s job=%s model=%s provider=bizyair", item_id, item.get("job_id"), item.get("model"))

    def _process_openai_compatible_item(self, item_id: str, item: dict, provider_id: str) -> None:
        provider = self.config.openai_providers.get(provider_id)
        if not provider:
            raise RuntimeError(f"未找到模型 {item['model']} 对应的 OpenAI-compatible provider: {provider_id}")
        self.db.set_item_running(item_id)
        if self._cancel_if_requested(item_id):
            return
        reference_images = []
        send_reference_images_as_files = item["payload"].get("send_reference_images_as_files", provider.send_reference_images_as_files) is not False
        if send_reference_images_as_files and isinstance(item["payload"].get("urls"), list):
            reference_images = self._resolve_openai_reference_images(item["payload"]["urls"])
        upstream_model = provider.model_map.get(item["model"], item["model"])
        with self._provider_slot(provider.id):
            if self._cancel_if_requested(item_id):
                return
            self.logger.info(
                "调用 OpenAI-compatible provider=%s model=%s upstream_model=%s references=%s",
                provider.id,
                item["model"],
                upstream_model,
                len(reference_images),
            )
            response, data = self.upstream_client.create_openai_image(provider, upstream_model, item["payload"], reference_images=reference_images)
        if not response.ok:
            raise RuntimeError(f"OpenAI-compatible 生成失败: HTTP {response.status_code} - {redact(data, self.config)}")
        result = normalize_openai_image_result(data, str(item["payload"].get("output_format") or "png"))
        if not result.get("outputs", {}).get("images"):
            raise RuntimeError(f"OpenAI-compatible 响应缺少图片: {redact(data, self.config)}")
        self._archive_result_images(item, result)
        self.db.finish_item(item_id, "succeeded", result=result)
        self.logger.info("子任务完成 item=%s job=%s model=%s provider=%s", item_id, item.get("job_id"), item.get("model"), provider.id)

    def _cancel_if_requested(self, item_id: str) -> bool:
        item = self.db.get_item_for_processing(item_id)
        if item.get("cancel_requested"):
            self.db.finish_item(item_id, "cancelled")
            return True
        return item["status"] in TERMINAL_STATUSES

    @contextlib.contextmanager
    def _provider_slot(self, provider_id: str):
        semaphore = self.provider_semaphores.get(provider_id)
        if not semaphore:
            yield
            return
        self.logger.debug("等待 provider 并发槽 provider=%s", provider_id)
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    def _resolve_openai_reference_images(self, urls: list[str]) -> list[dict]:
        images = []
        for index, url in enumerate(urls):
            if not isinstance(url, str) or not url:
                continue
            images.append(self._resolve_openai_reference_image(url, index))
        return images

    def _resolve_openai_reference_image(self, url: str, index: int) -> dict:
        if is_image_data_url(url):
            data, content_type, extension = parse_image_data_url(url, self.config.max_upload_bytes)
            return {
                "data": data,
                "filename": f"input-{index + 1}{extension}",
                "content_type": content_type,
            }

        input_image_id = input_image_id_from_url(url)
        if input_image_id:
            image = load_input_image(self.config.input_image_dir, input_image_id)
            return {
                "data": image.path.read_bytes(),
                "filename": image.filename or f"input-{index + 1}{image.extension}",
                "content_type": image.content_type,
            }

        local_result_id = self._local_result_image_id_from_url(url)
        if local_result_id:
            image = self.db.get_job_image(local_result_id)
            file_path = Path(image["local_path"]).resolve()
            return {
                "data": file_path.read_bytes(),
                "filename": f"result-{local_result_id[:8]}{image.get('extension') or file_path.suffix or '.png'}",
                "content_type": image.get("content_type") or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream",
            }

        if not self.image_cache:
            raise RuntimeError("OpenAI-compatible reference image file mode requires image cache")
        file_path, content_type = self.image_cache.get(url)
        suffix = Path(urlparse(url).path).suffix or mimetypes.guess_extension(content_type or "") or ".png"
        return {
            "data": file_path.read_bytes(),
            "filename": f"input-{index + 1}{suffix}",
            "content_type": content_type or "application/octet-stream",
        }

    @staticmethod
    def _local_result_image_id_from_url(url: str) -> str:
        if not is_local_api_url(url):
            return ""
        path = urlparse(url).path if url.startswith(("http://", "https://")) else url
        if not path.startswith("/api/images/"):
            return ""
        image_id = path.removeprefix("/api/images/").split("/", 1)[0]
        return image_id if re.fullmatch(r"[a-f0-9]{32}", image_id) else ""

    def _archive_result_images(self, item: dict, result: dict) -> None:
        if not self.image_cache:
            return
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else result.get("data", {}).get("outputs", {}) if isinstance(result.get("data"), dict) else {}
        images = outputs.get("images", []) if isinstance(outputs, dict) else []
        for index, url in enumerate(images):
            if not isinstance(url, str) or not (url.startswith(("http://", "https://")) or is_image_data_url(url)):
                continue
            image_id = self.db.create_job_image_id(item["id"], index)
            local_url = f"/api/images/{image_id}"
            try:
                if is_image_data_url(url):
                    archived = self.image_cache.archive_result_data_url(url, image_id)
                    original_url = local_url
                else:
                    archived = self.image_cache.archive_result_image(url, image_id)
                    original_url = url
                self.db.upsert_job_image({
                    "id": image_id,
                    "job_id": item["job_id"],
                    "item_id": item["id"],
                    "image_index": index,
                    "original_url": original_url,
                    "local_path": archived["local_path"],
                    "content_type": archived["content_type"],
                    "extension": archived["extension"],
                    "size": archived["size"],
                    "sha256": archived.get("sha256"),
                    "status": "ready",
                })
                if is_image_data_url(url):
                    images[index] = local_url
            except Exception as exc:
                self.logger.warning("结果图本地保存失败 item=%s index=%s: %s", item["id"], index, redact(exc, self.config))

    def _create_upstream_task(self, item: dict, key: BizyAirKeyConfig) -> str:
        response, data = self.upstream_client.post(f"/trd_api/{item['model']}", key, item["payload"], timeout=60)
        if not response.ok:
            raise RuntimeError(f"创建上游任务失败: HTTP {response.status_code} - {redact(data, self.config)}")
        request_id = data.get("data", {}).get("request_id")
        if not request_id:
            raise RuntimeError(f"创建上游任务缺少 request_id: {redact(data, self.config)}")
        return str(request_id)

    def _poll_upstream_task(self, item_id: str, request_id: str, key: BizyAirKeyConfig) -> dict:
        deadline = time.monotonic() + self.config.max_poll_seconds
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"上游任务轮询超时: request_id={request_id}")
            time.sleep(min(self.config.poll_interval_seconds, max(deadline - time.monotonic(), 0)))
            item = self.db.get_item_for_processing(item_id)
            if item.get("cancel_requested"):
                self.db.finish_item(item_id, "cancelled")
                raise JobCancelled()
            response, data = self.upstream_client.get(f"/trd_api/{request_id}", key, timeout=60)
            if not response.ok:
                raise RuntimeError(f"轮询上游任务失败: HTTP {response.status_code} - {redact(data, self.config)}")
            status = data.get("data", {}).get("status", "unknown")
            if status == "failed":
                raise RuntimeError(f"上游任务失败: {redact(data, self.config)}")
            if status in {"cancelled", "canceled"}:
                raise JobCancelled()
            if status not in PENDING_UPSTREAM_STATUSES:
                outputs = data.get("data", {}).get("outputs")
                if outputs:
                    return data.get("data", {})
                raise RuntimeError(f"上游任务已结束但没有输出: {redact(data, self.config)}")
