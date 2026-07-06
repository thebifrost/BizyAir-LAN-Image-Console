from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

from .env_store import public_env_config
from .schemas import get_model_schemas

if TYPE_CHECKING:
    from urllib.parse import ParseResult

    from .handlers import LanGatewayHandler


HandlerAction = Callable[[], None]


def dispatch_get(handler: LanGatewayHandler, parsed: ParseResult) -> None:
    path = parsed.path
    exact_routes: dict[str, tuple[str | None, HandlerAction]] = {
        "/health": (None, handler._handle_health),
        "/api/config": ("config", lambda: handler._send_json({"status": True, "data": handler._public_config()})),
        "/api/models": ("models", lambda: handler._send_json({"status": True, "data": get_model_schemas(handler.server.config)})),
        "/v1/models": ("openai:models", handler._handle_openai_models),
        "/api/inputs": ("inputs", lambda: handler._handle_inputs(parsed)),
        "/api/account": ("account", handler._handle_account),
        "/api/admin/runtime": ("admin:runtime", handler._handle_admin_runtime),
        "/api/admin/env": ("admin:env:get", lambda: handler._send_json({"status": True, "data": public_env_config(handler.server.config)})),
        "/api/logs": ("logs:get", lambda: handler._handle_logs(parsed)),
        "/api/image-cache": (None, lambda: handler._handle_image_cache(parsed)),
        "/api/jobs": ("jobs:list", lambda: handler._send_json({"status": True, "data": handler.server.db.list_jobs()})),
    }
    route = exact_routes.get(path)
    if route:
        _run(handler, *route)
        return
    if path.startswith("/api/input-images/"):
        handler._handle_input_image(path)
        return
    if path.startswith("/api/images/"):
        handler._handle_local_image(path)
        return
    if path.startswith("/api/jobs/"):
        _run(handler, "jobs:get", lambda: _handle_get_job(handler, path))
        return
    if path.startswith("/api/"):
        _not_found(handler)
        return
    handler._serve_static(path)


def dispatch_post(handler: LanGatewayHandler, parsed: ParseResult) -> None:
    path = parsed.path
    exact_routes: dict[str, tuple[str, HandlerAction]] = {
        "/api/upload": ("upload", handler._handle_upload),
        "/v1/chat/completions": ("openai:chat_completions", handler._handle_openai_chat_completions),
        "/v1/images/generations": ("openai:images_generations", handler._handle_openai_images_generations),
        "/v1/images/edits": ("openai:images_edits", handler._handle_openai_images_edits),
        "/api/admin/config": ("admin:config", handler._handle_admin_config),
        "/api/admin/env": ("admin:env:set", handler._handle_admin_env),
        "/api/admin/restart": ("admin:restart", handler._handle_admin_restart),
        "/api/jobs": ("jobs:create", handler._handle_create_job),
    }
    route = exact_routes.get(path)
    if route:
        _run(handler, *route)
        return
    if path.startswith("/api/jobs/") and path.endswith("/cancel"):
        _run(handler, "jobs:cancel", lambda: _handle_cancel_job(handler, path))
        return
    if path.startswith("/api/jobs/") and path.endswith("/retry"):
        _run(handler, "jobs:create", lambda: _handle_retry_job(handler, path))
        return
    _not_found(handler)


def dispatch_delete(handler: LanGatewayHandler, parsed: ParseResult) -> None:
    path = parsed.path
    if path.startswith("/api/images/"):
        _run(handler, "images:delete", lambda: _handle_delete_image(handler, path))
        return
    _not_found(handler)


def _run(handler: LanGatewayHandler, auth_action: str | None, action: HandlerAction) -> None:
    if auth_action:
        handler._require_auth(auth_action)
    action()


def _handle_get_job(handler: LanGatewayHandler, path: str) -> None:
    job_id = path.removeprefix("/api/jobs/").strip("/")
    if "/" in job_id or not job_id:
        _not_found(handler)
        return
    handler._send_json({"status": True, "data": handler.server.db.get_job(job_id)})


def _handle_cancel_job(handler: LanGatewayHandler, path: str) -> None:
    job_id = path.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/")
    job = handler.server.db.cancel_job(job_id)
    handler._audit("jobs:cancel", "ok", {"job_id": job_id})
    handler._send_json({"status": True, "data": job})


def _handle_retry_job(handler: LanGatewayHandler, path: str) -> None:
    job_id = path.removeprefix("/api/jobs/").removesuffix("/retry").strip("/")
    job = handler.server.db.retry_job(job_id)
    handler.server.runner.enqueue_job(job)
    handler._audit("jobs:retry", "ok", {"job_id": job_id})
    handler._send_json({"status": True, "data": job})


def _handle_delete_image(handler: LanGatewayHandler, path: str) -> None:
    image_id = path.removeprefix("/api/images/").strip("/")
    if "/" in image_id or not re.fullmatch(r"[a-f0-9]{32}", image_id):
        _not_found(handler)
        return
    image = handler.server.db.delete_job_image(image_id)
    handler._delete_local_image_file(image)
    handler._audit("images:delete", "ok", {"image_id": image_id})
    handler._send_json({"status": True, "data": {"id": image_id}})


def _not_found(handler: LanGatewayHandler) -> None:
    handler._send_json({"status": False, "message": "Not found"}, 404)
