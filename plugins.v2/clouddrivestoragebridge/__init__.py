from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Tuple

from app.plugins import _PluginBase
try:
    from app.core.event import Event, eventmanager
    from app.helper.storage import StorageHelper
    from app.schemas import FileItem, StorageUsage, StorageOperSelectionEventData
    from app.schemas.types import ChainEventType
except Exception:  # pragma: no cover - local tests provide lightweight stubs
    Event = Any
    StorageOperSelectionEventData = Any
    StorageUsage = Any

    class _FallbackEventManager:
        @staticmethod
        def register(_event_type: Any):
            def _decorator(func):
                return func

            return _decorator

    class _FallbackStorageHelper:
        def get_storagies(self):
            return []

        def add_storage(self, **kwargs):
            return None

    class FileItem:  # type: ignore[override]
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _FallbackChainEventType:
        StorageOperSelection = "StorageOperSelection"

    eventmanager = _FallbackEventManager()
    StorageHelper = _FallbackStorageHelper
    ChainEventType = _FallbackChainEventType()

from .runtime import CloudDriveStorageBridgeClient, normalize_plugin_config


class CloudDriveStorageBridge(_PluginBase):
    plugin_name = "CloudDrive 存储桥接"
    plugin_desc = "连接 clouddrive-mini 的挂载目录，为 MoviePilot 提供可选存储路径和直传能力。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = "0.3.1"
    plugin_author = "yyllaa"
    author_url = "https://github.com/yyllaa/clouddriveminidisk-moviepilot"
    plugin_config_prefix = "clouddrive_storage_bridge_"
    plugin_order = 50
    auth_level = 1
    _disk_name = "CloudDrive 存储"

    _enabled = False
    _server_url = ""
    _username = ""
    _password = ""
    _root_key = ""
    _last_error = ""
    _last_roots: List[Dict[str, Any]] = []
    _last_transfer: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None):
        config = normalize_plugin_config(config or {})
        storage_helper = StorageHelper()
        existing = storage_helper.get_storagies() if hasattr(storage_helper, "get_storagies") else []
        if not any(getattr(item, "type", "") == self._disk_name and getattr(item, "name", "") == self._disk_name for item in existing or []):
            try:
                storage_helper.add_storage(storage=self._disk_name, name=self._disk_name, conf={})
            except Exception:
                pass
        self._enabled = bool(config.get("enabled"))
        self._server_url = str(config.get("server_url", "") or "").strip()
        self._username = str(config.get("username", "") or "").strip()
        self._password = str(config.get("password", "") or "").strip()
        self._root_key = str(config.get("root_key", "") or "").strip()
        self._last_error = ""
        self._last_roots = []
        self._last_transfer = {}
        self._refresh_roots_snapshot()

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
                "methods": ["GET", "POST"],
                "auth": "bear",
                "summary": "读取 CloudDrive 挂载根目录",
                "description": "从 clouddrive-mini 的内置存储接口读取可用挂载根目录。",
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

    def get_module(self) -> Dict[str, Any]:
        return {
            "list_files": self.list_files,
            "any_files": self.any_files,
            "upload_file": self.upload_file,
            "delete_file": self.delete_file,
            "rename_file": self.rename_file,
            "create_folder": self.create_folder,
            "exists": self.exists,
            "get_item": self.get_item,
            "get_file_item": self.get_file_item,
            "get_parent_item": self.get_parent_item,
            "support_transtype": self.support_transtype,
            "storage_usage": self.storage_usage,
        }

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
                                            "model": "username",
                                            "label": "登录账号",
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
                                            "model": "password",
                                            "label": "登录密码",
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
            "username": "",
            "password": "",
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

    def _refresh_roots_snapshot(self) -> None:
        if not self._enabled or not self._server_url:
            return
        try:
            payload = self._client().list_roots()
            self._last_error = ""
            self._last_roots = list(payload.get("roots", []) or [])
        except Exception as exc:
            self._remember_error(exc)

    @eventmanager.register(ChainEventType.StorageOperSelection)
    def storage_oper_selection(self, event: Event) -> None:
        if not self._enabled:
            return
        event_data: StorageOperSelectionEventData = event.event_data
        if getattr(event_data, "storage", "") == self._disk_name:
            event_data.storage_oper = self  # noqa: B010

    def _client(self) -> CloudDriveStorageBridgeClient:
        return CloudDriveStorageBridgeClient(
            server_url=self._server_url,
            username=self._username,
            password=self._password,
            root_key=self._root_key,
        )

    def _to_storage_sub_path(self, path_value: Any) -> str:
        text = str(path_value or "").strip().replace("\\", "/")
        return text.strip("/")

    def _to_file_item(self, payload: Dict[str, Any] | None) -> FileItem | None:
        if not isinstance(payload, dict):
            return None
        storage_path = str(payload.get("storage_path", "") or "/").strip() or "/"
        path_for_item = storage_path if storage_path.startswith("/") else f"/{storage_path}"
        name = str(payload.get("name", "") or "").strip()
        file_type = str(payload.get("type", "") or "file").strip() or "file"
        suffix = Path(name).suffix if name else ""
        extension = suffix[1:] if suffix and file_type != "dir" else None
        parent_path = str(Path(path_for_item.rstrip("/")).parent).replace("\\", "/")
        if parent_path == ".":
            parent_path = "/"
        return FileItem(
            storage=self._disk_name,
            fileid=str(payload.get("exported_path", "") or path_for_item),
            parent_fileid=parent_path,
            name=name or ("/" if path_for_item == "/" else Path(path_for_item).name),
            basename=Path(name).stem if name else "",
            extension=extension,
            type=file_type,
            path=path_for_item,
            size=payload.get("size"),
            modify_time=payload.get("modify_time"),
        )

    def _remember_error(self, error: Exception) -> None:
        self._last_error = str(error or "").strip() or "unknown error"

    def api_roots(self, body: Dict[str, Any] | None = None) -> dict:
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

    def list_files(self, fileitem: FileItem, recursion: bool = False) -> List[FileItem]:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return []
        try:
            if getattr(fileitem, "type", "") == "file":
                item = self.get_item(fileitem)
                return [item] if item else []
            response = self._client().list_entries({"sub_path": self._to_storage_sub_path(getattr(fileitem, "path", "/"))})
            items = [self._to_file_item(item) for item in list(response.get("items", []) or [])]
            file_items = [item for item in items if item is not None]
            if not recursion:
                return file_items
            result: List[FileItem] = []
            for item in file_items:
                if getattr(item, "type", "") == "dir":
                    result.extend(self.list_files(item, recursion=True))
                else:
                    result.append(item)
            return result
        except Exception as exc:
            self._remember_error(exc)
            return []

    def any_files(self, fileitem: FileItem, extensions: List[str] | None = None) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        normalized_extensions = {str(item).lower() for item in (extensions or [])}
        for item in self.list_files(fileitem, recursion=True):
            if getattr(item, "type", "") != "file":
                continue
            if not normalized_extensions:
                return True
            extension = getattr(item, "extension", None)
            if extension and f".{str(extension).lower()}" in normalized_extensions:
                return True
        return False

    def create_folder(self, fileitem: FileItem, name: str) -> FileItem | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        current = self._to_storage_sub_path(getattr(fileitem, "path", "/"))
        sub_path = "/".join(part for part in [current, str(name or "").strip().strip("/")] if part)
        try:
            response = self._client().mkdir({"sub_path": sub_path})
            self._last_error = ""
            return self._to_file_item(response.get("item"))
        except Exception as exc:
            self._remember_error(exc)
            return None

    def upload_file(self, fileitem: FileItem, path: Path, new_name: str | None = None) -> FileItem | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        filename = str(new_name or path.name).strip()
        payload = {
            "sub_path": self._to_storage_sub_path(getattr(fileitem, "path", "/")),
            "filename": filename,
        }
        try:
            response = self.transfer_local_file(str(path), payload=payload, run_probe=True)
            self._last_error = ""
            uploaded_path = "/".join(part for part in [payload["sub_path"], filename] if part)
            return self.get_file_item(self._disk_name, Path(f"/{uploaded_path}")) if response else None
        except Exception as exc:
            self._remember_error(exc)
            return None

    def delete_file(self, fileitem: FileItem) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        try:
            response = self._client().delete_entry({"sub_path": self._to_storage_sub_path(getattr(fileitem, "path", "/"))})
            self._last_error = ""
            return bool(response.get("deleted"))
        except Exception as exc:
            self._remember_error(exc)
            return False

    def rename_file(self, fileitem: FileItem, name: str) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        try:
            response = self._client().rename_entry(
                {
                    "sub_path": self._to_storage_sub_path(getattr(fileitem, "path", "/")),
                    "new_name": str(name or "").strip(),
                }
            )
            self._last_error = ""
            return bool(response.get("renamed"))
        except Exception as exc:
            self._remember_error(exc)
            return False

    def exists(self, fileitem: FileItem) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        return self.get_item(fileitem) is not None

    def get_item(self, fileitem: FileItem) -> FileItem | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        return self.get_file_item(self._disk_name, Path(str(getattr(fileitem, "path", "/")) or "/"))

    def get_file_item(self, storage: str, path: Path) -> FileItem | None:
        if storage != self._disk_name:
            return None
        try:
            response = self._client().get_item({"sub_path": self._to_storage_sub_path(path.as_posix())})
            self._last_error = ""
            return self._to_file_item(response.get("item"))
        except Exception as exc:
            self._remember_error(exc)
            return None

    def get_parent_item(self, fileitem: FileItem) -> FileItem | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        parent_path = Path(str(getattr(fileitem, "path", "/")) or "/").parent
        if str(parent_path) == ".":
            parent_path = Path("/")
        return self.get_file_item(self._disk_name, parent_path)

    def support_transtype(self, storage: str) -> Dict[str, str] | None:
        if storage != self._disk_name:
            return None
        return {"copy": "复制", "rename": "重命名", "delete": "删除"}

    def storage_usage(self, storage: str) -> StorageUsage | None:
        if storage != self._disk_name:
            return None
        try:
            response = self._client().usage({})
            self._last_error = ""
            usage_payload = response.get("usage", {}) if isinstance(response.get("usage"), dict) else {}
            return StorageUsage(
                total=usage_payload.get("total", 0),
                available=usage_payload.get("available", 0),
            )
        except Exception as exc:
            self._remember_error(exc)
            return None

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
