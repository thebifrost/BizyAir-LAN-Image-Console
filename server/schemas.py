import ipaddress
from urllib.parse import urlparse

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
LOCAL_API_PREFIXES = ("/api/input-images/", "/api/images/")

MODEL_SCHEMAS = {
    "gpt-image-1": {
        "aspectRatios": ["1:1", "2:3", "3:2"],
        "resolutions": [],
        "qualities": [],
        "variants": [1, 2, 4],
        "maxUrls": 99,
    },
    "gpt-image-2": {
        "aspectRatios": ["1:1", "2:3", "3:2", "4:5", "5:4", "3:4", "4:3", "16:9", "9:16", "21:9"],
        "resolutions": ["1k", "2k", "4k"],
        "qualities": [],
        "variants": [],
        "maxUrls": 99,
    },
    "gpt-image-2-official": {
        "aspectRatios": ["1:1", "1:3", "3:1", "2:3", "3:2", "4:5", "5:4", "3:4", "4:3", "16:9", "9:16", "21:9"],
        "resolutions": ["1k", "2k", "4k"],
        "qualities": ["low", "medium", "high"],
        "variants": [],
        "maxUrls": 99,
    },
    "gemini-2.5-flash-image": {
        "aspectRatios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        "resolutions": [],
        "qualities": [],
        "variants": [],
        "maxUrls": 5,
        "temperature": {"max": 1},
        "topP": 1,
        "maxTokens": 8192,
    },
    "gemini-3-pro-image-preview": {
        "aspectRatios": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "qualities": [],
        "variants": [],
        "maxUrls": 14,
    },
    "gemini-3-pro-image-preview-official": {
        "aspectRatios": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "qualities": [],
        "variants": [],
        "maxUrls": 14,
    },
    "gemini-3.1-flash-image-preview": {
        "aspectRatios": ["1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "qualities": [],
        "variants": [],
        "maxUrls": 14,
    },
    "gemini-3.1-flash-image-preview-official": {
        "aspectRatios": ["1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "qualities": [],
        "variants": [],
        "maxUrls": 14,
    },
}


def get_model_schemas(config=None) -> dict:
    if not config:
        return MODEL_SCHEMAS
    custom_schemas = getattr(config, "custom_model_schemas", {})
    if not getattr(config, "bizyair_keys", ()):
        return {
            model: schema
            for model, schema in custom_schemas.items()
            if isinstance(schema, dict) and schema.get("provider", "bizyair") != "bizyair"
        }
    return {**MODEL_SCHEMAS, **custom_schemas}


def validate_params(model: str, params: dict, schemas: dict | None = None) -> dict:
    schema = (schemas or MODEL_SCHEMAS)[model]
    clean: dict = {}
    if schema.get("aspectRatios"):
        aspect_ratio = params.get("aspect_ratio")
        if aspect_ratio in schema["aspectRatios"]:
            clean["aspect_ratio"] = aspect_ratio
    if schema.get("resolutions"):
        resolution = params.get("resolution")
        if resolution in schema["resolutions"]:
            clean["resolution"] = resolution
    if schema.get("qualities"):
        quality = params.get("quality")
        if quality in schema["qualities"]:
            clean["quality"] = quality
    if schema.get("variants"):
        try:
            variants = int(params.get("variants", 1))
        except (TypeError, ValueError):
            variants = None
        if variants in schema["variants"]:
            clean["variants"] = variants
            if variants == 4 and schema.get("provider", "bizyair") == "bizyair":
                clean["provider"] = "KieAI"
    supports_seed = schema.get("seed") is True or model.startswith("gemini")
    if model.startswith("gemini"):
        for key in ("temperature", "top_p"):
            if key in params:
                value = float(params[key])
                if 0 <= value <= float(schema.get("temperature", {}).get("max", 1) if key == "temperature" else schema.get("topP", 1)):
                    clean[key] = value
        for key in ("max_tokens",):
            if key in params:
                value = int(params[key])
                if value >= 0:
                    clean[key] = value
    if supports_seed and "seed" in params:
        try:
            value = int(params["seed"])
        except (TypeError, ValueError):
            value = -1
        if value >= 0:
            clean["seed"] = value
    if schema.get("provider", "bizyair") != "bizyair":
        if schema.get("sizes"):
            size = params.get("size")
            if size in schema["sizes"]:
                clean["size"] = size
        output_format = params.get("output_format")
        if output_format in schema.get("outputFormats", ["png", "jpeg", "webp"]):
            clean["output_format"] = output_format
        moderation = params.get("moderation")
        if moderation in schema.get("moderations", ["auto", "low"]):
            clean["moderation"] = moderation
        if "output_compression" in params and output_format and output_format != "png":
            try:
                value = int(params["output_compression"])
            except (TypeError, ValueError):
                value = -1
            if 0 <= value <= 100:
                clean["output_compression"] = value
        for key in ("style", "background", "temperature", "top_p"):
            if key in params and params[key] not in (None, ""):
                clean[key] = params[key]
        if "send_reference_images_as_files" in params:
            clean["send_reference_images_as_files"] = params["send_reference_images_as_files"] is not False
    urls = params.get("urls")
    if isinstance(urls, list) and schema.get("maxUrls", 0):
        max_urls = min(int(schema.get("maxUrls") or 0), MAX_INPUT_IMAGES)
        provider = schema.get("provider", "bizyair")
        clean_urls = [url for url in urls if isinstance(url, str) and is_allowed_input_url(url, provider)]
        if len(clean_urls) > MAX_INPUT_IMAGES:
            raise ValueError(f"输入图片不能超过 {MAX_INPUT_IMAGES} 张")
        clean["urls"] = clean_urls[:max_urls]
    return clean


def is_allowed_input_url(url: str, provider: str) -> bool:
    if is_local_api_url(url) or url.lower().startswith("data:image/"):
        return provider != "bizyair"
    return url.startswith(("http://", "https://"))


def is_local_api_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url) if url.startswith(("http://", "https://")) else None
    path = parsed.path if parsed else url
    if not path.startswith(LOCAL_API_PREFIXES):
        return False
    if not parsed:
        return True
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private
