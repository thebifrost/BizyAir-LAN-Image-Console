import logging
import queue
import threading
import time

from .config import AppConfig, BizyAirKeyConfig
from .database import Database
from .key_pool import BizyAirKeyPool
from .logging_utils import redact
from .schemas import PENDING_UPSTREAM_STATUSES, TERMINAL_STATUSES
from .upstream_client import UpstreamClient


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
        self.stop_event = threading.Event()

    def start(self) -> None:
        for item_id in self.db.queued_item_ids():
            self.enqueue(item_id)
        for index in range(self.config.worker_threads):
            thread = threading.Thread(target=self._worker, name=f"job-worker-{index + 1}", daemon=True)
            thread.start()

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

    def _archive_result_images(self, item: dict, result: dict) -> None:
        if not self.image_cache:
            return
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else result.get("data", {}).get("outputs", {}) if isinstance(result.get("data"), dict) else {}
        images = outputs.get("images", []) if isinstance(outputs, dict) else []
        for index, url in enumerate(images):
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            image_id = self.db.create_job_image_id(item["id"], index)
            try:
                archived = self.image_cache.archive_result_image(url, image_id)
                self.db.upsert_job_image({
                    "id": image_id,
                    "job_id": item["job_id"],
                    "item_id": item["id"],
                    "image_index": index,
                    "original_url": url,
                    "local_path": archived["local_path"],
                    "content_type": archived["content_type"],
                    "extension": archived["extension"],
                    "size": archived["size"],
                    "sha256": archived.get("sha256"),
                    "status": "ready",
                })
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
