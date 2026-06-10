import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

APP_VERSION = "v0.1"
CLIENT_VERSION = "1.2.93"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PENDING_UPSTREAM_STATUSES = {"running", "queuing", "saving"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ALLOWED_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_UPLOAD_MIME_PREFIXES = ("image/",)
ALLOWED_CACHE_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
MAX_INPUT_IMAGES = 10
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

@dataclass(frozen=True)
class BizyAirKeyConfig:
    id: str
    api_key: str
    label: str = ""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    id: str
    api_key: str
    base_url: str
    models: tuple[str, ...]
    label: str = ""
    timeout_seconds: int = 300


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    admin_token: str
    bizyair_keys: tuple[BizyAirKeyConfig, ...]
    bizyair_base_url: str
    bizyair_wallet_url: str
    bizyair_metadata_url: str
    openai_compatible: OpenAICompatibleConfig | None
    custom_model_schemas: dict
    cors_origins: set[str]
    data_dir: Path
    log_dir: Path
    poll_interval_seconds: float
    max_poll_seconds: float
    max_upload_bytes: int
    upload_retry_attempts: int
    upload_retry_delay_seconds: float
    image_cache_dir: Path
    result_image_dir: Path
    image_cache_max_bytes: int
    image_cache_ttl_seconds: float
    image_cache_total_bytes: int
    worker_threads: int
    debug_requests: bool

    @property
    def bizyair_api_key(self) -> str:
        return self.bizyair_keys[0].api_key

    @property
    def model_providers(self) -> dict[str, str]:
        providers = {
            model: schema.get("provider", "bizyair")
            for model, schema in self.custom_model_schemas.items()
            if isinstance(schema, dict)
        }
        if self.openai_compatible:
            providers.update({model: self.openai_compatible.id for model in self.openai_compatible.models})
        return providers

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None

def load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} 必须是整数") from exc
    if minimum is not None and value < minimum:
        raise SystemExit(f"{name} 不能小于 {minimum}")
    return value


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} 必须是数字") from exc
    if minimum is not None and value < minimum:
        raise SystemExit(f"{name} 不能小于 {minimum}")
    return value


def split_env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def load_custom_model_schemas() -> dict:
    raw = os.getenv("CUSTOM_MODEL_SCHEMAS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"CUSTOM_MODEL_SCHEMAS 必须是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("CUSTOM_MODEL_SCHEMAS 必须是 JSON 对象")
    return value


def default_openai_schema() -> dict:
    return {
        "aspectRatios": ["1:1", "2:3", "3:2", "4:5", "5:4", "3:4", "4:3", "16:9", "9:16"],
        "resolutions": [],
        "qualities": ["auto", "low", "medium", "high"],
        "variants": [1, 2, 3, 4],
        "maxUrls": 10,
        "outputFormats": ["png", "jpeg", "webp"],
        "moderations": ["auto", "low"],
        "provider": "openai_compatible",
    }


def load_openai_compatible() -> tuple[OpenAICompatibleConfig | None, dict]:
    api_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "").strip().rstrip("/")
    models = tuple(split_env_list("OPENAI_COMPAT_MODELS"))
    if not any((api_key, base_url, models)):
        return None, {}
    if not api_key:
        raise SystemExit("配置 OPENAI_COMPAT_BASE_URL/OPENAI_COMPAT_MODELS 时必须同时配置 OPENAI_COMPAT_API_KEY")
    if not base_url:
        raise SystemExit("配置 OPENAI_COMPAT_API_KEY/OPENAI_COMPAT_MODELS 时必须同时配置 OPENAI_COMPAT_BASE_URL")
    if not models:
        raise SystemExit("配置 OPENAI_COMPAT_API_KEY/OPENAI_COMPAT_BASE_URL 时必须同时配置 OPENAI_COMPAT_MODELS")
    provider_id = os.getenv("OPENAI_COMPAT_PROVIDER_ID", "openai_compatible").strip() or "openai_compatible"
    config = OpenAICompatibleConfig(
        id=provider_id,
        api_key=api_key,
        base_url=base_url,
        models=models,
        label=os.getenv("OPENAI_COMPAT_LABEL", "OpenAI Compatible").strip(),
        timeout_seconds=env_int("OPENAI_COMPAT_TIMEOUT_SECONDS", 300, 1),
    )
    schemas = {model: {**default_openai_schema(), "provider": provider_id} for model in models}
    return config, schemas


def load_bizyair_keys() -> tuple[BizyAirKeyConfig, ...]:
    keys = split_env_list("BIZYAIR_API_KEYS")
    if not keys:
        single_key = os.getenv("BIZYAIR_API_KEY", os.getenv("APIKEY", "")).strip()
        if not single_key:
            if os.getenv("OPENAI_COMPAT_API_KEY", "").strip() or os.getenv("OPENAI_COMPAT_BASE_URL", "").strip() or split_env_list("OPENAI_COMPAT_MODELS"):
                return ()
            raise SystemExit("缺少 BIZYAIR_API_KEY 或 BIZYAIR_API_KEYS，请在环境变量或 .env 中配置 BizyAir 密钥")
        return (BizyAirKeyConfig(id="key-1", api_key=single_key, label=os.getenv("BIZYAIR_KEY_LABEL", "主账号").strip()),)

    labels = split_env_list("BIZYAIR_KEY_LABELS")
    configs: list[BizyAirKeyConfig] = []
    seen_ids: set[str] = set()
    for index, api_key in enumerate(keys):
        key_id = f"key-{index + 1}"
        if key_id in seen_ids:
            raise SystemExit(f"BizyAir Key id 重复: {key_id}")
        seen_ids.add(key_id)
        label = labels[index] if index < len(labels) else f"账号 {index + 1}"
        configs.append(BizyAirKeyConfig(id=key_id, api_key=api_key, label=label))
    return tuple(configs)


def update_env_value(name: str, value: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    replacement = f"{name}={value}"
    updated = False
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _old_value = stripped.split("=", 1)
        if key.strip() == name:
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.append(replacement)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_config() -> AppConfig:
    load_dotenv()
    host = os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = env_int("APP_PORT", int(os.getenv("UPLOAD_SERVER_PORT", "8787")), 1)
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    if len(admin_token) < 16:
        raise SystemExit("缺少 ADMIN_TOKEN，或长度小于 16 个字符")
    cors_raw = os.getenv("CORS_ORIGINS", "").strip()
    default_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    cors_origins = {origin.strip().rstrip("/") for origin in cors_raw.split(",") if origin.strip()} or default_origins
    data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
    log_dir = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs"))).expanduser()
    image_cache_dir = Path(os.getenv("IMAGE_CACHE_DIR", str(Path(tempfile.gettempdir()) / "bizyair-lan-image-cache"))).expanduser()
    result_image_dir = Path(os.getenv("RESULT_IMAGE_DIR", str(data_dir / "result-images"))).expanduser()
    openai_compatible, openai_model_schemas = load_openai_compatible()
    custom_model_schemas = {**openai_model_schemas, **load_custom_model_schemas()}
    return AppConfig(
        host=host,
        port=port,
        admin_token=admin_token,
        bizyair_keys=load_bizyair_keys(),
        bizyair_base_url=os.getenv("BIZYAIR_BASE_URL", "https://api.bizyair.cn/x/v1").rstrip("/"),
        bizyair_wallet_url=os.getenv("BIZYAIR_WALLET_URL", "https://api.bizyair.cn/y/v1/wallet"),
        bizyair_metadata_url=os.getenv("BIZYAIR_METADATA_URL", "https://api.bizyair.cn/x/v1/user/metadata"),
        openai_compatible=openai_compatible,
        custom_model_schemas=custom_model_schemas,
        cors_origins=cors_origins,
        data_dir=data_dir,
        log_dir=log_dir,
        poll_interval_seconds=env_float("POLL_INTERVAL_SECONDS", 5, 0.5),
        max_poll_seconds=env_float("MAX_POLL_SECONDS", 1800, 30),
        max_upload_bytes=min(env_int("MAX_UPLOAD_MB", 20, 1) * 1024 * 1024, MAX_UPLOAD_BYTES),
        upload_retry_attempts=env_int("UPLOAD_RETRY_ATTEMPTS", 2, 0),
        upload_retry_delay_seconds=env_float("UPLOAD_RETRY_DELAY_SECONDS", 1, 0),
        image_cache_dir=image_cache_dir,
        result_image_dir=result_image_dir,
        image_cache_max_bytes=env_int("IMAGE_CACHE_MAX_MB", 50, 1) * 1024 * 1024,
        image_cache_ttl_seconds=env_float("IMAGE_CACHE_TTL_HOURS", 168, 1) * 3600,
        image_cache_total_bytes=env_int("IMAGE_CACHE_TOTAL_MB", 2048, 1) * 1024 * 1024,
        worker_threads=env_int("WORKER_THREADS", 32, 1),
        debug_requests=os.getenv("DEBUG_REQUESTS", "").strip().lower() in {"1", "true", "yes", "on"},
    )
