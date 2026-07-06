import json
import os
import re
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

OPENAI_SIZE_PRESETS = {
    "1K": {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "2:3": "1024x1536",
        "16:9": "1280x720",
        "9:16": "720x1280",
        "4:3": "1024x768",
        "3:4": "768x1024",
        "21:9": "1280x544",
    },
    "2K": {
        "1:1": "2048x2048",
        "3:2": "2160x1440",
        "2:3": "1440x2160",
        "16:9": "2560x1440",
        "9:16": "1440x2560",
        "4:3": "2048x1536",
        "3:4": "1536x2048",
        "21:9": "2560x1088",
    },
    "4K": {
        "1:1": "2880x2880",
        "3:2": "3456x2304",
        "2:3": "2304x3456",
        "16:9": "3840x2160",
        "9:16": "2160x3840",
        "4:3": "3200x2400",
        "3:4": "2400x3200",
        "21:9": "3840x1600",
    },
}

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
    model_map: dict[str, str]
    label: str = ""
    timeout_seconds: int = 300
    send_reference_images_as_files: bool = True
    concurrency: int = 0


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
    openai_providers: dict[str, OpenAICompatibleConfig]
    custom_model_schemas: dict
    cors_origins: set[str]
    data_dir: Path
    log_dir: Path
    poll_interval_seconds: float
    max_poll_seconds: float
    max_upload_bytes: int
    upload_retry_attempts: int
    upload_retry_delay_seconds: float
    imgbb_api_key: str
    imgbb_timeout_seconds: int
    image_cache_dir: Path
    input_image_dir: Path
    result_image_dir: Path
    image_cache_max_bytes: int
    image_cache_ttl_seconds: float
    image_cache_total_bytes: int
    worker_threads: int
    debug_requests: bool
    log_level: str
    log_max_bytes: int
    log_backup_count: int

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
        for provider in self.openai_providers.values():
            providers.update({model: provider.id for model in provider.models})
        return providers

    def openai_provider_for_model(self, model: str) -> OpenAICompatibleConfig | None:
        provider_id = self.model_providers.get(model, "")
        return self.openai_providers.get(provider_id)

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


def openai_provider_env_prefix(provider_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider_id.strip()).strip("_").upper()
    if not normalized:
        raise SystemExit("OPENAI_COMPAT_PROVIDERS 包含空 provider id")
    return f"OPENAI_COMPAT_{normalized}"


def env_path(name: str, default: Path | str) -> Path:
    raw = os.getenv(name, str(default)).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def parse_openai_model_map(models: tuple[str, ...]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in models:
        if "=" in item:
            alias, upstream_model = item.split("=", 1)
        elif ":" in item:
            alias, upstream_model = item.split(":", 1)
        else:
            alias, upstream_model = item, item
        alias = alias.strip()
        upstream_model = upstream_model.strip()
        if not alias or not upstream_model:
            raise SystemExit("OPENAI_COMPAT_MODELS 的模型映射必须是 alias=upstream_model")
        if alias in mapping:
            raise SystemExit(f"OPENAI_COMPAT_MODELS 模型别名重复: {alias}")
        mapping[alias] = upstream_model
    return mapping


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


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean value")


def default_openai_schema(provider_id: str = "openai_compatible", provider_label: str = "") -> dict:
    sizes = [size for tier in OPENAI_SIZE_PRESETS.values() for size in tier.values()]
    return {
        "aspectRatios": list(next(iter(OPENAI_SIZE_PRESETS.values())).keys()),
        "sizes": sizes,
        "resolutions": list(OPENAI_SIZE_PRESETS.keys()),
        "sizeFromResolution": True,
        "sizeMap": OPENAI_SIZE_PRESETS,
        "qualities": ["auto", "low", "medium", "high"],
        "variants": [1, 2, 3, 4],
        "maxUrls": 10,
        "outputFormats": ["png", "jpeg", "webp"],
        "moderations": ["auto", "low"],
        "provider": provider_id,
        "providerLabel": provider_label or provider_id,
    }


def _build_openai_provider(
    provider_id: str,
    api_key: str,
    base_url: str,
    raw_models: tuple[str, ...],
    label: str,
    timeout_seconds: int,
    send_reference_images_as_files: bool,
    concurrency: int,
) -> OpenAICompatibleConfig:
    provider_id = provider_id.strip()
    if not provider_id:
        raise SystemExit("OpenAI-compatible provider id 不能为空")
    if provider_id == "bizyair":
        raise SystemExit("OPENAI_COMPAT_PROVIDER_ID 不能设置为 bizyair")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", provider_id):
        raise SystemExit(f"OpenAI-compatible provider id 只能包含字母、数字、点、下划线和短横线: {provider_id}")
    if not api_key:
        raise SystemExit(f"{provider_id} 缺少 API Key")
    if not base_url:
        raise SystemExit(f"{provider_id} 缺少 Base URL")
    if not raw_models:
        raise SystemExit(f"{provider_id} 缺少模型列表")
    model_map = parse_openai_model_map(raw_models)
    return OpenAICompatibleConfig(
        id=provider_id,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        models=tuple(model_map.keys()),
        model_map=model_map,
        label=label or provider_id,
        timeout_seconds=timeout_seconds,
        send_reference_images_as_files=send_reference_images_as_files,
        concurrency=concurrency,
    )


def _openai_provider_schemas(provider: OpenAICompatibleConfig) -> dict:
    return {
        model: default_openai_schema(provider.id, provider.label)
        for model in provider.models
    }


def load_openai_compatible() -> tuple[OpenAICompatibleConfig | None, dict]:
    providers, schemas = load_openai_providers()
    first_provider = next(iter(providers.values()), None)
    return first_provider, schemas


def load_openai_providers() -> tuple[dict[str, OpenAICompatibleConfig], dict]:
    provider_ids = tuple(split_env_list("OPENAI_COMPAT_PROVIDERS"))
    if provider_ids:
        providers: dict[str, OpenAICompatibleConfig] = {}
        schemas: dict = {}
        for provider_id in provider_ids:
            prefix = openai_provider_env_prefix(provider_id)
            provider = _build_openai_provider(
                provider_id=provider_id,
                api_key=os.getenv(f"{prefix}_API_KEY", "").strip(),
                base_url=os.getenv(f"{prefix}_BASE_URL", "").strip(),
                raw_models=tuple(split_env_list(f"{prefix}_MODELS")),
                label=os.getenv(f"{prefix}_LABEL", provider_id).strip(),
                timeout_seconds=env_int(f"{prefix}_TIMEOUT_SECONDS", 300, 1),
                send_reference_images_as_files=env_bool(f"{prefix}_SEND_REFERENCE_IMAGES_AS_FILES", True),
                concurrency=env_int(f"{prefix}_CONCURRENCY", 0, 0),
            )
            if provider.id in providers:
                raise SystemExit(f"OpenAI-compatible provider id 重复: {provider.id}")
            for model in provider.models:
                if model in schemas:
                    raise SystemExit(f"OpenAI-compatible 模型别名重复: {model}")
            providers[provider.id] = provider
            schemas.update(_openai_provider_schemas(provider))
        return providers, schemas

    api_key = os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_COMPAT_BASE_URL", "").strip().rstrip("/")
    raw_models = tuple(split_env_list("OPENAI_COMPAT_MODELS"))
    if not any((api_key, base_url, raw_models)):
        return {}, {}
    if not api_key:
        raise SystemExit("配置 OPENAI_COMPAT_BASE_URL/OPENAI_COMPAT_MODELS 时必须同时配置 OPENAI_COMPAT_API_KEY")
    if not base_url:
        raise SystemExit("配置 OPENAI_COMPAT_API_KEY/OPENAI_COMPAT_MODELS 时必须同时配置 OPENAI_COMPAT_BASE_URL")
    if not raw_models:
        raise SystemExit("配置 OPENAI_COMPAT_API_KEY/OPENAI_COMPAT_BASE_URL 时必须同时配置 OPENAI_COMPAT_MODELS")
    provider_id = os.getenv("OPENAI_COMPAT_PROVIDER_ID", "openai_compatible").strip() or "openai_compatible"
    provider = _build_openai_provider(
        provider_id=provider_id,
        api_key=api_key,
        base_url=base_url,
        raw_models=raw_models,
        label=os.getenv("OPENAI_COMPAT_LABEL", "OpenAI Compatible").strip(),
        timeout_seconds=env_int("OPENAI_COMPAT_TIMEOUT_SECONDS", 300, 1),
        send_reference_images_as_files=env_bool("OPENAI_COMPAT_SEND_REFERENCE_IMAGES_AS_FILES", True),
        concurrency=env_int("OPENAI_COMPAT_CONCURRENCY", 0, 0),
    )
    return {provider.id: provider}, _openai_provider_schemas(provider)


def load_bizyair_keys() -> tuple[BizyAirKeyConfig, ...]:
    keys = split_env_list("BIZYAIR_API_KEYS")
    if not keys:
        single_key = os.getenv("BIZYAIR_API_KEY", os.getenv("APIKEY", "")).strip()
        if not single_key:
            if has_openai_provider_config():
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


def has_openai_provider_config() -> bool:
    return bool(
        split_env_list("OPENAI_COMPAT_PROVIDERS")
        or os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
        or os.getenv("OPENAI_COMPAT_BASE_URL", "").strip()
        or split_env_list("OPENAI_COMPAT_MODELS")
    )


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
    data_dir = env_path("DATA_DIR", PROJECT_ROOT / "data")
    log_dir = env_path("LOG_DIR", PROJECT_ROOT / "logs")
    image_cache_dir = env_path("IMAGE_CACHE_DIR", Path(tempfile.gettempdir()) / "bizyair-lan-image-cache")
    input_image_dir = env_path("INPUT_IMAGE_DIR", data_dir / "input-images")
    result_image_dir = env_path("RESULT_IMAGE_DIR", data_dir / "result-images")
    openai_providers, openai_model_schemas = load_openai_providers()
    openai_compatible = next(iter(openai_providers.values()), None)
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
        openai_providers=openai_providers,
        custom_model_schemas=custom_model_schemas,
        cors_origins=cors_origins,
        data_dir=data_dir,
        log_dir=log_dir,
        poll_interval_seconds=env_float("POLL_INTERVAL_SECONDS", 5, 0.5),
        max_poll_seconds=env_float("MAX_POLL_SECONDS", 1800, 30),
        max_upload_bytes=min(env_int("MAX_UPLOAD_MB", 20, 1) * 1024 * 1024, MAX_UPLOAD_BYTES),
        upload_retry_attempts=env_int("UPLOAD_RETRY_ATTEMPTS", 2, 0),
        upload_retry_delay_seconds=env_float("UPLOAD_RETRY_DELAY_SECONDS", 1, 0),
        imgbb_api_key=os.getenv("IMGBB_API_KEY", "").strip(),
        imgbb_timeout_seconds=env_int("IMGBB_TIMEOUT_SECONDS", 30, 1),
        image_cache_dir=image_cache_dir,
        input_image_dir=input_image_dir,
        result_image_dir=result_image_dir,
        image_cache_max_bytes=env_int("IMAGE_CACHE_MAX_MB", 50, 1) * 1024 * 1024,
        image_cache_ttl_seconds=env_float("IMAGE_CACHE_TTL_HOURS", 168, 1) * 3600,
        image_cache_total_bytes=env_int("IMAGE_CACHE_TOTAL_MB", 2048, 1) * 1024 * 1024,
        worker_threads=env_int("WORKER_THREADS", 32, 1),
        debug_requests=env_bool("DEBUG_REQUESTS", False),
        log_level=os.getenv("LOG_LEVEL", "DEBUG" if env_bool("DEBUG_REQUESTS", False) else "INFO").strip().upper() or "INFO",
        log_max_bytes=env_int("LOG_MAX_MB", 5, 1) * 1024 * 1024,
        log_backup_count=env_int("LOG_BACKUP_COUNT", 5, 1),
    )
