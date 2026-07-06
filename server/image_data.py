import base64
import binascii
import re
from urllib.parse import unquote_to_bytes

from .schemas import ALLOWED_CACHE_IMAGE_TYPES


DATA_IMAGE_PREFIX = "data:image/"


def is_image_data_url(value: str) -> bool:
    return isinstance(value, str) and value.lower().startswith(DATA_IMAGE_PREFIX)


def parse_image_data_url(value: str, max_bytes: int) -> tuple[bytes, str, str]:
    if not is_image_data_url(value):
        raise ValueError("图片 data URL 格式无效")
    header, separator, payload = value.partition(",")
    if not separator:
        raise ValueError("图片 data URL 缺少数据")
    match = re.fullmatch(r"data:([^;,]+)((?:;[^;,=]+|;[^;,=]+=[^;,]*)*)", header, flags=re.IGNORECASE)
    if not match:
        raise ValueError("图片 data URL 头部格式无效")
    content_type = match.group(1).lower()
    if content_type not in ALLOWED_CACHE_IMAGE_TYPES:
        raise ValueError("只允许 jpg、png、webp、gif、avif 图片 data URL")
    params = [item.lower() for item in (match.group(2) or "").split(";") if item]
    try:
        if "base64" in params:
            data = base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
        else:
            data = unquote_to_bytes(payload)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片 data URL 数据无效") from exc
    if not data:
        raise ValueError("图片 data URL 为空")
    if len(data) > max_bytes:
        raise ValueError(f"图片不能超过 {max_bytes // 1024 // 1024} MB")
    return data, content_type, ALLOWED_CACHE_IMAGE_TYPES[content_type]
