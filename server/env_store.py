import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, PROJECT_ROOT, openai_provider_env_prefix


@dataclass(frozen=True)
class EnvField:
    name: str
    label: str
    section: str
    type: str = "text"
    sensitive: bool = False
    default: str = ""


ENV_FIELDS = [
    EnvField("APP_HOST", "监听地址", "基础", default="127.0.0.1"),
    EnvField("APP_PORT", "端口", "基础", type="number", default="8787"),
    EnvField("ADMIN_TOKEN", "访问口令", "基础", sensitive=True),
    EnvField("CORS_ORIGINS", "允许跨域来源", "基础"),
    EnvField("WORKER_THREADS", "工作线程", "基础", type="number", default="32"),
    EnvField("DEBUG_REQUESTS", "调试请求日志", "日志", type="boolean", default="false"),
    EnvField("LOG_LEVEL", "日志级别", "日志", type="select", default="INFO"),
    EnvField("LOG_MAX_MB", "单个日志 MB", "日志", type="number", default="5"),
    EnvField("LOG_BACKUP_COUNT", "日志保留份数", "日志", type="number", default="5"),
    EnvField("DATA_DIR", "数据目录", "存储", default="./data"),
    EnvField("LOG_DIR", "日志目录", "存储", default="./logs"),
    EnvField("MAX_UPLOAD_MB", "上传上限 MB", "上传", type="number", default="20"),
    EnvField("UPLOAD_RETRY_ATTEMPTS", "上传重试次数", "上传", type="number", default="2"),
    EnvField("UPLOAD_RETRY_DELAY_SECONDS", "上传重试间隔秒", "上传", type="number", default="1"),
    EnvField("IMGBB_API_KEY", "ImgBB API Key", "上传", sensitive=True),
    EnvField("IMGBB_TIMEOUT_SECONDS", "ImgBB 超时秒", "上传", type="number", default="30"),
    EnvField("BIZYAIR_API_KEY", "BizyAir 单 Key", "BizyAir", sensitive=True),
    EnvField("BIZYAIR_API_KEYS", "BizyAir 多 Key", "BizyAir", sensitive=True),
    EnvField("BIZYAIR_KEY_LABELS", "BizyAir Key 标签", "BizyAir"),
    EnvField("BIZYAIR_BASE_URL", "BizyAir Base URL", "BizyAir", default="https://api.bizyair.cn/x/v1"),
    EnvField("POLL_INTERVAL_SECONDS", "轮询间隔秒", "任务", type="number", default="5"),
    EnvField("MAX_POLL_SECONDS", "任务超时秒", "任务", type="number", default="1800"),
    EnvField("IMAGE_CACHE_MAX_MB", "单图缓存 MB", "缓存", type="number", default="50"),
    EnvField("IMAGE_CACHE_TOTAL_MB", "总缓存 MB", "缓存", type="number", default="2048"),
    EnvField("IMAGE_CACHE_TTL_HOURS", "缓存 TTL 小时", "缓存", type="number", default="168"),
]

FIELD_MAP = {field.name: field for field in ENV_FIELDS}
FIELD_MINIMUMS = {
    "APP_PORT": 1,
    "WORKER_THREADS": 1,
    "LOG_MAX_MB": 1,
    "LOG_BACKUP_COUNT": 1,
    "MAX_UPLOAD_MB": 1,
    "UPLOAD_RETRY_ATTEMPTS": 0,
    "UPLOAD_RETRY_DELAY_SECONDS": 0,
    "IMGBB_TIMEOUT_SECONDS": 1,
    "POLL_INTERVAL_SECONDS": 0.5,
    "MAX_POLL_SECONDS": 30,
    "IMAGE_CACHE_MAX_MB": 1,
    "IMAGE_CACHE_TOTAL_MB": 1,
    "IMAGE_CACHE_TTL_HOURS": 1,
}
FLOAT_FIELDS = {"UPLOAD_RETRY_DELAY_SECONDS", "POLL_INTERVAL_SECONDS", "MAX_POLL_SECONDS", "IMAGE_CACHE_TTL_HOURS"}
PROVIDER_SUFFIXES = (
    "API_KEY",
    "BASE_URL",
    "MODELS",
    "LABEL",
    "TIMEOUT_SECONDS",
    "SEND_REFERENCE_IMAGES_AS_FILES",
    "CONCURRENCY",
)
LEGACY_PROVIDER_KEYS = (
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_MODELS",
    "OPENAI_COMPAT_PROVIDER_ID",
    "OPENAI_COMPAT_LABEL",
    "OPENAI_COMPAT_TIMEOUT_SECONDS",
    "OPENAI_COMPAT_SEND_REFERENCE_IMAGES_AS_FILES",
    "OPENAI_COMPAT_CONCURRENCY",
)


def public_env_config(config: AppConfig) -> dict:
    fields = []
    for field in ENV_FIELDS:
        raw = os.getenv(field.name, "")
        fields.append(
            {
                "name": field.name,
                "label": field.label,
                "section": field.section,
                "type": field.type,
                "sensitive": field.sensitive,
                "configured": bool(raw),
                "value": "" if field.sensitive else raw,
                "default": field.default,
            }
        )
    return {
        "env_path": str(PROJECT_ROOT / ".env"),
        "fields": fields,
        "providers": [public_provider(provider) for provider in config.openai_providers.values()],
        "log_dir": str(config.log_dir),
        "restart_required_after_save": True,
    }


def public_provider(provider) -> dict:
    return {
        "id": provider.id,
        "label": provider.label or provider.id,
        "base_url": provider.base_url,
        "models": ",".join(
            f"{alias}={provider.model_map.get(alias, alias)}"
            if provider.model_map.get(alias, alias) != alias else alias
            for alias in provider.models
        ),
        "timeout_seconds": provider.timeout_seconds,
        "send_reference_images_as_files": provider.send_reference_images_as_files,
        "concurrency": provider.concurrency,
        "api_key_configured": bool(provider.api_key),
    }


def save_env_config(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("配置请求必须是 JSON 对象")
    updates: dict[str, str] = {}
    removals: set[str] = set()
    apply_field_updates(payload.get("fields") or {}, updates, removals)
    if "providers" in payload:
        apply_provider_updates(payload.get("providers"), updates, removals)
    backup_path = write_env_values(updates, removals)
    for name in removals:
        os.environ.pop(name, None)
    for name, value in updates.items():
        os.environ[name] = value
    return {
        "updated": sorted(updates),
        "removed": sorted(removals),
        "backup_path": str(backup_path) if backup_path else "",
        "restart_required": True,
    }


def apply_field_updates(fields: dict, updates: dict[str, str], removals: set[str]) -> None:
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    for name, value in fields.items():
        if name not in FIELD_MAP:
            raise ValueError(f"不允许修改配置项: {name}")
        field = FIELD_MAP[name]
        text = "" if value is None else str(value).strip()
        if field.sensitive and not text:
            continue
        if not text:
            removals.add(name)
            updates.pop(name, None)
            continue
        updates[name] = normalize_env_value(name, value)


def apply_provider_updates(providers, updates: dict[str, str], removals: set[str]) -> None:
    if not isinstance(providers, list):
        raise ValueError("providers 必须是数组")
    if len(providers) > 20:
        raise ValueError("第三方 provider 不能超过 20 个")
    removals.update(LEGACY_PROVIDER_KEYS)
    provider_ids: list[str] = []
    seen: set[str] = set()
    old_ids = [provider_id for provider_id in os.getenv("OPENAI_COMPAT_PROVIDERS", "").split(",") if provider_id.strip()]
    for item in providers:
        if not isinstance(item, dict):
            raise ValueError("provider 配置必须是对象")
        provider_id = str(item.get("id") or "").strip()
        if not provider_id:
            continue
        if provider_id == "bizyair" or not re.fullmatch(r"[A-Za-z0-9_.-]+", provider_id):
            raise ValueError(f"provider id 无效: {provider_id}")
        if provider_id in seen:
            raise ValueError(f"provider id 重复: {provider_id}")
        seen.add(provider_id)
        prefix = openai_provider_env_prefix(provider_id)
        api_key = str(item.get("api_key") or "").strip()
        existing_key = existing_provider_api_key(provider_id, prefix)
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        models = str(item.get("models") or "").strip()
        if not api_key and not existing_key:
            raise ValueError(f"{provider_id} 缺少 API Key")
        if not base_url:
            raise ValueError(f"{provider_id} 缺少 Base URL")
        if not models:
            raise ValueError(f"{provider_id} 缺少模型列表")
        provider_ids.append(provider_id)
        updates[f"{prefix}_LABEL"] = str(item.get("label") or provider_id).strip() or provider_id
        updates[f"{prefix}_BASE_URL"] = base_url
        updates[f"{prefix}_MODELS"] = models
        updates[f"{prefix}_TIMEOUT_SECONDS"] = normalize_int(item.get("timeout_seconds", 300), 1, f"{provider_id} timeout")
        updates[f"{prefix}_SEND_REFERENCE_IMAGES_AS_FILES"] = "true" if item.get("send_reference_images_as_files") is not False else "false"
        updates[f"{prefix}_CONCURRENCY"] = normalize_int(item.get("concurrency", 0), 0, f"{provider_id} concurrency")
        if api_key:
            updates[f"{prefix}_API_KEY"] = api_key
        elif existing_key and not os.getenv(f"{prefix}_API_KEY", "").strip():
            updates[f"{prefix}_API_KEY"] = existing_key
    updates["OPENAI_COMPAT_PROVIDERS"] = ",".join(provider_ids)
    removed_ids = set(old_ids) - set(provider_ids)
    for provider_id in removed_ids:
        prefix = openai_provider_env_prefix(provider_id)
        removals.update(f"{prefix}_{suffix}" for suffix in PROVIDER_SUFFIXES)


def normalize_env_value(name: str, value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if "\n" in text or "\r" in text:
        raise ValueError(f"{name} 不能包含换行")
    field = FIELD_MAP[name]
    if field.type == "number":
        minimum = FIELD_MINIMUMS.get(name, 0)
        if name in FLOAT_FIELDS:
            return normalize_float(text, float(minimum), name)
        return normalize_int(text, int(minimum), name)
    if field.type == "boolean":
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on"} or value is True:
            return "true"
        if raw in {"0", "false", "no", "off"} or value is False:
            return "false"
        raise ValueError(f"{name} 必须是布尔值")
    if field.type == "select" and name == "LOG_LEVEL":
        level = text.upper() or "INFO"
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("LOG_LEVEL 只能是 DEBUG、INFO、WARNING 或 ERROR")
        return level
    return text


def existing_provider_api_key(provider_id: str, prefix: str) -> str:
    provider_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    if provider_key:
        return provider_key
    legacy_id = os.getenv("OPENAI_COMPAT_PROVIDER_ID", "openai_compatible").strip() or "openai_compatible"
    if provider_id == legacy_id:
        return os.getenv("OPENAI_COMPAT_API_KEY", "").strip()
    return ""


def normalize_int(value, minimum: int, label: str) -> str:
    try:
        number = int(str(value).strip() or "0")
    except ValueError as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    if number < minimum:
        raise ValueError(f"{label} 不能小于 {minimum}")
    return str(number)


def normalize_float(value, minimum: float, label: str) -> str:
    try:
        number = float(str(value).strip() or "0")
    except ValueError as exc:
        raise ValueError(f"{label} 必须是数字") from exc
    if number < minimum:
        raise ValueError(f"{label} 不能小于 {minimum}")
    return str(number).rstrip("0").rstrip(".") if "." in str(number) else str(number)


def write_env_values(updates: dict[str, str], removals: set[str]) -> Path | None:
    env_path = PROJECT_ROOT / ".env"
    backup_path = backup_env_file(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    updated: set[str] = set()
    output: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(raw_line)
            continue
        key, _old_value = stripped.split("=", 1)
        key = key.strip()
        if key in removals:
            continue
        if key in updates:
            if key not in updated:
                output.append(f"{key}={updates[key]}")
                updated.add(key)
            continue
        output.append(raw_line)
    for key, value in updates.items():
        if key not in updated:
            output.append(f"{key}={value}")
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return backup_path


def backup_env_file(env_path: Path) -> Path | None:
    if not env_path.is_file():
        return None
    backup_dir = env_path.parent / ".temp" / "env-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ".env.bak"
    shutil.copy2(env_path, backup_path)
    return backup_path
