import logging
import json
import re
from logging.handlers import RotatingFileHandler

from .config import AppConfig

def configure_logging(config: AppConfig) -> tuple[logging.Logger, logging.Logger]:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    app_logger = logging.getLogger("bizyair_lan")
    app_logger.setLevel(getattr(logging, config.log_level, logging.INFO))
    app_logger.handlers.clear()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(config.log_dir / "app.log", maxBytes=config.log_max_bytes, backupCount=config.log_backup_count, encoding="utf-8")
    file_handler.setFormatter(formatter)
    app_logger.addHandler(stream_handler)
    app_logger.addHandler(file_handler)
    audit_logger = logging.getLogger("bizyair_audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.handlers.clear()
    audit_handler = RotatingFileHandler(config.log_dir / "audit.log", maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8")
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(audit_handler)
    return app_logger, audit_logger


def redact(value: object, config: AppConfig | None = None) -> str:
    text = str(value)
    if config:
        for key_config in config.bizyair_keys:
            if key_config.api_key:
                text = text.replace(key_config.api_key, "[REDACTED_BIZYAIR_KEY]")
        for provider in getattr(config, "openai_providers", {}).values():
            if provider.api_key:
                text = text.replace(provider.api_key, f"[REDACTED_OPENAI_KEY:{provider.id}]")
        if config.imgbb_api_key:
            text = text.replace(config.imgbb_api_key, "[REDACTED_IMGBB_KEY]")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9]+", "sk-[REDACTED]", text)
    return text[:1200]


def log_event(logger: logging.Logger, level: int, event: str, config: AppConfig | None = None, **fields) -> None:
    safe_fields = {}
    for key, value in fields.items():
        safe_fields[key] = redact(value, config) if isinstance(value, (dict, list, tuple, Exception)) else value
    logger.log(level, json.dumps({"event": event, **safe_fields}, ensure_ascii=False, default=str))
