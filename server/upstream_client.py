import threading
import time
from urllib.parse import urljoin

import requests

from .config import AppConfig, BizyAirKeyConfig, CLIENT_VERSION, OpenAICompatibleConfig

OPENAI_TRANSIENT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.SSLError,
)

class UpstreamClient:
    def __init__(self, config: AppConfig):
        self.base_url = config.bizyair_base_url.rstrip("/")
        self.wallet_url = config.bizyair_wallet_url
        self.metadata_url = config.bizyair_metadata_url
        self.session = requests.Session()
        self._thread_local = threading.local()

    def _session(self):
        if not isinstance(self.session, requests.Session):
            return self.session
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
        return session

    @staticmethod
    def headers(key: BizyAirKeyConfig) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-bizyair-client-version": CLIENT_VERSION,
            "X-Client-Type": "bizyair",
            "Authorization": f"Bearer {key.api_key}",
        }

    @staticmethod
    def json_response(response: requests.Response) -> dict:
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def post(self, path: str, key: BizyAirKeyConfig, payload: dict, timeout: int = 60) -> tuple[requests.Response, dict]:
        response = self._session().post(f"{self.base_url}{path}", json=payload, headers=self.headers(key), timeout=timeout)
        return response, self.json_response(response)

    def get(self, path: str, key: BizyAirKeyConfig, timeout: int = 60) -> tuple[requests.Response, dict]:
        response = self._session().get(f"{self.base_url}{path}", headers=self.headers(key), timeout=timeout)
        return response, self.json_response(response)

    def get_account(self, key: BizyAirKeyConfig) -> tuple[requests.Response, requests.Response, dict, dict]:
        headers = {"Authorization": f"Bearer {key.api_key}", "accept": "application/json"}
        session = self._session()
        wallet = session.get(self.wallet_url, headers=headers, timeout=30)
        metadata = session.get(self.metadata_url, headers=headers, timeout=30)
        return wallet, metadata, self.json_response(wallet), self.json_response(metadata)

    @staticmethod
    def openai_headers(provider: OpenAICompatibleConfig, content_type: str | None = "application/json") -> dict:
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {provider.api_key}",
        }
        if content_type:
            headers["content-type"] = content_type
        return headers

    def _openai_request(self, method: str, url: str, provider: OpenAICompatibleConfig, **kwargs) -> requests.Response:
        last_error = None
        session = self._session()
        request = getattr(session, method.lower())
        for attempt in range(1, 4):
            try:
                return request(url, timeout=provider.timeout_seconds, **kwargs)
            except OPENAI_TRANSIENT_ERRORS as exc:
                last_error = exc
                if attempt >= 3:
                    raise
                time.sleep(min(2 * attempt, 5))
        raise last_error

    def create_openai_image(self, provider: OpenAICompatibleConfig, model: str, payload: dict, reference_images: list[dict] | None = None) -> tuple[requests.Response, dict]:
        reference_images = reference_images or []
        if reference_images:
            response, data = self._create_openai_image_edit(provider, model, payload, reference_images)
        else:
            response, data = self._create_openai_image_generation(provider, model, payload)
        if response.ok and is_openai_image_job_response(data):
            return self._poll_openai_image_job(provider, data)
        return response, data

    def _openai_image_body(self, model: str, payload: dict, include_reference_urls: bool) -> dict:
        prompt = str(payload.get("prompt") or "")
        urls = payload.get("urls") if isinstance(payload.get("urls"), list) else []
        if include_reference_urls and urls:
            prompt = f"{prompt}\n\nReference images:\n" + "\n".join(str(url) for url in urls)
        body = {
            "model": model,
            "prompt": prompt,
            "response_format": "b64_json",
        }
        variants = payload.get("variants", 1)
        if variants:
            body["n"] = variants
        for key in ("size", "quality", "aspect_ratio", "resolution", "style", "background", "moderation", "output_format", "output_compression", "seed", "temperature", "top_p"):
            if payload.get("size") and key in {"aspect_ratio", "resolution"}:
                continue
            if key in payload and payload[key] not in (None, ""):
                body[key] = payload[key]
        return body

    def _create_openai_image_generation(self, provider: OpenAICompatibleConfig, model: str, payload: dict) -> tuple[requests.Response, dict]:
        body = self._openai_image_body(model, payload, include_reference_urls=True)
        response = self._openai_request(
            "POST",
            f"{provider.base_url}/images/generations",
            provider,
            json=body,
            headers=self.openai_headers(provider),
        )
        return response, self.json_response(response)

    def _create_openai_image_edit(self, provider: OpenAICompatibleConfig, model: str, payload: dict, reference_images: list[dict]) -> tuple[requests.Response, dict]:
        body = self._openai_image_body(model, payload, include_reference_urls=False)
        files = []
        for index, image in enumerate(reference_images):
            filename = str(image.get("filename") or f"input-{index + 1}.png")
            content_type = str(image.get("content_type") or "application/octet-stream")
            files.append(("image[]", (filename, image["data"], content_type)))
        response = self._openai_request(
            "POST",
            f"{provider.base_url}/images/edits",
            provider,
            data={key: str(value) for key, value in body.items() if value not in (None, "")},
            files=files,
            headers=self.openai_headers(provider, content_type=None),
        )
        return response, self.json_response(response)

    def _poll_openai_image_job(self, provider: OpenAICompatibleConfig, data: dict) -> tuple[requests.Response, dict]:
        deadline = time.monotonic() + provider.timeout_seconds
        current_data = data
        result_url = openai_image_job_result_url(current_data)
        status_url = openai_image_job_status_url(current_data)
        response = None
        while True:
            delay = openai_image_job_retry_after(current_data)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"OpenAI-compatible image job polling timed out: {current_data}")
            time.sleep(min(delay, remaining))
            target = status_url or result_url
            if not target:
                raise RuntimeError(f"OpenAI-compatible image job response missing result_url/status_url: {current_data}")
            response = self._openai_request(
                "GET",
                self._openai_job_url(provider, target),
                provider,
                headers=self.openai_headers(provider, content_type=None),
            )
            current_data = self.json_response(response)
            if not response.ok:
                if is_openai_image_job_not_ready(current_data):
                    result_url = openai_image_job_result_url(current_data) or result_url
                    status_url = openai_image_job_status_url(current_data) or status_url
                    continue
                return response, current_data
            if normalize_openai_image_result(current_data, base_url=provider.base_url).get("outputs", {}).get("images"):
                return response, current_data
            status = openai_image_job_status(current_data)
            result_url = openai_image_job_result_url(current_data) or result_url
            status_url = openai_image_job_status_url(current_data) or status_url
            if status in {"succeeded", "completed", "success"} and result_url:
                response = self._openai_request(
                    "GET",
                    self._openai_job_url(provider, result_url),
                    provider,
                    headers=self.openai_headers(provider, content_type=None),
                )
                current_data = self.json_response(response)
                if not response.ok and is_openai_image_job_not_ready(current_data):
                    continue
                return response, current_data
            if status in {"failed", "cancelled", "canceled", "error"}:
                return response, current_data

    @staticmethod
    def _openai_job_url(provider: OpenAICompatibleConfig, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urljoin(f"{provider.base_url.rstrip('/')}/", path_or_url)


def normalize_openai_image_result(data: dict, output_format: str = "png", base_url: str = "") -> dict:
    mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp", "png": "image/png"}.get(output_format, "image/png")
    images: list[str] = []
    collect_openai_images(data, mime, images, base_url)
    return {"status": "succeeded", "outputs": {"images": images}, "raw": data}


def collect_openai_images(value, mime: str, images: list[str], base_url: str = "") -> None:
    if isinstance(value, list):
        for item in value:
            collect_openai_images(item, mime, images, base_url)
        return
    if not isinstance(value, dict):
        return
    url = value.get("url")
    if isinstance(url, str) and url:
        image_url = normalize_openai_image_url(url, base_url)
        if image_url:
            images.append(image_url)
    b64_json = value.get("b64_json")
    if isinstance(b64_json, str) and b64_json:
        images.append(f"data:{mime};base64,{b64_json}")
    image_values = value.get("images")
    if isinstance(image_values, list):
        for image in image_values:
            if isinstance(image, str) and image:
                image_url = normalize_openai_image_url(image, base_url)
                if image_url:
                    images.append(image_url)
            else:
                collect_openai_images(image, mime, images, base_url)
    outputs = value.get("outputs")
    if isinstance(outputs, dict):
        collect_openai_images(outputs, mime, images, base_url)
    for key in ("data", "result", "response"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            collect_openai_images(nested, mime, images, base_url)


def normalize_openai_image_url(value: str, base_url: str = "") -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "data:image/")):
        return value
    if base_url:
        return urljoin(f"{base_url.rstrip('/')}/", value)
    return value


def is_openai_image_job_response(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("job_id") and (data.get("result_url") or data.get("status_url")))


def openai_image_job_status(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    status = data.get("status") or data.get("state")
    return str(status or "").strip().lower()


def openai_image_job_result_url(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get("result_url")
    return str(value or "").strip()


def openai_image_job_status_url(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get("status_url")
    return str(value or "").strip()


def openai_image_job_retry_after(data: dict) -> float:
    if not isinstance(data, dict):
        return 2.0
    try:
        value = float(data.get("retry_after") or 2)
    except (TypeError, ValueError):
        value = 2.0
    return min(max(value, 0.25), 30.0)


def is_openai_image_job_not_ready(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = str(error.get("code") or data.get("code") or "").strip().upper()
    message = str(error.get("message") or data.get("message") or "").strip().lower()
    return code == "IMAGE_JOB_NOT_READY" or "job is not finished" in message or "not ready" in message


def summarize_account(wallet: dict, metadata: dict) -> dict:
    wallet_data = wallet.get("data", {}) if isinstance(wallet, dict) else {}
    metadata_data = metadata.get("data", {}) if isinstance(metadata, dict) else {}
    expire_map = metadata_data.get("sub_expire_at") if isinstance(metadata_data.get("sub_expire_at"), dict) else {}
    level = metadata_data.get("level", "")
    expire_at = "--"
    if expire_map:
        expire_at = expire_map.get(level)
        if expire_at is None:
            expire_at = expire_map.get(str(level))
        if expire_at is None:
            expire_at = next(iter(expire_map.values()), "--")
    return {
        "account": metadata_data.get("name") or "未知账户",
        "status": metadata_data.get("status") or "未知状态",
        "membership": metadata_data.get("user_level_str") or f"等级 {metadata_data.get('level', '--')}",
        "expire_at": expire_at,
        "total_balance": wallet_data.get("total_balance") or wallet_data.get("total_balance_amount") or "--",
        "charge_balance": wallet_data.get("charge_balance") or wallet_data.get("charge_balance_amount") or "--",
        "gift_balance": wallet_data.get("gift_balance") or wallet_data.get("gift_balance_amount") or "--",
    }
