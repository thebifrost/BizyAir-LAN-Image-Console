import requests

from .config import AppConfig, BizyAirKeyConfig, CLIENT_VERSION, OpenAICompatibleConfig

class UpstreamClient:
    def __init__(self, config: AppConfig):
        self.base_url = config.bizyair_base_url.rstrip("/")
        self.wallet_url = config.bizyair_wallet_url
        self.metadata_url = config.bizyair_metadata_url
        self.session = requests.Session()

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
        response = self.session.post(f"{self.base_url}{path}", json=payload, headers=self.headers(key), timeout=timeout)
        return response, self.json_response(response)

    def get(self, path: str, key: BizyAirKeyConfig, timeout: int = 60) -> tuple[requests.Response, dict]:
        response = self.session.get(f"{self.base_url}{path}", headers=self.headers(key), timeout=timeout)
        return response, self.json_response(response)

    def get_account(self, key: BizyAirKeyConfig) -> tuple[requests.Response, requests.Response, dict, dict]:
        headers = {"Authorization": f"Bearer {key.api_key}", "accept": "application/json"}
        wallet = self.session.get(self.wallet_url, headers=headers, timeout=30)
        metadata = self.session.get(self.metadata_url, headers=headers, timeout=30)
        return wallet, metadata, self.json_response(wallet), self.json_response(metadata)

    @staticmethod
    def openai_headers(provider: OpenAICompatibleConfig) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {provider.api_key}",
        }

    def create_openai_image(self, provider: OpenAICompatibleConfig, model: str, payload: dict) -> tuple[requests.Response, dict]:
        prompt = str(payload.get("prompt") or "")
        urls = payload.get("urls") if isinstance(payload.get("urls"), list) else []
        if urls:
            prompt = f"{prompt}\n\nReference images:\n" + "\n".join(str(url) for url in urls)
        body = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
        }
        variants = payload.get("variants")
        if variants:
            body["n"] = variants
        for key in ("size", "quality", "aspect_ratio", "resolution", "style", "background", "moderation", "seed", "temperature", "top_p"):
            if key in payload and payload[key] not in (None, ""):
                body[key] = payload[key]
        response = self.session.post(
            f"{provider.base_url}/images/generations",
            json=body,
            headers=self.openai_headers(provider),
            timeout=provider.timeout_seconds,
        )
        return response, self.json_response(response)


def normalize_openai_image_result(data: dict) -> dict:
    images: list[str] = []
    for item in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url:
            images.append(url)
            continue
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            images.append(f"data:image/png;base64,{b64_json}")
    return {"status": "succeeded", "outputs": {"images": images}, "raw": data}

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
