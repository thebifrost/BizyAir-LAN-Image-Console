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
            variants = int(params.get("variants"))
        except (TypeError, ValueError):
            variants = None
        if variants in schema["variants"]:
            clean["variants"] = variants
            if variants == 4 and schema.get("provider", "bizyair") == "bizyair":
                clean["provider"] = "KieAI"
    if model.startswith("gemini"):
        for key in ("temperature", "top_p"):
            if key in params:
                value = float(params[key])
                if 0 <= value <= float(schema.get("temperature", {}).get("max", 1) if key == "temperature" else schema.get("topP", 1)):
                    clean[key] = value
        for key in ("seed", "max_tokens"):
            if key in params:
                value = int(params[key])
                if value >= 0:
                    clean[key] = value
    if schema.get("provider", "bizyair") != "bizyair":
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
        for key in ("size", "style", "background", "seed", "temperature", "top_p"):
            if key in params and params[key] not in (None, ""):
                clean[key] = params[key]
    urls = params.get("urls")
    if isinstance(urls, list) and schema.get("maxUrls", 0):
        max_urls = min(int(schema.get("maxUrls") or 0), MAX_INPUT_IMAGES)
        clean_urls = [url for url in urls if isinstance(url, str) and url.startswith(("http://", "https://"))]
        if len(clean_urls) > MAX_INPUT_IMAGES:
            raise ValueError(f"输入图片不能超过 {MAX_INPUT_IMAGES} 张")
        clean["urls"] = clean_urls[:max_urls]
    return clean
