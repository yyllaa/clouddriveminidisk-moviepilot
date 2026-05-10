from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from backend.core.plugin_manager import PluginApiResult


_RUNTIME_MODULE_NAME = "clouddrive_plugin_moviepilot_storage_runtime"
_RUNTIME_MODULE: Any | None = None
_RUNTIME_MODULE_MTIME_NS = -1


def _runtime() -> Any:
    global _RUNTIME_MODULE, _RUNTIME_MODULE_MTIME_NS
    runtime_path = Path(__file__).with_name("runtime.py")
    mtime_ns = int(runtime_path.stat().st_mtime_ns)
    if _RUNTIME_MODULE is not None and _RUNTIME_MODULE_MTIME_NS == mtime_ns:
        return _RUNTIME_MODULE
    spec = importlib.util.spec_from_file_location(_RUNTIME_MODULE_NAME, runtime_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load moviepilot-storage runtime")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_RUNTIME_MODULE_NAME] = module
    spec.loader.exec_module(module)
    _RUNTIME_MODULE = module
    _RUNTIME_MODULE_MTIME_NS = mtime_ns
    return module


def handle_api(request: Any, context: Any) -> Any:
    runtime = _runtime()
    method = str(getattr(request, "method", "") or "").upper()
    action = str(getattr(request, "path", "") or "").strip("/").lower()

    if action in {"", "ping"} and method == "GET":
        return runtime.ping_payload(context)

    auth_error = runtime.ensure_request_authorized(request, context)
    if auth_error is not None:
        return PluginApiResult(auth_error, status_code=401)

    if action == "roots" and method == "GET":
        return runtime.list_roots_payload(context)
    if action == "manifest-summary" and method == "GET":
        return runtime.manifest_summary_payload(context)
    if action == "item" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.item_payload(context, payload)
    if action == "list" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.list_payload(context, payload)
    if action == "mkdir" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.mkdir_payload(context, payload)
    if action == "delete" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.delete_payload(context, payload)
    if action == "rename" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.rename_payload(context, payload)
    if action == "usage" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.usage_payload(context, payload)
    if action == "resolve" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.resolve_storage_payload(context, payload)
    if action == "probe" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.probe_storage_payload(context, payload)
    if action == "upload-probe" and method in {"GET", "POST"}:
        payload = runtime.request_payload(request)
        return runtime.upload_probe_payload(context, payload)
    if action == "upload" and method == "POST":
        payload = runtime.request_payload(request)
        raw_body = bytes(getattr(request, "raw_body", b"") or b"")
        return runtime.upload_binary_payload(context, payload, raw_body)

    return PluginApiResult(
        {
            "status": "error",
            "message": f"unknown plugin api path: {getattr(request, 'path', '')}",
        },
        status_code=404,
    )


def on_startup(context: Any) -> dict[str, Any]:
    runtime = _runtime()
    return {
        "status": "ok",
        "plugin": str(getattr(context, "plugin_id", "") or "").strip(),
        "token_configured": runtime.token_is_configured(context),
        "started": True,
    }


def prepare_stream_upload(payload: Any, context: Any) -> dict[str, Any]:
    runtime = _runtime()
    return runtime.prepare_stream_upload_payload(context, dict(payload or {}))
