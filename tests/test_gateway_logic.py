import base64
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server.database import Database
from server.config import load_openai_providers
from server.env_store import LEGACY_PROVIDER_KEYS, apply_field_updates, apply_provider_updates
from server.image_cache import ImageCache
from server.input_images import input_image_id_from_url, load_input_image, store_input_image
from server.job_runner import JobRunner
from server.key_pool import BizyAirKeyPool
from server.schemas import validate_params


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


class GatewayLogicTests(unittest.TestCase):
    def test_key_pool_empty_has_clear_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "BizyAir"):
            BizyAirKeyPool(()).pick()

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
