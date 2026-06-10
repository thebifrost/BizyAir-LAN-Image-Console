import logging
import re
from logging.handlers import RotatingFileHandler

from .config import AppConfig

def configure_logging(config: AppConfig) -> tuple[logging.Logger, logging.Logger]:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    app_logger = logging.getLogger("bizyair_lan")
    app_logger.setLevel(logging.DEBUG if config.debug_requests else logging.INFO)
    app_logger.handlers.clear()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(config.log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
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
        if config.openai_compatible and config.openai_compatible.api_key:
            text = text.replace(config.openai_compatible.api_key, "[REDACTED_OPENAI_COMPAT_KEY]")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9]+", "sk-[REDACTED]", text)
    return text[:1200]
