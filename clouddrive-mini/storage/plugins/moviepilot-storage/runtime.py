from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any


INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACE_RE = re.compile(r"\s+")
SUPPORTED_MEDIA_TYPES = {"movie", "tv", "series", "anime", "generic", "download"}
DEFAULT_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
STREAM_REQUEST_HEADER_MAP = {
    "filename": ("x-mp-filename", "x-filename"),
    "root_key": ("x-mp-root-key",),
    "account_id": ("x-mp-account-id",),
    "mode": ("x-mp-mode",),
    "media_type": ("x-mp-media-type",),
    "title": ("x-mp-title",),
    "year": ("x-mp-year",),
    "season": ("x-mp-season",),
    "category": ("x-mp-category",),
    "sub_path": ("x-mp-sub-path",),
    "save_as": ("x-mp-save-as",),
    "create_dirs": ("x-mp-create-dirs",),
    "content_hash": ("x-mp-content-hash",),
    "content_hash_algorithm": ("x-mp-content-hash-algorithm",),
    "sha1": ("x-mp-sha1",),
    "md5": ("x-mp-md5",),
    "md5_block_size": ("x-mp-md5-block-size",),
    "md5_block_hashes": ("x-mp-md5-block-hashes",),
    "sign_check": ("x-mp-sign-check",),
    "sign_val": ("x-mp-sign-val",),
    "chunk_size": ("x-mp-chunk-size",),
}


def _plugin_config(context: Any) -> dict[str, Any]:
    config = getattr(context, "config", {})
    return dict(config) if isinstance(config, dict) else {}


def _env_text(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _normalize_path_aliases(raw: Any) -> list[dict[str, str]]:
    aliases: list[dict[str, str]] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                raw = parsed
            else:
                pairs = [item for item in text.split(";") if item.strip()]
                raw = []
                for pair in pairs:
                    left, _, right = pair.partition("=")
                    raw.append({"from": left.strip(), "to": right.strip()})
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        source = _normalize_path_text(item.get("from"))
        target = str(item.get("to", "") or "").strip()
        if source and target:
            aliases.append({"from": source, "to": target})
    aliases.sort(key=lambda item: len(item["from"]), reverse=True)
    return aliases


def _normalized_config(context: Any) -> dict[str, Any]:
    config = _plugin_config(context)
    env_aliases = _env_text("MOVIEPILOT_STORAGE_PATH_ALIASES")
    aliases_source = env_aliases if env_aliases else config.get("path_aliases", [])
    preferred_root_keys = [
        str(item or "").strip()
        for item in (config.get("preferred_root_keys", []) if isinstance(config.get("preferred_root_keys"), list) else [])
        if str(item or "").strip()
    ]
    include_account_ids = [
        str(item or "").strip()
        for item in (config.get("include_account_ids", []) if isinstance(config.get("include_account_ids"), list) else [])
        if str(item or "").strip()
    ]
    include_modes = [
        str(item or "").strip().lower()
        for item in (config.get("include_modes", []) if isinstance(config.get("include_modes"), list) else [])
        if str(item or "").strip()
    ]
    return {
        "token": _effective_token(context),
        "path_aliases": _normalize_path_aliases(aliases_source),
        "preferred_root_keys": preferred_root_keys,
        "include_account_ids": include_account_ids,
        "include_modes": include_modes,
        "create_dirs_on_resolve": bool(config.get("create_dirs_on_resolve", True)),
        "allow_probe_write": bool(config.get("allow_probe_write", True)),
    }


def _effective_token(context: Any) -> str:
    return _env_text("MOVIEPILOT_STORAGE_TOKEN") or str(_plugin_config(context).get("token", "") or "").strip()


def token_is_configured(context: Any) -> bool:
    return bool(_effective_token(context))


def ping_payload(context: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "plugin": str(getattr(context, "plugin_id", "") or "").strip(),
        "name": str(getattr(context, "manifest", {}).get("name", "") or "").strip(),
        "token_configured": token_is_configured(context),
        "public_api_hint": "Pass X-MP-Storage-Token or Authorization: Bearer <token> to access protected endpoints.",
    }


def _request_header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", {})
    if not isinstance(headers, dict):
        return ""
    target = str(name or "").strip().lower()
    for key, value in headers.items():
        if str(key or "").strip().lower() == target:
            return str(value or "").strip()
    return ""


def _extract_request_token(request: Any) -> str:
    auth = _request_header(request, "authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header_token = _request_header(request, "x-mp-storage-token")
    if header_token:
        return header_token
    query = getattr(request, "query", {})
    if isinstance(query, dict):
        values = query.get("token", [])
        if isinstance(values, list) and values:
            return str(values[0] or "").strip()
    body = getattr(request, "body", {})
    if isinstance(body, dict):
        return str(body.get("token", "") or "").strip()
    return ""


def ensure_request_authorized(request: Any, context: Any) -> dict[str, Any] | None:
    expected = _effective_token(context)
    if not expected:
        return {
            "status": "error",
            "message": "moviepilot storage token is not configured",
        }
    provided = _extract_request_token(request)
    if provided != expected:
        return {
            "status": "error",
            "message": "unauthorized",
        }
    return None


def request_payload(request: Any) -> dict[str, Any]:
    body = getattr(request, "body", None)
    if isinstance(body, dict) and body:
        return dict(body)
    query = getattr(request, "query", {})
    payload: dict[str, Any] = {}
    if isinstance(query, dict):
        for key, values in query.items():
            if not isinstance(values, list) or not values:
                continue
            payload[str(key)] = values[0]
    return payload


def _normalized_query(query: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    if not isinstance(query, dict):
        return normalized
    for key, values in query.items():
        if isinstance(values, list):
            normalized[str(key)] = [str(item or "") for item in values]
        elif values is not None:
            normalized[str(key)] = [str(values)]
    return normalized


def _normalized_headers(headers: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(headers, dict):
        return normalized
    for key, value in headers.items():
        normalized[str(key or "").strip().lower()] = str(value or "").strip()
    return normalized


def stream_request_payload(envelope: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], dict[str, list[str]]]:
    query = _normalized_query(envelope.get("query"))
    headers = _normalized_headers(envelope.get("headers"))
    payload: dict[str, Any] = {}
    for key, values in query.items():
        if values:
            payload[key] = values[0]
    for key, header_names in STREAM_REQUEST_HEADER_MAP.items():
        for header_name in header_names:
            value = headers.get(header_name, "")
            if value:
                payload[key] = value
                break
    return payload, headers, query


def _request_text(payload: dict[str, Any], key: str, default: str = "") -> str:
    return str(payload.get(key, default) or default).strip()


def _request_int(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(str(payload.get(key, default) or default).strip())
    except (TypeError, ValueError):
        return int(default)


def _request_bool(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    if key not in payload:
        return bool(default)
    raw = str(payload.get(key, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(payload.get(key))


def _request_json_list(payload: dict[str, Any], key: str) -> list[str]:
    raw = payload.get(key)
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in text.split(",") if item.strip()]
    if not isinstance(parsed, list):
        return []
    return [str(item or "").strip() for item in parsed if str(item or "").strip()]


def _app(context: Any) -> Any:
    return getattr(context, "app", None)


def _manifest(context: Any) -> dict[str, Any]:
    app = _app(context)
    getter = getattr(app, "get_media_mount_manifest", None)
    if not callable(getter):
        raise RuntimeError("host app does not expose get_media_mount_manifest")
    payload = getter()
    return dict(payload) if isinstance(payload, dict) else {}


def _normalize_path_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser())


def _derive_mapping_relative_path(mapping_root: Path, mapping_dir: Path) -> str:
    try:
        return str(mapping_dir.resolve().relative_to(mapping_root.resolve())).replace("\\", "/")
    except Exception:
        return mapping_dir.name


def _mounted_roots(context: Any) -> list[dict[str, Any]]:
    manifest = _manifest(context)
    mapping_root = Path(str(manifest.get("mapping_root", "") or ".")).expanduser()
    roots: list[dict[str, Any]] = []
    config = _normalized_config(context)
    include_account_ids = set(config["include_account_ids"])
    include_modes = set(config["include_modes"])
    for raw in manifest.get("mounted_roots", []) if isinstance(manifest.get("mounted_roots"), list) else []:
        if not isinstance(raw, dict):
            continue
        account_id = str(raw.get("account_id", "") or "").strip()
        mode = str(raw.get("mode", "") or "").strip().lower()
        if include_account_ids and account_id not in include_account_ids:
            continue
        if include_modes and mode not in include_modes:
            continue
        mapping_dir_text = str(raw.get("mapping_dir", "") or "").strip()
        if not mapping_dir_text:
            continue
        mapping_dir = Path(mapping_dir_text).expanduser()
        mapping_relative_path = str(raw.get("mapping_relative_path", "") or "").strip().replace("\\", "/").strip("/")
        if not mapping_relative_path:
            mapping_relative_path = _derive_mapping_relative_path(mapping_root, mapping_dir)
        exported_dir = _apply_path_alias(mapping_dir, config["path_aliases"])
        roots.append(
            {
                "root_key": str(raw.get("root_key", "") or "").strip(),
                "account_id": account_id,
                "account_label": str(raw.get("account_label", "") or "").strip(),
                "provider": str(raw.get("provider", "") or "").strip(),
                "mode": mode,
                "source_path": str(raw.get("source_path", "") or "").strip(),
                "mapping_dir": str(mapping_dir),
                "mapping_relative_path": mapping_relative_path,
                "exported_dir": exported_dir,
                "exists": mapping_dir.exists(),
                "is_dir": mapping_dir.is_dir(),
                "file_count": int(raw.get("file_count", 0) or 0),
                "dir_count": int(raw.get("dir_count", 0) or 0),
            }
        )
    roots.sort(key=lambda item: (item["account_label"], item["mode"], item["source_path"], item["mapping_dir"]))
    return roots


def _apply_path_alias(path: Path, aliases: list[dict[str, str]]) -> str:
    original = _normalize_path_text(path)
    original_lower = original.replace("\\", "/").lower()
    for alias in aliases:
        source = alias["from"].replace("\\", "/").lower()
        if original_lower == source:
            return alias["to"]
        if original_lower.startswith(source.rstrip("/") + "/"):
            suffix = original.replace("\\", "/")[len(alias["from"].replace("\\", "/")):].lstrip("/")
            return alias["to"].rstrip("/\\") + ("/" + suffix if suffix else "")
    return original


def list_roots_payload(context: Any) -> dict[str, Any]:
    config = _normalized_config(context)
    return {
        "status": "ok",
        "roots": _mounted_roots(context),
        "preferred_root_keys": list(config["preferred_root_keys"]),
        "path_aliases": list(config["path_aliases"]),
    }


def manifest_summary_payload(context: Any) -> dict[str, Any]:
    manifest = _manifest(context)
    roots = _mounted_roots(context)
    return {
        "status": "ok",
        "generated_at": str(manifest.get("generated_at", "") or "").strip(),
        "mapping_root": str(manifest.get("mapping_root", "") or "").strip(),
        "mounted_root_count": len(roots),
        "file_count": int(manifest.get("file_count", 0) or 0),
        "dir_count": int(manifest.get("dir_count", 0) or 0),
    }


def _resolve_target_path(context: Any, payload: dict[str, Any], *, create_dirs: bool = False) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    local_path_text = str(payload.get("local_path", "") or "").strip()
    if local_path_text:
        roots = _mounted_roots(context)
        selected_root = _select_root(context, payload, roots)
        local_path = Path(local_path_text).expanduser()
        return local_path, selected_root, {
            "status": "ok",
            "selected_root": selected_root,
            "relative_subpath": "",
            "relative_to_root": "",
            "local_path": str(local_path),
            "exported_path": _apply_path_alias(local_path, _normalized_config(context)["path_aliases"]),
            "created": False,
        }
    resolved_payload = resolve_storage_payload(
        context,
        {
            **dict(payload or {}),
            "create_dirs": bool(create_dirs),
        },
    )
    return (
        Path(str(resolved_payload["local_path"])),
        dict(resolved_payload["selected_root"]),
        resolved_payload,
    )


def _storage_path_for(root: dict[str, Any], local_path: Path) -> str:
    root_dir = Path(str(root.get("mapping_dir", "") or "")).expanduser()
    try:
        relative = local_path.resolve().relative_to(root_dir.resolve())
        relative_text = str(relative).replace("\\", "/").strip("/")
    except Exception:
        relative_text = local_path.name
    if not relative_text:
        return "/"
    return f"/{relative_text}"


def _item_payload_for_path(context: Any, root: dict[str, Any], local_path: Path) -> dict[str, Any]:
    stat = local_path.stat()
    is_dir = local_path.is_dir()
    return {
        "root_key": str(root.get("root_key", "") or "").strip(),
        "account_id": str(root.get("account_id", "") or "").strip(),
        "account_label": str(root.get("account_label", "") or "").strip(),
        "provider": str(root.get("provider", "") or "").strip(),
        "mode": str(root.get("mode", "") or "").strip(),
        "name": local_path.name if local_path.name else "/",
        "type": "dir" if is_dir else "file",
        "size": 0 if is_dir else int(stat.st_size),
        "modify_time": int(stat.st_mtime),
        "local_path": str(local_path),
        "exported_path": _apply_path_alias(local_path, _normalized_config(context)["path_aliases"]),
        "storage_path": _storage_path_for(root, local_path),
        "exists": True,
        "is_dir": is_dir,
    }


def item_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    local_path, root, resolved_payload = _resolve_target_path(context, payload, create_dirs=False)
    if not local_path.exists():
        return {
            "status": "ok",
            "item": None,
            "selected_root": root,
            "resolved_path": str(local_path),
            "relative_subpath": str(resolved_payload.get("relative_subpath", "") or ""),
        }
    return {
        "status": "ok",
        "item": _item_payload_for_path(context, root, local_path),
        "selected_root": root,
        "resolved_path": str(local_path),
        "relative_subpath": str(resolved_payload.get("relative_subpath", "") or ""),
    }


def list_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    local_path, root, resolved_payload = _resolve_target_path(context, payload, create_dirs=False)
    if not local_path.exists():
        return {
            "status": "ok",
            "items": [],
            "selected_root": root,
            "resolved_path": str(local_path),
            "relative_subpath": str(resolved_payload.get("relative_subpath", "") or ""),
        }
    if not local_path.is_dir():
        items = [_item_payload_for_path(context, root, local_path)]
    else:
        items = [
            _item_payload_for_path(context, root, child)
            for child in sorted(
                local_path.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        ]
    return {
        "status": "ok",
        "items": items,
        "selected_root": root,
        "resolved_path": str(local_path),
        "relative_subpath": str(resolved_payload.get("relative_subpath", "") or ""),
    }


def mkdir_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    local_path, root, resolved_payload = _resolve_target_path(context, payload, create_dirs=True)
    local_path.mkdir(parents=True, exist_ok=True)
    return {
        "status": "ok",
        "item": _item_payload_for_path(context, root, local_path),
        "selected_root": root,
        "resolved_path": str(local_path),
        "relative_subpath": str(resolved_payload.get("relative_subpath", "") or ""),
        "created": True,
    }


def _sanitize_path_component(value: Any) -> str:
    text = SPACE_RE.sub(" ", str(value or "").strip())
    if not text:
        return ""
    text = INVALID_PATH_CHARS_RE.sub("_", text)
    return text.rstrip(". ").strip()


def _normalized_media_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "show":
        text = "tv"
    if text not in SUPPORTED_MEDIA_TYPES:
        return "generic"
    if text == "series":
        return "tv"
    return text


def _title_with_year(title: str, year: Any) -> str:
    safe_title = _sanitize_path_component(title)
    year_text = str(year or "").strip()
    if safe_title and year_text:
        return f"{safe_title} ({year_text})"
    return safe_title or year_text


def _season_dir(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        number = int(text)
    except ValueError:
        normalized = text.upper().lstrip("S")
        return f"Season {normalized}" if normalized else ""
    return f"Season {number}"


def _build_relative_subpath(payload: dict[str, Any]) -> str:
    provided_sub_path = str(payload.get("sub_path", "") or "").strip().replace("\\", "/").strip("/")
    if provided_sub_path:
        parts = [_sanitize_path_component(part) for part in provided_sub_path.split("/") if _sanitize_path_component(part)]
        return "/".join(parts)

    media_type = _normalized_media_type(payload.get("media_type", "generic"))
    title_dir = _title_with_year(str(payload.get("title", "") or ""), payload.get("year"))
    category = _sanitize_path_component(payload.get("category", ""))
    season_dir = _season_dir(payload.get("season"))

    parts: list[str] = []
    if category:
        parts.append(category)
    if title_dir:
        parts.append(title_dir)
    if media_type in {"tv", "anime"} and season_dir:
        parts.append(season_dir)
    return "/".join(part for part in parts if part)


def _select_root(context: Any, payload: dict[str, Any], roots: list[dict[str, Any]]) -> dict[str, Any]:
    if not roots:
        raise RuntimeError("no mounted roots are available")
    requested_root_key = str(payload.get("root_key", "") or "").strip()
    if requested_root_key:
        for root in roots:
            if root["root_key"] == requested_root_key:
                return root
        raise RuntimeError(f"mounted root not found: {requested_root_key}")
    requested_account_id = str(payload.get("account_id", "") or "").strip()
    requested_mode = str(payload.get("mode", "") or "").strip().lower()
    filtered = roots
    if requested_account_id:
        filtered = [item for item in filtered if item["account_id"] == requested_account_id]
    if requested_mode:
        filtered = [item for item in filtered if item["mode"] == requested_mode]
    if not filtered:
        raise RuntimeError("no mounted root matches the requested selector")
    preferred_root_keys = _normalized_config(context)["preferred_root_keys"]
    for preferred in preferred_root_keys:
        for root in filtered:
            if root["root_key"] == preferred:
                return root
    return filtered[0]


def resolve_storage_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    roots = _mounted_roots(context)
    config = _normalized_config(context)
    selected_root = _select_root(context, payload, roots)
    relative_subpath = _build_relative_subpath(payload)
    local_dir = Path(selected_root["mapping_dir"])
    if relative_subpath:
        local_dir = local_dir / Path(relative_subpath.replace("/", os.sep))
    created = False
    if bool(payload.get("create_dirs", config["create_dirs_on_resolve"])):
        local_dir.mkdir(parents=True, exist_ok=True)
        created = True
    exported_dir = _apply_path_alias(local_dir, config["path_aliases"])
    relative_to_root = relative_subpath.replace("\\", "/").strip("/")
    return {
        "status": "ok",
        "media_type": _normalized_media_type(payload.get("media_type", "generic")),
        "selected_root": selected_root,
        "relative_subpath": relative_subpath,
        "relative_to_root": relative_to_root,
        "local_path": str(local_dir),
        "exported_path": exported_dir,
        "exists": local_dir.exists(),
        "created": created,
    }


def probe_storage_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    config = _normalized_config(context)
    if not config["allow_probe_write"]:
        return {
            "status": "error",
            "message": "write probe is disabled",
        }
    local_path_text = str(payload.get("local_path", "") or "").strip()
    if local_path_text:
        target_dir = Path(local_path_text).expanduser()
    else:
        resolved = resolve_storage_payload(context, payload)
        target_dir = Path(str(resolved["local_path"]))
    target_dir.mkdir(parents=True, exist_ok=True)
    probe_path = target_dir / ".moviepilot-storage-probe.tmp"
    probe_path.write_text("ok", encoding="utf-8")
    size = probe_path.stat().st_size
    probe_path.unlink(missing_ok=True)
    return {
        "status": "ok",
        "local_path": str(target_dir),
        "writable": True,
        "probe_file_size": int(size),
    }


def _upload_request_args(context: Any, payload: dict[str, Any], *, file_size: int) -> dict[str, Any]:
    resolved = resolve_storage_payload(context, payload)
    remote_dir_path = "/" + str(resolved.get("relative_to_root", "") or "").replace("\\", "/").strip("/")
    if remote_dir_path == "/":
        remote_dir_path = str(selected_root_source_path(resolved) or "/")
    root = resolved.get("selected_root", {}) if isinstance(resolved.get("selected_root"), dict) else {}
    mode = str(root.get("mode", "") or "personal").strip().lower()
    media_type = str(resolved.get("media_type", "") or "generic").strip().lower()
    md5_block_hashes = _request_json_list(payload, "md5_block_hashes")
    return {
        "account_id": str(root.get("account_id", "") or "").strip(),
        "mode": "family" if mode == "family" else "personal",
        "remote_dir_path": remote_dir_path,
        "filename": _request_text(payload, "filename") or _sanitize_path_component(payload.get("save_as", "")) or "upload.bin",
        "file_size": max(0, int(file_size or 0)),
        "media_type": media_type,
        "content_hash": _request_text(payload, "content_hash"),
        "content_hash_algorithm": _request_text(payload, "content_hash_algorithm"),
        "sha1": _request_text(payload, "sha1"),
        "md5": _request_text(payload, "md5"),
        "md5_block_size": _request_int(payload, "md5_block_size", 0),
        "md5_block_hashes": md5_block_hashes,
        "sign_check": _request_text(payload, "sign_check"),
        "sign_val": _request_text(payload, "sign_val"),
        "chunk_size": max(256 * 1024, min(_request_int(payload, "chunk_size", DEFAULT_UPLOAD_CHUNK_SIZE), 8 * 1024 * 1024)),
        "create_dirs": bool(resolved.get("created")),
        "resolved_path": str(resolved.get("local_path", "") or ""),
        "selected_root": root,
    }


def selected_root_source_path(resolved_payload: dict[str, Any]) -> str:
    root = resolved_payload.get("selected_root", {}) if isinstance(resolved_payload.get("selected_root"), dict) else {}
    source_path = str(root.get("source_path", "") or "").strip()
    if not source_path:
        return "/"
    return source_path if source_path.startswith("/") else f"/{source_path}"


def _direct_upload_capabilities(context: Any, account_id: str) -> dict[str, Any]:
    app = _app(context)
    if app is None:
        raise RuntimeError("host app is unavailable")
    if not account_id:
        raise RuntimeError("selected root is missing account_id")
    loader = getattr(app, "_load_account_client", None)
    if not callable(loader):
        raise RuntimeError("host app does not expose account client loader")
    with app._lock:
        client, _config_path, resolved_account_id = loader(account_id)
    adapter = getattr(client, "_provider_adapter", None)
    provider = str(getattr(adapter, "provider_id", "") or "").strip()
    capabilities = adapter.upload_capabilities(client) if hasattr(adapter, "upload_capabilities") else {}
    if hasattr(adapter, "supports_upload_stream_known_size"):
        direct_supported = bool(adapter.supports_upload_stream_known_size(client))
    else:
        checker = getattr(client, "supports_upload_stream_known_size", None)
        direct_supported = bool(checker()) if callable(checker) else callable(getattr(client, "upload_stream_known_size", None))
    capabilities = dict(capabilities) if isinstance(capabilities, dict) else {}
    capabilities["known_size_stream_supported"] = bool(direct_supported)
    return {
        "provider": provider,
        "account_id": str(resolved_account_id or account_id).strip(),
        "capabilities": capabilities,
        "direct_supported": bool(direct_supported),
    }


def prepare_stream_upload_payload(context: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    payload, headers, query = stream_request_payload(dict(envelope or {}))
    request = SimpleNamespace(headers=headers, query=query, body=payload)
    auth_error = ensure_request_authorized(request, context)
    if auth_error is not None:
        return {
            **auth_error,
            "status_code": 401,
        }
    content_length = _request_int(dict(envelope or {}), "content_length", 0)
    if content_length <= 0:
        return {
            "status": "error",
            "status_code": 400,
            "message": "content_length must be greater than 0",
        }
    args = _upload_request_args(context, payload, file_size=content_length)
    if args["mode"] != "personal":
        return {
            "status": "error",
            "status_code": 409,
            "message": "direct stream upload only supports personal mode roots",
            "resolved_path": args["resolved_path"],
            "selected_root": args["selected_root"],
        }
    capability = _direct_upload_capabilities(context, args["account_id"])
    if not capability["direct_supported"]:
        return {
            "status": "error",
            "status_code": 409,
            "message": "selected provider does not support direct known-size streaming; refusing to fall back to cache upload",
            "resolved_path": args["resolved_path"],
            "selected_root": args["selected_root"],
            "provider": capability["provider"],
            "capabilities": capability["capabilities"],
        }
    return {
        "status": "ok",
        "content_length": int(content_length),
        "account_id": args["account_id"],
        "remote_dir_path": args["remote_dir_path"],
        "filename": args["filename"],
        "content_hash": args["content_hash"],
        "content_hash_algorithm": args["content_hash_algorithm"],
        "sha1": args["sha1"],
        "md5": args["md5"],
        "resolved_path": args["resolved_path"],
        "selected_root": args["selected_root"],
        "provider": capability["provider"],
        "capabilities": capability["capabilities"],
    }


def upload_probe_payload(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    app = _app(context)
    creator = getattr(app, "create_upload_task", None)
    if not callable(creator):
        raise RuntimeError("host app does not expose create_upload_task")
    args = _upload_request_args(context, payload, file_size=_request_int(payload, "file_size", 0))
    if int(args["file_size"]) <= 0:
        raise RuntimeError("file_size must be greater than 0")
    task = creator(
        args["filename"],
        args["file_size"],
        args["remote_dir_path"],
        account_id=args["account_id"],
        mode=args["mode"],
        chunk_size=args["chunk_size"],
        content_hash=args["content_hash"],
        content_hash_algorithm=args["content_hash_algorithm"],
        sha1=args["sha1"],
        md5=args["md5"],
        md5_block_size=args["md5_block_size"],
        md5_block_hashes=args["md5_block_hashes"],
        sign_check=args["sign_check"],
        sign_val=args["sign_val"],
        probe_only=True,
    )
    return {
        "status": "ok",
        "upload": dict(task) if isinstance(task, dict) else {},
        "resolved_path": args["resolved_path"],
        "selected_root": args["selected_root"],
    }


def upload_binary_payload(context: Any, payload: dict[str, Any], raw_body: bytes) -> dict[str, Any]:
    app = _app(context)
    creator = getattr(app, "create_upload_task", None)
    upload_chunk = getattr(app, "upload_task_chunk", None)
    session_getter = getattr(app, "get_upload_task_session", None)
    if not callable(creator) or not callable(upload_chunk):
        raise RuntimeError("host app does not expose upload task APIs")
    file_size = len(raw_body or b"")
    if file_size <= 0:
        raise RuntimeError("upload body is empty")
    args = _upload_request_args(context, payload, file_size=file_size)
    task = creator(
        args["filename"],
        args["file_size"],
        args["remote_dir_path"],
        account_id=args["account_id"],
        mode=args["mode"],
        chunk_size=args["chunk_size"],
        content_hash=args["content_hash"],
        content_hash_algorithm=args["content_hash_algorithm"],
        sha1=args["sha1"],
        md5=args["md5"],
        md5_block_size=args["md5_block_size"],
        md5_block_hashes=args["md5_block_hashes"],
        sign_check=args["sign_check"],
        sign_val=args["sign_val"],
        probe_only=False,
    )
    task_payload = dict(task) if isinstance(task, dict) else {}
    task_id = str(task_payload.get("task_id", "") or "").strip()
    result_payload = task_payload.get("result", {}) if isinstance(task_payload.get("result"), dict) else {}
    if bool(result_payload.get("rapid_upload")) and not bool(result_payload.get("requires_upload", True)):
        return {
            "status": "ok",
            "transfer_strategy": "rapid_upload",
            "task": task_payload,
            "resolved_path": args["resolved_path"],
            "selected_root": args["selected_root"],
        }
    if not task_id:
        raise RuntimeError("upload task was not created")
    total_chunks = max(1, (file_size + args["chunk_size"] - 1) // args["chunk_size"])
    last_chunk_response: dict[str, Any] = {}
    for chunk_index in range(total_chunks):
        start = chunk_index * args["chunk_size"]
        end = min(file_size, start + args["chunk_size"])
        body = raw_body[start:end]
        last_chunk_response = upload_chunk(
            task_id,
            chunk_index,
            total_chunks,
            args["chunk_size"],
            body,
        )
    session = session_getter(task_id) if callable(session_getter) else last_chunk_response
    return {
        "status": "ok",
        "transfer_strategy": "upload_task",
        "task": dict(session) if isinstance(session, dict) else {},
        "resolved_path": args["resolved_path"],
        "selected_root": args["selected_root"],
    }
