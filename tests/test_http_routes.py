import unittest
from types import SimpleNamespace
from urllib.parse import urlparse

from server.http_routes import dispatch_delete, dispatch_get, dispatch_post


class FakeDatabase:
    def __init__(self) -> None:
        self.deleted_images: list[str] = []
        self.retried_jobs: list[str] = []

    def list_jobs(self) -> list[dict]:
        return [{"id": "job-list"}]

    def get_job(self, job_id: str) -> dict:
        return {"id": job_id}

    def retry_job(self, job_id: str) -> dict:
        self.retried_jobs.append(job_id)
        return {"id": job_id, "items": [{"id": "item-1", "status": "queued"}]}

    def cancel_job(self, job_id: str) -> dict:
        return {"id": job_id, "status": "cancelled"}

    def delete_job_image(self, image_id: str) -> dict:
        self.deleted_images.append(image_id)
        return {"id": image_id, "local_path": ""}


class FakeRunner:
    def __init__(self) -> None:
        self.enqueued_jobs: list[dict] = []

    def enqueue_job(self, job: dict) -> None:
        self.enqueued_jobs.append(job)


class FakeHandler:
    def __init__(self) -> None:
        self.auth_actions: list[str] = []
        self.responses: list[tuple[int, dict]] = []
        self.audits: list[tuple[str, str, dict]] = []
        self.called: list[tuple[str, object]] = []
        self.server = SimpleNamespace(config=SimpleNamespace(), db=FakeDatabase(), runner=FakeRunner())

    def _require_auth(self, action: str) -> None:
        self.auth_actions.append(action)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self.responses.append((status, payload))

    def _public_config(self) -> dict:
        return {"version": "test"}

    def _audit(self, action: str, status: str, detail: dict) -> None:
        self.audits.append((action, status, detail))

    def _serve_static(self, path: str) -> None:
        self.called.append(("static", path))

    def _handle_health(self) -> None:
        self.called.append(("health", None))

    def _handle_openai_models(self) -> None:
        self.called.append(("openai_models", None))

    def _handle_inputs(self, parsed) -> None:
        self.called.append(("inputs", parsed.query))

    def _handle_account(self) -> None:
        self.called.append(("account", None))

    def _handle_admin_runtime(self) -> None:
        self.called.append(("admin_runtime", None))

    def _handle_logs(self, parsed) -> None:
        self.called.append(("logs", parsed.query))

    def _handle_image_cache(self, parsed) -> None:
        self.called.append(("image_cache", parsed.query))

    def _handle_input_image(self, path: str) -> None:
        self.called.append(("input_image", path))

    def _handle_local_image(self, path: str) -> None:
        self.called.append(("local_image", path))

    def _handle_upload(self) -> None:
        self.called.append(("upload", None))

    def _handle_openai_chat_completions(self) -> None:
        self.called.append(("openai_chat", None))

    def _handle_openai_images_generations(self) -> None:
        self.called.append(("openai_generations", None))

    def _handle_openai_images_edits(self) -> None:
        self.called.append(("openai_edits", None))

    def _handle_admin_config(self) -> None:
        self.called.append(("admin_config", None))

    def _handle_admin_env(self) -> None:
        self.called.append(("admin_env", None))

    def _handle_admin_restart(self) -> None:
        self.called.append(("admin_restart", None))

    def _handle_create_job(self) -> None:
        self.called.append(("create_job", None))

    def _delete_local_image_file(self, image: dict) -> None:
        self.called.append(("delete_file", image.get("id")))


class HttpRouteTests(unittest.TestCase):
    def test_get_config_requires_auth_and_returns_public_config(self) -> None:
        handler = FakeHandler()

        dispatch_get(handler, urlparse("/api/config"))

        self.assertEqual(handler.auth_actions, ["config"])
        self.assertEqual(handler.responses, [(200, {"status": True, "data": {"version": "test"}})])

    def test_get_static_paths_do_not_require_auth(self) -> None:
        handler = FakeHandler()

        dispatch_get(handler, urlparse("/static/js/app.js"))

        self.assertEqual(handler.auth_actions, [])
        self.assertEqual(handler.called, [("static", "/static/js/app.js")])

    def test_post_retry_job_requeues_items_and_audits(self) -> None:
        handler = FakeHandler()

        dispatch_post(handler, urlparse("/api/jobs/job-123/retry"))

        self.assertEqual(handler.auth_actions, ["jobs:create"])
        self.assertEqual(handler.server.db.retried_jobs, ["job-123"])
        self.assertEqual(handler.server.runner.enqueued_jobs[0]["id"], "job-123")
        self.assertEqual(handler.audits, [("jobs:retry", "ok", {"job_id": "job-123"})])

    def test_delete_image_validates_id_after_auth(self) -> None:
        handler = FakeHandler()

        dispatch_delete(handler, urlparse("/api/images/not-an-image"))

        self.assertEqual(handler.auth_actions, ["images:delete"])
        self.assertEqual(handler.responses, [(404, {"status": False, "message": "Not found"})])
        self.assertEqual(handler.server.db.deleted_images, [])


if __name__ == "__main__":
    unittest.main()
