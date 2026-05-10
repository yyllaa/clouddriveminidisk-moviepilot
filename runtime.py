from __future__ import annotations

from typing import Any

import requests


def normalize_plugin_config(config: dict[str, Any]) -> dict[str, Any]:
    config = dict(config or {})
    return {
        "enabled": bool(config.get("enabled")),
        "server_url": str(config.get("server_url", "") or "").strip().rstrip("/"),
        "token": str(config.get("token", "") or "").strip(),
        "root_key": str(config.get("root_key", "") or "").strip(),
    }


class CloudDriveStorageBridgeClient:
    def __init__(
        self,
        *,
        server_url: str,
        token: str,
        root_key: str = "",
        timeout_seconds: float = 20.0,
        upload_timeout_seconds: float = 7200.0,
    ) -> None:
        self.server_url = str(server_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self.root_key = str(root_key or "").strip()
        self.timeout_seconds = float(timeout_seconds or 20.0)
        self.upload_timeout_seconds = float(upload_timeout_seconds or 7200.0)

    def _api_url(self, path: str) -> str:
        normalized = str(path or "").strip().lstrip("/")
        if not self.server_url:
            raise ValueError("server_url is empty")
        return f"{self.server_url}/api/public/plugins/moviepilot-storage/api/{normalized}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-MP-Storage-Token"] = self.token
        return headers

    def _stream_upload_url(self) -> str:
        if not self.server_url:
            raise ValueError("server_url is empty")
        return f"{self.server_url}/api/public/moviepilot-storage/upload-stream"

    def _merge_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(payload or {})
        if self.root_key and not str(merged.get("root_key", "") or "").strip():
            merged["root_key"] = self.root_key
        return merged

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.request(
            method=method.upper(),
            url=self._api_url(path),
            headers=self._headers(),
            json=payload or None,
            timeout=self.timeout_seconds,
        )
        data = response.json()
        if response.status_code >= 400 or str(data.get("status", "") or "").lower() == "error":
            raise RuntimeError(str(data.get("message", "") or f"bridge request failed: {response.status_code}"))
        return data

    def list_roots(self) -> dict[str, Any]:
        return self._request("GET", "roots")

    def resolve_storage(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = self._merge_defaults(payload)
        return self._request("POST", "resolve", merged)

    def probe_storage(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = self._merge_defaults(payload)
        return self._request("POST", "probe", merged)

    def upload_probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        merged = self._merge_defaults(payload)
        return self._request("POST", "upload-probe", merged)

    def stream_upload(self, stream: Any, *, file_size: int, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_size = max(0, int(file_size or 0))
        if normalized_size <= 0:
            raise ValueError("file_size must be greater than 0")
        merged = self._merge_defaults(payload)
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(normalized_size),
        }
        if self.token:
            headers["X-MP-Storage-Token"] = self.token
        header_map = {
            "filename": "X-MP-Filename",
            "root_key": "X-MP-Root-Key",
            "account_id": "X-MP-Account-Id",
            "mode": "X-MP-Mode",
            "media_type": "X-MP-Media-Type",
            "title": "X-MP-Title",
            "year": "X-MP-Year",
            "season": "X-MP-Season",
            "category": "X-MP-Category",
            "sub_path": "X-MP-Sub-Path",
            "save_as": "X-MP-Save-As",
            "create_dirs": "X-MP-Create-Dirs",
            "content_hash": "X-MP-Content-Hash",
            "content_hash_algorithm": "X-MP-Content-Hash-Algorithm",
            "sha1": "X-MP-Sha1",
            "md5": "X-MP-Md5",
            "md5_block_size": "X-MP-Md5-Block-Size",
            "md5_block_hashes": "X-MP-Md5-Block-Hashes",
            "sign_check": "X-MP-Sign-Check",
            "sign_val": "X-MP-Sign-Val",
        }
        for key, header_name in header_map.items():
            value = merged.get(key)
            if value is None or value == "":
                continue
            headers[header_name] = str(value)
        response = requests.request(
            method="POST",
            url=self._stream_upload_url(),
            headers=headers,
            data=stream,
            timeout=self.upload_timeout_seconds,
        )
        data = response.json()
        if response.status_code >= 400 or str(data.get("status", "") or "").lower() == "error":
            raise RuntimeError(str(data.get("message", "") or f"bridge upload failed: {response.status_code}"))
        return data
