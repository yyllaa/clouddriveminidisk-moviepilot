from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Tuple

from app.plugins import _PluginBase

from .runtime import CloudDriveStorageBridgeClient, normalize_plugin_config


class CloudDriveStorageBridge(_PluginBase):
    plugin_name = "CloudDrive 存储桥接"
    plugin_desc = "连接 clouddrive-mini 的挂载目录，为 MoviePilot 提供可选存储路径和直传能力。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = "0.1.0"
    plugin_author = "yyllaa"
    author_url = "https://github.com/yyllaa/clouddriveminidisk-moviepilot"
    plugin_config_prefix = "clouddrive_storage_bridge_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _server_url = ""
    _token = ""
    _root_key = ""
    _last_error = ""
    _last_roots: List[Dict[str, Any]] = []
    _last_transfer: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None):
        config = normalize_plugin_config(config or {})
        self._enabled = bool(config.get("enabled"))
        self._server_url = str(config.get("server_url", "") or "").strip()
        self._token = str(config.get("token", "") or "").strip()
        self._root_key = str(config.get("root_key", "") or "").strip()
        self._last_error = ""
        self._last_roots = []
        self._last_transfer = {}

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/roots",
                "endpoint": self.api_roots,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取 CloudDrive 挂载根目录",
                "description": "从 clouddrive-mini 的桥接接口读取可用挂载根目录。",
            },
            {
                "path": "/resolve",
                "endpoint": self.api_resolve,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "解析存储路径",
                "description": "根据媒体类型和标题生成最终保存路径。",
            },
            {
                "path": "/probe",
                "endpoint": self.api_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "探测路径是否可写",
                "description": "对目标目录执行一次可写性探测。",
            },
            {
                "path": "/upload-probe",
                "endpoint": self.api_upload_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "预检查直传任务",
                "description": "检查是否可秒传，或是否允许进入直传链路。",
            },
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "server_url",
                                            "label": "CloudDrive 地址",
                                            "placeholder": "http://127.0.0.1:8765",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "token",
                                            "label": "桥接 Token",
                                            "type": "password",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "root_key",
                                            "label": "默认根目录 Root Key",
                                            "placeholder": "可留空，默认取第一个匹配项",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ], {
            "enabled": False,
            "server_url": "",
            "token": "",
            "root_key": "",
        }

    def get_page(self) -> List[dict]:
        message = self._last_error or "插件已就绪，可通过插件 API 读取根目录、解析路径并执行直传预检查。"
        message_type = "error" if self._last_error else "info"
        root_count = len(self._last_roots)
        transfer_mode = str(self._last_transfer.get("mode", "") or "").strip()
        transfer_text = "最近还没有执行传输预检查。"
        if transfer_mode == "rapid_upload":
            transfer_text = "最近一次传输命中了秒传，无需上传文件内容。"
        elif transfer_mode == "direct_stream":
            provider = str(self._last_transfer.get("provider", "") or "").strip() or "unknown"
            transfer_text = f"最近一次传输走的是直传链路，provider={provider}。"
        rows = [
            {
                "component": "VAlert",
                "props": {
                    "type": message_type,
                    "variant": "tonal",
                    "text": message,
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "success",
                    "variant": "tonal",
                    "text": f"最近一次读取到 {root_count} 个根目录。",
                },
            },
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": transfer_text,
                },
            },
        ]
        if self._last_roots:
            rows.append(
                {
                    "component": "VTable",
                    "props": {"density": "compact"},
                    "content": [
                        {
                            "component": "thead",
                            "content": [
                                {
                                    "component": "tr",
                                    "content": [
                                        {"component": "th", "text": "标签"},
                                        {"component": "th", "text": "模式"},
                                        {"component": "th", "text": "导出路径"},
                                    ],
                                }
                            ],
                        },
                        {
                            "component": "tbody",
                            "content": [
                                {
                                    "component": "tr",
                                    "content": [
                                        {"component": "td", "text": str(item.get("account_label", "") or "")},
                                        {"component": "td", "text": str(item.get("mode", "") or "")},
                                        {"component": "td", "text": str(item.get("exported_dir", "") or "")},
                                    ],
                                }
                                for item in self._last_roots[:8]
                            ],
                        },
                    ],
                }
            )
        return rows

    def stop_service(self):
        pass

    def _client(self) -> CloudDriveStorageBridgeClient:
        return CloudDriveStorageBridgeClient(
            server_url=self._server_url,
            token=self._token,
            root_key=self._root_key,
        )

    def _remember_error(self, error: Exception) -> None:
        self._last_error = str(error or "").strip() or "unknown error"

    def api_roots(self) -> dict:
        try:
            payload = self._client().list_roots()
            self._last_error = ""
            self._last_roots = list(payload.get("roots", []) or [])
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_resolve(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().resolve_storage(body or {})
            self._last_error = ""
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_probe(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().probe_storage(body or {})
            self._last_error = ""
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_upload_probe(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().upload_probe(body or {})
            self._last_error = ""
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def transfer_file(
        self,
        stream: BinaryIO,
        *,
        file_size: int,
        payload: Dict[str, Any] | None = None,
        run_probe: bool = True,
    ) -> dict:
        merged_payload = dict(payload or {})
        client = self._client()
        try:
            if run_probe:
                probe_payload = client.upload_probe({**merged_payload, "file_size": file_size})
                upload_payload = probe_payload.get("upload", {}) if isinstance(probe_payload.get("upload"), dict) else {}
                result_payload = upload_payload.get("result", {}) if isinstance(upload_payload.get("result"), dict) else {}
                if bool(result_payload.get("rapid_upload")) and not bool(result_payload.get("requires_upload", True)):
                    self._last_error = ""
                    self._last_transfer = {
                        "mode": "rapid_upload",
                        "payload": probe_payload,
                    }
                    return {
                        "status": "ok",
                        "transfer_strategy": "rapid_upload",
                        "probe": probe_payload,
                    }
            response = client.stream_upload(stream, file_size=file_size, payload=merged_payload)
            self._last_error = ""
            self._last_transfer = {
                "mode": "direct_stream",
                "provider": str(response.get("provider", "") or "").strip(),
                "payload": response,
            }
            return response
        except Exception as exc:
            self._remember_error(exc)
            raise

    def transfer_local_file(
        self,
        local_path: str,
        *,
        payload: Dict[str, Any] | None = None,
        run_probe: bool = True,
    ) -> dict:
        path = Path(str(local_path or "").strip()).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"local file not found: {path}")
        merged_payload = dict(payload or {})
        merged_payload.setdefault("filename", path.name)
        file_size = int(path.stat().st_size)
        with path.open("rb") as handle:
            return self.transfer_file(
                handle,
                file_size=file_size,
                payload=merged_payload,
                run_probe=run_probe,
            )
