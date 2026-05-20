import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import iso_timestamp, now_iso

class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self.lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    params_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_items (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    prompt TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    upstream_request_id TEXT,
                    bizyair_key_id TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_images (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
                    image_index INTEGER NOT NULL,
                    original_url TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(item_id, image_index)
                );
                CREATE INDEX IF NOT EXISTS idx_job_items_job_id ON job_items(job_id);
                CREATE INDEX IF NOT EXISTS idx_job_items_status ON job_items(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);
                CREATE INDEX IF NOT EXISTS idx_job_images_job_id ON job_images(job_id);
                CREATE INDEX IF NOT EXISTS idx_job_images_item_id ON job_images(item_id);
                CREATE INDEX IF NOT EXISTS idx_job_images_created_at ON job_images(created_at);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_items)").fetchall()}
            if "bizyair_key_id" not in columns:
                conn.execute("ALTER TABLE job_items ADD COLUMN bizyair_key_id TEXT")

    def create_job(self, model: str, prompts: list[str], params: dict) -> dict:
        job_id = uuid.uuid4().hex
        created_at = now_iso()
        with self.lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, model, status, total, created_at, updated_at, params_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, model, "queued", len(prompts), created_at, created_at, json.dumps(params, ensure_ascii=False)),
            )
            for prompt in prompts:
                item_id = uuid.uuid4().hex
                payload = {"prompt": prompt, **params}
                conn.execute(
                    """
                    INSERT INTO job_items (id, job_id, prompt, payload_json, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, job_id, prompt, json.dumps(payload, ensure_ascii=False), "queued", created_at),
                )
        return self.get_job(job_id)

    def list_jobs(self, limit: int | None = None) -> list[dict]:
        with self.lock, self.connect() as conn:
            if limit is None:
                rows = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            jobs = [self._job_summary(conn, row) for row in rows]
        return jobs

    def get_job(self, job_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError("任务不存在")
            job = self._row_to_dict(row)
            items = [self._row_to_dict(item) for item in conn.execute("SELECT * FROM job_items WHERE job_id = ? ORDER BY created_at ASC", (job_id,)).fetchall()]
            self._hydrate_job(conn, job, items)
        return job

    def queued_item_ids(self) -> list[str]:
        with self.lock, self.connect() as conn:
            rows = conn.execute(
                """
                SELECT job_items.id
                FROM job_items
                JOIN jobs ON jobs.id = job_items.job_id
                WHERE job_items.status IN ('queued', 'running') AND jobs.cancel_requested = 0
                ORDER BY job_items.created_at ASC
                """
            ).fetchall()
        return [row["id"] for row in rows]

    def get_item_for_processing(self, item_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT job_items.*, jobs.model, jobs.cancel_requested
                FROM job_items
                JOIN jobs ON jobs.id = job_items.job_id
                WHERE job_items.id = ?
                """,
                (item_id,),
            ).fetchone()
        if not row:
            raise KeyError("子任务不存在")
        item = self._row_to_dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def set_item_running(self, item_id: str) -> None:
        timestamp = now_iso()
        with self.lock, self.connect() as conn:
            conn.execute(
                "UPDATE job_items SET status = 'running', started_at = COALESCE(started_at, ?), error = NULL WHERE id = ? AND status != 'cancelled'",
                (timestamp, item_id),
            )
            row = conn.execute("SELECT job_id FROM job_items WHERE id = ?", (item_id,)).fetchone()
            if row:
                conn.execute("UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ? AND status NOT IN ('cancelled')", (timestamp, row["job_id"]))

    def set_upstream_request_id(self, item_id: str, request_id: str) -> None:
        with self.lock, self.connect() as conn:
            conn.execute("UPDATE job_items SET upstream_request_id = ? WHERE id = ?", (request_id, item_id))

    def set_item_bizyair_key_id(self, item_id: str, key_id: str) -> None:
        with self.lock, self.connect() as conn:
            conn.execute("UPDATE job_items SET bizyair_key_id = ? WHERE id = ?", (key_id, item_id))

    def create_job_image_id(self, item_id: str, image_index: int) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"bizyair-job-image:{item_id}:{image_index}").hex

    def upsert_job_image(self, image: dict) -> None:
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO job_images (id, job_id, item_id, image_index, original_url, local_path, content_type, extension, size, sha256, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id, image_index) DO UPDATE SET
                  original_url = excluded.original_url,
                  local_path = excluded.local_path,
                  content_type = excluded.content_type,
                  extension = excluded.extension,
                  size = excluded.size,
                  sha256 = excluded.sha256,
                  status = excluded.status,
                  created_at = excluded.created_at
                """,
                (
                    image["id"], image["job_id"], image["item_id"], int(image["image_index"]), image["original_url"],
                    image.get("local_path") or "", image.get("content_type") or "application/octet-stream", image.get("extension") or ".img",
                    int(image.get("size") or 0), image.get("sha256"), image.get("status") or "ready", image.get("created_at") or now_iso(),
                ),
            )

    def get_job_image(self, image_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM job_images WHERE id = ? AND status = 'ready'", (image_id,)).fetchone()
        if not row:
            raise KeyError("图片不存在")
        return self._row_to_dict(row)

    def delete_job_image(self, image_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT * FROM job_images WHERE id = ? AND status = 'ready'", (image_id,)).fetchone()
            if not row:
                raise KeyError("图片不存在")
            conn.execute("UPDATE job_images SET status = 'deleted' WHERE id = ?", (image_id,))
        return self._row_to_dict(row)

    def finish_item(self, item_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        timestamp = now_iso()
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE job_items
                SET status = ?, result_json = ?, error = ?, finished_at = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, timestamp, timestamp, item_id),
            )
            row = conn.execute("SELECT job_id FROM job_items WHERE id = ?", (item_id,)).fetchone()
            if row:
                self._refresh_job_counts(conn, row["job_id"])

    def cancel_job(self, job_id: str) -> dict:
        timestamp = now_iso()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError("任务不存在")
            conn.execute("UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (timestamp, job_id))
            conn.execute(
                "UPDATE job_items SET status = 'cancelled', finished_at = ?, error = NULL WHERE job_id = ? AND status = 'queued'",
                (timestamp, job_id),
            )
            self._refresh_job_counts(conn, job_id)
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict:
        timestamp = now_iso()
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError("任务不存在")
            if row["status"] not in {"failed", "cancelled"}:
                raise ValueError("只有失败或已取消的任务可以重试")
            conn.execute(
                """
                UPDATE jobs
                SET status = 'queued', completed = 0, failed = 0, cancel_requested = 0, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, job_id),
            )
            conn.execute(
                """
                UPDATE job_items
                SET status = 'queued', result_json = NULL, error = NULL, upstream_request_id = NULL,
                    bizyair_key_id = NULL, created_at = ?, started_at = NULL, finished_at = NULL
                WHERE job_id = ?
                """,
                (timestamp, job_id),
            )
        return self.get_job(job_id)

    def insert_audit(self, ip: str, action: str, status: str, detail: dict) -> None:
        with self.lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_logs (timestamp, ip, action, status, detail_json) VALUES (?, ?, ?, ?, ?)",
                (now_iso(), ip, action, status, json.dumps(detail, ensure_ascii=False)),
            )

    def _job_summary(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        job = self._row_to_dict(row)
        items = [
            self._row_to_dict(item)
            for item in conn.execute(
                """
                SELECT id, job_id, prompt, payload_json, status, result_json, error, created_at, started_at, finished_at
                FROM job_items
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job["id"],),
            ).fetchall()
        ]
        self._hydrate_job(conn, job, items)
        job.pop("items", None)
        return job

    def _hydrate_job(self, conn: sqlite3.Connection, job: dict, items: list[dict]) -> None:
        job["params"] = json.loads(job.pop("params_json") or "{}")
        for item in items:
            if "payload_json" in item:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            if "result_json" in item:
                item["result"] = json.loads(item.pop("result_json") or "null") if item.get("result_json") else None
        job["items"] = items
        has_deleted_images = self._has_deleted_local_image_records(conn, items)
        job["latest_images"] = [] if has_deleted_images else self._extract_latest_images(items)
        job["image_records"] = self._extract_image_records(conn, job, items, has_deleted_images)
        self._add_status_counts(job, items)
        self._add_queue_metadata(conn, job, items)

    def _add_status_counts(self, job: dict, items: list[dict]) -> None:
        counts: dict[str, int] = {}
        for item in items:
            status = item.get("status") or "queued"
            counts[status] = counts.get(status, 0) + 1
        job["queued_count"] = counts.get("queued", 0)
        job["running_count"] = counts.get("running", 0)
        job["failed_count"] = counts.get("failed", 0)
        job["cancelled_count"] = counts.get("cancelled", 0)
        started_times = [item.get("started_at") for item in items if item.get("started_at")]
        finished_times = [item.get("finished_at") for item in items if item.get("finished_at")]
        job["first_started_at"] = min(started_times) if started_times else None
        job["last_finished_at"] = max(finished_times) if finished_times else None

    def _add_queue_metadata(self, conn: sqlite3.Connection, job: dict, items: list[dict]) -> None:
        active_items = [item for item in items if item.get("status") in {"queued", "running"}]
        first_created_at = min((item.get("created_at") for item in items if item.get("created_at")), default=job.get("created_at"))
        queue_ahead = 0
        if active_items and first_created_at:
            row = conn.execute(
                """
                SELECT COUNT(*) count
                FROM job_items
                JOIN jobs ON jobs.id = job_items.job_id
                WHERE job_items.status IN ('queued', 'running')
                  AND jobs.cancel_requested = 0
                  AND (job_items.created_at < ? OR (job_items.created_at = ? AND jobs.id != ?))
                """,
                (first_created_at, first_created_at, job["id"]),
            ).fetchone()
            queue_ahead = int(row["count"] or 0) if row else 0
        elapsed_from = job.get("first_started_at") or job.get("created_at")
        elapsed_start = iso_timestamp(elapsed_from)
        job["queue_ahead"] = queue_ahead if active_items else 0
        job["queue_position"] = queue_ahead + 1 if active_items and job.get("status") == "queued" else None
        job["estimated_wait_seconds"] = queue_ahead * 75 if active_items and job.get("status") == "queued" else None
        job["elapsed_seconds"] = max(0, int(time.time() - elapsed_start)) if elapsed_start else None

    def _extract_image_records(self, conn: sqlite3.Connection, job: dict, items: list[dict], has_deleted_images: bool = False) -> list[dict]:
        local_records = self._extract_local_image_records(job, items)
        if local_records:
            return local_records
        if has_deleted_images:
            return []
        records: list[dict] = []
        for item in sorted(items, key=lambda row: row.get("finished_at") or row.get("created_at") or "", reverse=True):
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else result.get("data", {}).get("outputs", {}) if isinstance(result.get("data"), dict) else {}
            for url in outputs.get("images", []) if isinstance(outputs, dict) else []:
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    records.append(
                        {
                            "url": url,
                            "display_url": url,
                            "download_url": url,
                            "original_url": url,
                            "local": False,
                            "label": f"{job.get('model', '')} {job.get('id', '')[:6]}",
                            "prompt": item.get("prompt") or "",
                            "model": job.get("model") or "",
                            "params": item.get("payload") or job.get("params") or {},
                            "job_id": job.get("id"),
                            "item_id": item.get("id"),
                            "created_at": item.get("created_at") or job.get("created_at"),
                            "finished_at": item.get("finished_at"),
                            "status": item.get("status"),
                        }
                    )
                if len(records) >= 12:
                    return records
        return records

    def _has_deleted_local_image_records(self, conn: sqlite3.Connection, items: list[dict]) -> bool:
        item_ids = [item.get("id") for item in items if item.get("id")]
        if not item_ids:
            return False
        placeholders = ",".join("?" for _ in item_ids)
        row = conn.execute(
            f"SELECT 1 FROM job_images WHERE item_id IN ({placeholders}) AND status = 'deleted' LIMIT 1",
            item_ids,
        ).fetchone()
        return row is not None

    def _extract_local_image_records(self, job: dict, items: list[dict]) -> list[dict]:
        item_map = {item.get("id"): item for item in items}
        item_ids = [item_id for item_id in item_map if item_id]
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM job_images WHERE item_id IN ({placeholders}) AND status = 'ready' ORDER BY created_at DESC, image_index ASC",
                item_ids,
            ).fetchall()
        records = []
        for row in rows:
            image = self._row_to_dict(row)
            item = item_map.get(image.get("item_id"), {})
            records.append(
                {
                    "id": image["id"],
                    "url": f"/api/images/{image['id']}",
                    "display_url": f"/api/images/{image['id']}",
                    "download_url": f"/api/images/{image['id']}/download",
                    "original_url": image.get("original_url") or "",
                    "local": True,
                    "label": f"{job.get('model', '')} {job.get('id', '')[:6]}",
                    "prompt": item.get("prompt") or "",
                    "model": job.get("model") or "",
                    "params": item.get("payload") or job.get("params") or {},
                    "job_id": job.get("id"),
                    "item_id": item.get("id"),
                    "created_at": item.get("created_at") or job.get("created_at"),
                    "finished_at": item.get("finished_at") or image.get("created_at"),
                    "status": item.get("status") or "succeeded",
                }
            )
        return records

    def _refresh_job_counts(self, conn: sqlite3.Connection, job_id: str) -> None:
        rows = conn.execute("SELECT status, COUNT(*) count FROM job_items WHERE job_id = ? GROUP BY status", (job_id,)).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        completed = counts.get("succeeded", 0)
        failed = counts.get("failed", 0)
        cancelled = counts.get("cancelled", 0)
        total = sum(counts.values())
        active = counts.get("running", 0)
        queued = counts.get("queued", 0)
        job_row = conn.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()
        cancel_requested = bool(job_row["cancel_requested"]) if job_row else False
        if total and completed == total:
            status = "succeeded"
        elif total and failed + completed + cancelled == total:
            status = "cancelled" if cancel_requested and not failed else "failed" if failed else "cancelled"
        elif active:
            status = "running"
        elif queued:
            status = "cancelled" if cancel_requested else "queued"
        else:
            status = "queued"
        conn.execute(
            "UPDATE jobs SET status = ?, completed = ?, failed = ?, updated_at = ? WHERE id = ?",
            (status, completed, failed, now_iso(), job_id),
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _extract_latest_images(items: list[dict]) -> list[str]:
        images: list[str] = []
        for item in items:
            result = item.get("result")
            if result is None and item.get("result_json"):
                try:
                    result = json.loads(item["result_json"])
                except json.JSONDecodeError:
                    result = None
            if isinstance(result, dict):
                outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else result.get("data", {}).get("outputs", {}) if isinstance(result.get("data"), dict) else {}
                for url in outputs.get("images", []) if isinstance(outputs, dict) else []:
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        images.append(url)
            if len(images) >= 8:
                break
        return images
