import hashlib
import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .schemas import ALLOWED_UPLOAD_EXTENSIONS, is_local_api_url


@dataclass(frozen=True)
class StoredInputImage:
    id: str
    path: Path
    filename: str
    content_type: str
    extension: str
    size: int
    sha256: str


def store_input_image(directory: Path, data: bytes, filename: str, content_type: str, extension: str) -> StoredInputImage:
    directory.mkdir(parents=True, exist_ok=True)
    image_id = uuid.uuid4().hex
    suffix = _normalize_extension(extension, content_type)
    file_path = directory / f"{image_id}{suffix}"
    file_path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    stored = StoredInputImage(
        id=image_id,
        path=file_path,
        filename=filename or f"input-{image_id}{suffix}",
        content_type=content_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream",
        extension=suffix,
        size=len(data),
        sha256=sha256,
    )
    _metadata_path(directory, image_id).write_text(
        json.dumps(
            {
                "id": stored.id,
                "filename": stored.filename,
                "content_type": stored.content_type,
                "extension": stored.extension,
                "size": stored.size,
                "sha256": stored.sha256,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return stored


def load_input_image(directory: Path, image_id: str) -> StoredInputImage:
    if not re.fullmatch(r"[a-f0-9]{32}", image_id or ""):
        raise KeyError("input image not found")
    metadata_path = _metadata_path(directory, image_id)
    if not metadata_path.is_file():
        raise KeyError("input image not found")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    extension = str(metadata.get("extension") or "").lower()
    file_path = (directory / f"{image_id}{extension}").resolve()
    root = directory.resolve()
    if root != file_path.parent or not file_path.is_file():
        raise KeyError("input image not found")
    return StoredInputImage(
        id=image_id,
        path=file_path,
        filename=str(metadata.get("filename") or f"input-{image_id}{extension}"),
        content_type=str(metadata.get("content_type") or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"),
        extension=extension,
        size=int(metadata.get("size") or file_path.stat().st_size),
        sha256=str(metadata.get("sha256") or ""),
    )


def input_image_id_from_url(url: str) -> str:
    if not is_local_api_url(url):
        return ""
    path = urlparse(url).path if url.startswith(("http://", "https://")) else url
    match = re.fullmatch(r"/api/input-images/([a-f0-9]{32})(?:/file)?/?", path or "")
    return match.group(1) if match else ""


def _metadata_path(directory: Path, image_id: str) -> Path:
    return directory / f"{image_id}.json"


def _normalize_extension(extension: str, content_type: str) -> str:
    suffix = (extension or "").lower()
    if suffix in ALLOWED_UPLOAD_EXTENSIONS:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed and guessed.lower() in ALLOWED_UPLOAD_EXTENSIONS:
        return guessed.lower()
    return ".png"
