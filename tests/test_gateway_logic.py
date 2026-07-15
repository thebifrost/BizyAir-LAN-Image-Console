import base64
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server.database import Database
from server.config import load_dotenv, load_openai_providers
from server.env_store import LEGACY_PROVIDER_KEYS, apply_field_updates, apply_provider_updates
from server.image_cache import ImageCache
from server.input_images import input_image_id_from_url, load_input_image, store_input_image
from server.job_runner import JobRunner
from server.key_pool import BizyAirKeyPool
from server.schemas import validate_params
from server.upstream_client import UpstreamClient, normalize_openai_image_result


def image_data_url(data: bytes = b"image-bytes", content_type: str = "image/png") -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def cache_config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        image_cache_dir=root / "cache",
        result_image_dir=root / "results",
        image_cache_max_bytes=1024 * 1024,
        image_cache_ttl_seconds=3600,
        image_cache_total_bytes=1024 * 1024,
    )


class FakeImageResponse:
    def __init__(self, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.status_code = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
        self.is_redirect = False
        self.is_permanent_redirect = False
        self._data = data

    def iter_content(self, chunk_size: int):
        yield self._data

    def close(self) -> None:
        pass


class FakeJsonResponse:
    def __init__(self, data: dict, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = str(data)

    def json(self) -> dict:
        return self._data


class FakeOpenAISession:
    def __init__(self, post_data: dict, get_data, get_status_codes: list[int] | None = None) -> None:
        self.post_data = post_data
        self.get_data = list(get_data) if isinstance(get_data, list) else [get_data]
        self.get_status_codes = list(get_status_codes or [])
        self.post_requests: list[tuple[tuple, dict]] = []
        self.get_urls: list[str] = []

    def post(self, *args, **kwargs) -> FakeJsonResponse:
        self.post_requests.append((args, kwargs))
        return FakeJsonResponse(self.post_data)

    def get(self, url: str, *args, **kwargs) -> FakeJsonResponse:
        self.get_urls.append(url)
        data = self.get_data.pop(0) if len(self.get_data) > 1 else self.get_data[0]
        status_code = self.get_status_codes.pop(0) if self.get_status_codes else 200
        return FakeJsonResponse(data, status_code=status_code)


def upstream_config() -> SimpleNamespace:
    return SimpleNamespace(
        bizyair_base_url="https://bizyair.example",
        bizyair_wallet_url="https://bizyair.example/wallet",
        bizyair_metadata_url="https://bizyair.example/metadata",
    )


class GatewayLogicTests(unittest.TestCase):
    def test_key_pool_empty_has_clear_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "BizyAir"):
            BizyAirKeyPool(()).pick()

    def test_load_dotenv_does_not_override_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("BIZYAIR_TEST_EXISTING=from-dotenv\nBIZYAIR_TEST_NEW=from-dotenv\n", encoding="utf-8")

            with patch("server.config.PROJECT_ROOT", root), patch.dict(os.environ, {"BIZYAIR_TEST_EXISTING": "from-env"}, clear=False):
                load_dotenv()

                self.assertEqual(os.environ["BIZYAIR_TEST_EXISTING"], "from-env")
                self.assertEqual(os.environ["BIZYAIR_TEST_NEW"], "from-dotenv")

    def test_validate_params_allows_local_and_data_urls_only_for_third_party(self) -> None:
        local_input = "/api/input-images/" + "a" * 32 + "/file"
        local_result = "/api/images/" + "b" * 32
        remote = "https://example.com/input.png"
        data_url = image_data_url()
        schemas = {
            "biz": {"maxUrls": 99},
            "third": {"maxUrls": 99, "provider": "openai_compatible"},
        }

        biz_clean = validate_params("biz", {"urls": [remote, local_input, local_result, data_url]}, schemas)
        third_clean = validate_params("third", {"urls": [remote, local_input, local_result, data_url]}, schemas)

        self.assertEqual(biz_clean["urls"], [remote])
        self.assertEqual(third_clean["urls"], [remote, local_input, local_result, data_url])

    def test_public_api_like_urls_are_not_treated_as_local_images(self) -> None:
        image_id = "a" * 32
        public_input = f"https://example.com/api/input-images/{image_id}/file"
        public_result = f"https://example.com/api/images/{image_id}"
        schemas = {"biz": {"maxUrls": 99}}

        self.assertEqual(input_image_id_from_url(public_input), "")
        self.assertEqual(JobRunner._local_result_image_id_from_url(public_result), "")
        self.assertEqual(validate_params("biz", {"urls": [public_result]}, schemas)["urls"], [public_result])

    def test_input_image_storage_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stored = store_input_image(directory, b"abc", "sample.png", "image/png", ".png")

            self.assertEqual(input_image_id_from_url(f"/api/input-images/{stored.id}/file"), stored.id)
            self.assertEqual(input_image_id_from_url(f"http://127.0.0.1:8787/api/input-images/{stored.id}/file"), stored.id)

            loaded = load_input_image(directory, stored.id)
            self.assertEqual(loaded.path.read_bytes(), b"abc")
            self.assertEqual(loaded.content_type, "image/png")
            self.assertEqual(loaded.extension, ".png")

    def test_image_cache_rejects_non_global_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = ImageCache(cache_config(Path(tmp)), logging.getLogger("test"))

            with patch("server.image_cache.socket.getaddrinfo", return_value=[(None, None, None, "", ("100.64.0.1", 80))]):
                with self.assertRaisesRegex(ValueError, "不允许"):
                    cache._validate_url("http://example.com/image.png")

    def test_image_cache_sniffs_generic_content_type(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        with tempfile.TemporaryDirectory() as tmp:
            cache = ImageCache(cache_config(Path(tmp)), logging.getLogger("test"))

            with patch("server.image_cache.socket.getaddrinfo", return_value=[(None, None, None, "", ("93.184.216.34", 443))]), patch(
                "server.image_cache.requests.get",
                return_value=FakeImageResponse(png_bytes),
            ):
                path, content_type = cache.get("https://example.com/image.png")

            self.assertEqual(content_type, "image/png")
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(path.read_bytes(), png_bytes)

    def test_openai_image_job_response_is_polled_for_result(self) -> None:
        client = UpstreamClient(upstream_config())
        client.session = FakeOpenAISession(
            {
                "job_id": "imgjob_test",
                "status": "queued",
                "result_url": "/v1/image-jobs/imgjob_test/result",
                "retry_after": 0,
            },
            {"data": [{"url": "https://example.com/final.png"}]},
        )
        provider = SimpleNamespace(api_key="sk-test", base_url="https://api.example/v1", timeout_seconds=5)

        with patch("server.upstream_client.time.sleep", return_value=None):
            response, data = client.create_openai_image(provider, "gpt-image-2", {"prompt": "draw"})

        self.assertTrue(response.ok)
        self.assertEqual(client.session.get_urls, ["https://api.example/v1/image-jobs/imgjob_test/result"])
        self.assertEqual(normalize_openai_image_result(data)["outputs"]["images"], ["https://example.com/final.png"])

    def test_openai_image_edit_requests_base64_result(self) -> None:
        client = UpstreamClient(upstream_config())
        client.session = FakeOpenAISession({"data": [{"b64_json": "aW1hZ2U="}]}, {})
        provider = SimpleNamespace(api_key="sk-test", base_url="https://api.example/v1", timeout_seconds=5)

        response, data = client.create_openai_image(
            provider,
            "gpt-image-2",
            {"prompt": "draw"},
            reference_images=[{"filename": "input.png", "content_type": "image/png", "data": b"image"}],
        )

        self.assertTrue(response.ok)
        self.assertEqual(data, {"data": [{"b64_json": "aW1hZ2U="}]})
        self.assertEqual(client.session.post_requests[0][1]["data"]["response_format"], "b64_json")

    def test_openai_image_job_not_ready_response_keeps_polling(self) -> None:
        client = UpstreamClient(upstream_config())
        client.session = FakeOpenAISession(
            {
                "job_id": "imgjob_test",
                "status": "queued",
                "result_url": "/v1/image-jobs/imgjob_test/result",
                "retry_after": 0,
            },
            [
                {"error": {"code": "IMAGE_JOB_NOT_READY", "message": "image generation job is not finished"}},
                {"data": [{"url": "https://example.com/final.png"}]},
            ],
            get_status_codes=[400, 200],
        )
        provider = SimpleNamespace(api_key="sk-test", base_url="https://api.example/v1", timeout_seconds=5)

        with patch("server.upstream_client.time.sleep", return_value=None):
            response, data = client.create_openai_image(provider, "gpt-image-2", {"prompt": "draw"})

        self.assertTrue(response.ok)
        self.assertEqual(
            client.session.get_urls,
            [
                "https://api.example/v1/image-jobs/imgjob_test/result",
                "https://api.example/v1/image-jobs/imgjob_test/result",
            ],
        )
        self.assertEqual(normalize_openai_image_result(data)["outputs"]["images"], ["https://example.com/final.png"])

    def test_openai_image_result_normalizes_nested_outputs(self) -> None:
        result = normalize_openai_image_result({"result": {"outputs": {"images": ["https://example.com/final.png"]}}})

        self.assertEqual(result["outputs"]["images"], ["https://example.com/final.png"])

    def test_openai_image_result_resolves_relative_asset_urls(self) -> None:
        result = normalize_openai_image_result(
            {"data": [{"url": "/v1/image-jobs/imgjob_test/assets/imgasset_test"}]},
            base_url="https://api.example/v1",
        )

        self.assertEqual(result["outputs"]["images"], ["https://api.example/v1/image-jobs/imgjob_test/assets/imgasset_test"])

    def test_database_does_not_claim_cancelled_running_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            job = db.create_job("gpt-image-2", ["draw"], {})
            item_id = job["items"][0]["id"]

            self.assertTrue(db.set_item_running(item_id))
            db.cancel_job(job["id"])

            self.assertFalse(db.set_item_running(item_id))

    def test_runner_rejects_new_enqueue_after_stop_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            config = SimpleNamespace(
                bizyair_keys=(),
                openai_providers={},
            )
            runner = JobRunner(config, db, logging.getLogger("test"), upstream_client=None, image_cache=None)

            runner.stop()

            with self.assertRaisesRegex(RuntimeError, "停止"):
                runner.enqueue("item-1")

    def test_data_url_result_is_archived_as_local_job_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = Database(root / "jobs.sqlite")
            cache = ImageCache(cache_config(root), logging.getLogger("test"))
            config = SimpleNamespace(
                bizyair_keys=(),
                openai_compatible=None,
                openai_providers={},
                imgbb_api_key="",
            )
            runner = JobRunner(config, db, logging.getLogger("test"), upstream_client=None, image_cache=cache)
            job = db.create_job("third", ["draw"], {})
            item = db.get_item_for_processing(job["items"][0]["id"])
            result = {"outputs": {"images": [image_data_url(b"result-bytes")]}}

            runner._archive_result_images(item, result)
            db.finish_item(item["id"], "succeeded", result=result)
            hydrated = db.get_job(job["id"])

            archived_url = result["outputs"]["images"][0]
            self.assertTrue(archived_url.startswith("/api/images/"))
            self.assertEqual(hydrated["latest_images"], [archived_url])
            self.assertEqual(len(hydrated["image_records"]), 1)
            self.assertTrue(hydrated["image_records"][0]["local"])
            self.assertEqual(hydrated["image_records"][0]["download_url"], archived_url + "/download")

    def test_database_keeps_data_url_visible_if_archiving_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            job = db.create_job("third", ["draw"], {})
            item_id = job["items"][0]["id"]
            data_url = image_data_url(b"inline-result")

            db.finish_item(item_id, "succeeded", result={"outputs": {"images": [data_url]}})
            hydrated = db.get_job(job["id"])

            self.assertEqual(hydrated["latest_images"], [data_url])
            self.assertEqual(hydrated["image_records"][0]["display_url"], data_url)

    def test_job_list_summary_omits_inline_result_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            job = db.create_job("third", ["draw"], {})
            item_id = job["items"][0]["id"]
            data_url = image_data_url(b"inline-result")

            db.finish_item(item_id, "succeeded", result={"outputs": {"images": [data_url]}})

            summary = db.list_jobs()[0]
            hydrated = db.get_job(job["id"])

            self.assertNotIn("items", summary)
            self.assertEqual(summary["latest_images"], [])
            self.assertEqual(summary["image_records"], [])
            self.assertEqual(hydrated["latest_images"], [data_url])

    def test_job_list_summary_keeps_all_legacy_remote_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            job = db.create_job("gpt-image-2", ["draw"], {})
            item_id = job["items"][0]["id"]
            urls = [f"https://example.com/legacy-{index}.png" for index in range(16)]

            db.finish_item(item_id, "succeeded", result={"outputs": {"images": urls}})

            summary = db.list_jobs()[0]

            self.assertEqual([record["display_url"] for record in summary["image_records"]], urls)

    def test_job_list_summary_merges_partial_local_archive_with_legacy_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "jobs.sqlite")
            job = db.create_job("gpt-image-2", ["draw"], {})
            item_id = job["items"][0]["id"]
            urls = ["https://example.com/archived.png", "https://example.com/legacy.png"]
            db.finish_item(item_id, "succeeded", result={"outputs": {"images": urls}})
            image_id = db.create_job_image_id(item_id, 0)
            db.upsert_job_image(
                {
                    "id": image_id,
                    "job_id": job["id"],
                    "item_id": item_id,
                    "image_index": 0,
                    "original_url": urls[0],
                    "local_path": str(Path(tmp) / "archived.png"),
                    "content_type": "image/png",
                    "extension": ".png",
                    "size": 1,
                    "status": "ready",
                }
            )

            summary = db.list_jobs()[0]

            self.assertEqual(
                [record["display_url"] for record in summary["image_records"]],
                [f"/api/images/{image_id}", urls[1]],
            )

    def test_loads_multiple_openai_compatible_providers(self) -> None:
        env = {
            "OPENAI_COMPAT_PROVIDERS": "moyuu,openrouter",
            "OPENAI_COMPAT_MOYUU_API_KEY": "sk-moyuu",
            "OPENAI_COMPAT_MOYUU_BASE_URL": "https://moyuu.example/v1",
            "OPENAI_COMPAT_MOYUU_MODELS": "moyuu-gpt-image-2=gpt-image-2",
            "OPENAI_COMPAT_MOYUU_CONCURRENCY": "3",
            "OPENAI_COMPAT_OPENROUTER_API_KEY": "sk-openrouter",
            "OPENAI_COMPAT_OPENROUTER_BASE_URL": "https://openrouter.example/v1",
            "OPENAI_COMPAT_OPENROUTER_MODELS": "or-image=provider/image",
            "OPENAI_COMPAT_OPENROUTER_SEND_REFERENCE_IMAGES_AS_FILES": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            providers, schemas = load_openai_providers()

        self.assertEqual(set(providers), {"moyuu", "openrouter"})
        self.assertEqual(providers["moyuu"].model_map["moyuu-gpt-image-2"], "gpt-image-2")
        self.assertEqual(providers["moyuu"].concurrency, 3)
        self.assertFalse(providers["moyuu"].send_reference_images_as_files)
        self.assertFalse(providers["openrouter"].send_reference_images_as_files)
        self.assertEqual(schemas["or-image"]["provider"], "openrouter")

    def test_blank_runtime_env_fields_remove_values_instead_of_writing_empty_strings(self) -> None:
        updates: dict[str, str] = {}
        removals: set[str] = set()

        apply_field_updates({"APP_PORT": "", "LOG_LEVEL": "   ", "ADMIN_TOKEN": ""}, updates, removals)

        self.assertEqual(updates, {})
        self.assertEqual(removals, {"APP_PORT", "LOG_LEVEL"})

    def test_provider_updates_remove_legacy_openai_compatible_env(self) -> None:
        updates: dict[str, str] = {}
        removals: set[str] = set()
        env = {
            "OPENAI_COMPAT_PROVIDER_ID": "legacy",
            "OPENAI_COMPAT_API_KEY": "sk-legacy",
            "OPENAI_COMPAT_BASE_URL": "https://legacy.example/v1",
            "OPENAI_COMPAT_MODELS": "legacy-image",
        }

        with patch.dict(os.environ, env, clear=True):
            apply_provider_updates(
                [
                    {
                        "id": "legacy",
                        "label": "Legacy",
                        "base_url": "https://legacy.example/v1",
                        "models": "legacy-image=upstream-image",
                        "api_key": "",
                    }
                ],
                updates,
                removals,
            )

        self.assertTrue(set(LEGACY_PROVIDER_KEYS).issubset(removals))
        self.assertEqual(updates["OPENAI_COMPAT_PROVIDERS"], "legacy")
        self.assertEqual(updates["OPENAI_COMPAT_LEGACY_API_KEY"], "sk-legacy")


if __name__ == "__main__":
    unittest.main()
