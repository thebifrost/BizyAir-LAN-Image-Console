import contextlib
import hashlib
import ipaddress
import json
import logging
import mimetypes
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .config import AppConfig
from .schemas import ALLOWED_CACHE_IMAGE_TYPES

class ImageCache:
    def __init__(self, config: AppConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.cache_dir = config.image_cache_dir
        self.result_dir = config.result_image_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.locks: dict[str, threading.Lock] = {}
        self.locks_guard = threading.Lock()
        self.last_cleanup = 0.0
        self.cleanup()

    def get(self, url: str) -> tuple[Path, str]:
        self._validate_url(url)
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        with self._lock_for(key):
            cached = self._cached_entry(key)
            if cached:
                path, content_type = cached
                self._touch_meta(key)
                return path, content_type
            path, content_type = self._download(url, key)
            self._cleanup_periodically()
            return path, content_type

    def archive_result_image(self, url: str, image_id: str) -> dict:
        self._validate_url(url)
        with self._lock_for(f"result-{image_id}"):
            path, content_type, extension, size, digest = self._download_to_directory(url, image_id, self.result_dir)
            return {"local_path": str(path), "content_type": content_type, "extension": extension, "size": size, "sha256": digest, "original_url": url}

    @contextlib.contextmanager
    def _lock_for(self, key: str):
        with self.locks_guard:
            lock = self.locks.setdefault(key, threading.Lock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("图片缓存只支持 http/https URL")
        if parsed.username or parsed.password:
            raise ValueError("图片 URL 不能包含用户名或密码")
        if parsed.port and parsed.port not in {80, 443}:
            raise ValueError("图片 URL 端口不允许")
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("图片 URL 指向的地址不允许缓存")
        return url

    def _cached_entry(self, key: str) -> tuple[Path, str] | None:
        meta_path = self._meta_path(key)
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        path = self._safe_cache_path(key, meta.get("extension") or ALLOWED_CACHE_IMAGE_TYPES.get(meta.get("content_type"), ".img"))
        if not path.is_file() or time.time() - float(meta.get("created_at") or 0) > self.config.image_cache_ttl_seconds:
            self._delete_entry(key)
            return None
        content_type = str(meta.get("content_type") or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        return path, content_type

    def _download(self, url: str, key: str) -> tuple[Path, str]:
        final_path, content_type, extension, size, _ = self._download_to_directory(url, key, self.cache_dir)
        self._write_meta(key, url, content_type, extension, size)
        return final_path, content_type

    def _download_to_directory(self, url: str, stem: str, directory: Path) -> tuple[Path, str, str, int, str]:
        current_url = url
        response = None
        for _ in range(4):
            self._validate_url(current_url)
            response = requests.get(current_url, stream=True, timeout=(5, 30), allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise ValueError("图片重定向缺少 Location")
                current_url = urljoin(current_url, location)
                continue
            break
        if response is None:
            raise ValueError("图片下载失败")
        try:
            if response.status_code != 200:
                raise ValueError(f"图片下载失败：HTTP {response.status_code}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in ALLOWED_CACHE_IMAGE_TYPES:
                raise ValueError("只允许缓存 jpg、png、webp、gif、avif 图片")
            length = int(response.headers.get("Content-Length") or "0")
            if length > self.config.image_cache_max_bytes:
                raise ValueError(f"图片不能超过 {self.config.image_cache_max_bytes // 1024 // 1024} MB")
            extension = ALLOWED_CACHE_IMAGE_TYPES[content_type]
            final_path = self._safe_image_path(directory, stem, extension)
            temp_path = final_path.with_suffix(final_path.suffix + ".tmp")
            written = 0
            digest = hashlib.sha256()
            with temp_path.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.config.image_cache_max_bytes:
                        raise ValueError(f"图片不能超过 {self.config.image_cache_max_bytes // 1024 // 1024} MB")
                    digest.update(chunk)
                    fh.write(chunk)
            temp_path.replace(final_path)
            return final_path, content_type, extension, written, digest.hexdigest()
        finally:
            response.close()
            with contextlib.suppress(FileNotFoundError):
                if "temp_path" in locals() and temp_path.exists():
                    temp_path.unlink()

    def _safe_cache_path(self, key: str, extension: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("缓存 key 无效")
        return self._safe_image_path(self.cache_dir, key, extension)

    def _safe_image_path(self, directory: Path, stem: str, extension: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32,64}", stem):
            raise ValueError("图片文件名无效")
        extension = extension if re.fullmatch(r"\.[a-z0-9]+", extension or "") else ".img"
        base = directory.resolve()
        path = (directory / f"{stem}{extension}").resolve()
        if base != path.parent:
            raise ValueError("图片路径无效")
        return path

    def _meta_path(self, key: str) -> Path:
        path = (self.cache_dir / f"{key}.json").resolve()
        if self.cache_dir.resolve() != path.parent:
            raise ValueError("缓存元数据路径无效")
        return path

    def _write_meta(self, key: str, url: str, content_type: str, extension: str, size: int) -> None:
        now = time.time()
        meta = {"original_url": url, "content_type": content_type, "extension": extension, "size": size, "created_at": now, "last_access": now}
        self._meta_path(key).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _touch_meta(self, key: str) -> None:
        meta_path = self._meta_path(key)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["last_access"] = time.time()
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    def _delete_entry(self, key: str) -> None:
        for path in self.cache_dir.glob(f"{key}.*"):
            with contextlib.suppress(OSError):
                path.unlink()

    def _cleanup_periodically(self) -> None:
        if time.time() - self.last_cleanup > 300:
            self.cleanup()

    def cleanup(self) -> None:
        self.last_cleanup = time.time()
        entries = []
        total = 0
        for meta_path in self.cache_dir.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                key = meta_path.stem
                path = self._safe_cache_path(key, meta.get("extension") or ALLOWED_CACHE_IMAGE_TYPES.get(meta.get("content_type"), ".img"))
                if not path.is_file() or time.time() - float(meta.get("created_at") or 0) > self.config.image_cache_ttl_seconds:
                    self._delete_entry(key)
                    continue
                size = int(meta.get("size") or path.stat().st_size)
                total += size
                entries.append((float(meta.get("last_access") or 0), key, size))
            except Exception:
                with contextlib.suppress(OSError):
                    meta_path.unlink()
        for _, key, size in sorted(entries):
            if total <= self.config.image_cache_total_bytes:
                break
            self._delete_entry(key)
            total -= size
