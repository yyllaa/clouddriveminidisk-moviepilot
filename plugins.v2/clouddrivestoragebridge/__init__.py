from __future__ import annotations

from os import PathLike
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
    plugin_desc = "连接 clouddrive-mini，并在 MoviePilot 中以原生存储方式展示挂载云盘。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = "16.0"
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
    _shared_enabled = False
    _shared_server_url = ""
    _shared_username = ""
    _shared_password = ""
    _shared_root_key = ""
    _shared_last_error = ""
    _shared_last_roots: List[Dict[str, Any]] = []
    _shared_last_transfer: Dict[str, Any] = {}

    def init_plugin(self, config: dict | None = None):
        config = normalize_plugin_config(config or {})
        storage_helper = StorageHelper()
        existing = storage_helper.get_storagies() if hasattr(storage_helper, "get_storagies") else []
        if not any(
            getattr(item, "type", "") == self._disk_name and getattr(item, "name", "") == self._disk_name
            for item in existing or []
        ):
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
        self.__class__._shared_enabled = self._enabled
        self.__class__._shared_server_url = self._server_url
        self.__class__._shared_username = self._username
        self.__class__._shared_password = self._password
        self.__class__._shared_root_key = self._root_key
        self.__class__._shared_last_error = ""
        self.__class__._shared_last_roots = []
        self.__class__._shared_last_transfer = {}
        self._refresh_roots_snapshot()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        api_prefix = f"/{self.__class__.__name__}"
        return [
            {
                "path": f"{api_prefix}/roots",
                "endpoint": self.api_roots,
                "methods": ["GET", "POST"],
                "auth": "bear",
                "summary": "读取 CloudDrive 挂载根目录",
                "description": "从 clouddrive-mini 读取当前可用的挂载根目录。",
            },
            {
                "path": f"{api_prefix}/resolve",
                "endpoint": self.api_resolve,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "解析存储路径",
                "description": "根据媒体类型和标题生成最终保存路径。",
            },
            {
                "path": f"{api_prefix}/probe",
                "endpoint": self.api_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "探测路径是否可写",
                "description": "对目标目录执行一次可写性探测。",
            },
            {
                "path": f"{api_prefix}/upload-probe",
                "endpoint": self.api_upload_probe,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "预检查直传任务",
                "description": "检查是否命中秒传，或者是否允许进入直传链路。",
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
            "get_folder": self.get_folder,
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
                                            "label": "默认 Root Key（可选）",
                                            "placeholder": "仅用于兼容旧路径或指定默认容量查询目标",
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
        message = self._current_last_error() or "插件已就绪，MoviePilot 将在根目录下直接显示 clouddrive-mini 挂载好的云盘。"
        message_type = "error" if self._current_last_error() else "info"
        root_count = len(self._current_last_roots())
        transfer_mode = str(self._current_last_transfer().get("mode", "") or "").strip()
        transfer_text = "最近还没有执行传输预检查。"
        if transfer_mode == "rapid_upload":
            transfer_text = "最近一次传输命中了秒传，无需上传文件内容。"
        elif transfer_mode == "direct_stream":
            provider = str(self._current_last_transfer().get("provider", "") or "").strip() or "unknown"
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
                    "text": f"最近一次读取到 {root_count} 个挂载根目录。",
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
        mounts = self._root_mounts()
        if mounts:
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
                                        {"component": "th", "text": "显示名称"},
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
                                        {"component": "td", "text": mount["name"]},
                                        {"component": "td", "text": str(mount["root"].get("mode", "") or "")},
                                        {"component": "td", "text": str(mount["root"].get("exported_dir", "") or "")},
                                    ],
                                }
                                for mount in mounts[:8]
                            ],
                        },
                    ],
                }
            )
        return rows

    def stop_service(self):
        pass

    def _hydrate_runtime_state(self) -> None:
        getter = getattr(self, "get_config", None)
        if not callable(getter):
            return
        try:
            persisted = getter() or {}
        except Exception:
            return
        if not isinstance(persisted, dict):
            return
        config = normalize_plugin_config(persisted)
        if not any(
            [
                config.get("enabled"),
                config.get("server_url"),
                config.get("username"),
                config.get("password"),
                config.get("root_key"),
            ]
        ):
            return
        self._enabled = bool(config.get("enabled"))
        self._server_url = str(config.get("server_url", "") or "").strip()
        self._username = str(config.get("username", "") or "").strip()
        self._password = str(config.get("password", "") or "").strip()
        self._root_key = str(config.get("root_key", "") or "").strip()
        self.__class__._shared_enabled = self._enabled
        self.__class__._shared_server_url = self._server_url
        self.__class__._shared_username = self._username
        self.__class__._shared_password = self._password
        self.__class__._shared_root_key = self._root_key

    def _effective_enabled(self) -> bool:
        self._hydrate_runtime_state()
        return bool(self._enabled or self.__class__._shared_enabled)

    def _effective_server_url(self) -> str:
        self._hydrate_runtime_state()
        return str(self._server_url or self.__class__._shared_server_url or "").strip()

    def _effective_username(self) -> str:
        self._hydrate_runtime_state()
        return str(self._username or self.__class__._shared_username or "").strip()

    def _effective_password(self) -> str:
        self._hydrate_runtime_state()
        return str(self._password or self.__class__._shared_password or "")

    def _effective_root_key(self) -> str:
        self._hydrate_runtime_state()
        return str(self._root_key or self.__class__._shared_root_key or "").strip()

    def _current_last_error(self) -> str:
        return str(self._last_error or self.__class__._shared_last_error or "").strip()

    def _current_last_roots(self) -> List[Dict[str, Any]]:
        return list(self._last_roots or self.__class__._shared_last_roots or [])

    def _current_last_transfer(self) -> Dict[str, Any]:
        return dict(self._last_transfer or self.__class__._shared_last_transfer or {})

    def _store_last_error(self, value: str) -> None:
        self._last_error = str(value or "").strip()
        self.__class__._shared_last_error = self._last_error

    def _store_last_roots(self, roots: List[Dict[str, Any]]) -> None:
        normalized = list(roots or [])
        self._last_roots = normalized
        self.__class__._shared_last_roots = list(normalized)

    def _store_last_transfer(self, payload: Dict[str, Any]) -> None:
        normalized = dict(payload or {})
        self._last_transfer = normalized
        self.__class__._shared_last_transfer = dict(normalized)

    def _refresh_roots_snapshot(self) -> None:
        if not self._effective_enabled() or not self._effective_server_url():
            return
        try:
            payload = self._client().list_roots()
            self._store_last_error("")
            self._store_last_roots(list(payload.get("roots", []) or []))
        except Exception as exc:
            self._remember_error(exc)

    @eventmanager.register(ChainEventType.StorageOperSelection)
    def storage_oper_selection(self, event: Event) -> None:
        if not self._effective_enabled():
            return
        event_data: StorageOperSelectionEventData = event.event_data
        if getattr(event_data, "storage", "") == self._disk_name:
            event_data.storage_oper = self  # noqa: B010

    def _client(self) -> CloudDriveStorageBridgeClient:
        return CloudDriveStorageBridgeClient(
            server_url=self._effective_server_url(),
            username=self._effective_username(),
            password=self._effective_password(),
            root_key=self._effective_root_key(),
        )

    def _to_storage_sub_path(self, path_value: Any) -> str:
        text = str(path_value or "").strip().replace("\\", "/")
        return text.strip("/")

    def _root_directory_item(self) -> FileItem:
        return FileItem(
            storage=self._disk_name,
            fileid="/",
            parent_fileid=None,
            name=self._disk_name,
            basename=self._disk_name,
            extension=None,
            type="dir",
            path="/",
            size=None,
            modify_time=None,
        )

    def _root_mounts(self) -> List[Dict[str, Any]]:
        if not self._current_last_roots() and self._effective_enabled() and self._effective_server_url():
            self._refresh_roots_snapshot()
        roots = self._current_last_roots()
        seen: Dict[str, int] = {}
        mounts: List[Dict[str, Any]] = []
        for root in roots:
            root_key = str(root.get("root_key", "") or "").strip()
            base_name = str(root.get("account_label", "") or root_key or "CloudDrive").strip() or "CloudDrive"
            seen[base_name] = seen.get(base_name, 0) + 1
            display_name = base_name if seen[base_name] == 1 else f"{base_name} ({seen[base_name]})"
            mounts.append(
                {
                    "name": display_name,
                    "root_key": root_key,
                    "root": root,
                }
            )
        return mounts

    def _mount_item(self, mount: Dict[str, Any]) -> FileItem:
        path = f"/{mount['name']}"
        return FileItem(
            storage=self._disk_name,
            fileid=str(mount.get("root_key", "") or path),
            parent_fileid="/",
            name=str(mount["name"]),
            basename=str(mount["name"]),
            extension=None,
            type="dir",
            path=path,
            size=None,
            modify_time=None,
        )

    def _find_mount_by_root_key(self, root_key: str) -> Dict[str, Any] | None:
        normalized = str(root_key or "").strip()
        if not normalized:
            return None
        for mount in self._root_mounts():
            if str(mount.get("root_key", "") or "").strip() == normalized:
                return mount
        return None

    def _resolve_virtual_path(self, path_value: Any) -> Tuple[str, Dict[str, Any] | None, str]:
        normalized = str(path_value or "/").strip().replace("\\", "/")
        normalized = normalized if normalized.startswith("/") else f"/{normalized}"
        normalized = normalized.rstrip("/") or "/"
        if normalized == "/":
            return "root", None, ""
        stripped = normalized.strip("/")
        parts = stripped.split("/", 1)
        mount_name = parts[0]
        remainder = parts[1] if len(parts) > 1 else ""
        for mount in self._root_mounts():
            if mount["name"] == mount_name:
                if remainder:
                    return "entry", mount, remainder.strip("/")
                return "mount", mount, ""
        fallback_mount = self._find_mount_by_root_key(self._root_key)
        if fallback_mount is None:
            mounts = self._root_mounts()
            if len(mounts) == 1:
                fallback_mount = mounts[0]
        if fallback_mount is not None:
            return "entry", fallback_mount, stripped
        return "missing", None, stripped

    def _to_file_item(self, payload: Dict[str, Any] | None, mount: Dict[str, Any]) -> FileItem | None:
        if not isinstance(payload, dict):
            return None
        storage_path = str(payload.get("storage_path", "") or "/").strip() or "/"
        normalized_storage_path = self._to_storage_sub_path(storage_path)
        if normalized_storage_path:
            path_for_item = f"/{mount['name']}/{normalized_storage_path}"
        else:
            path_for_item = f"/{mount['name']}"
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
            name=name or str(mount["name"]),
            basename=Path(name).stem if name else str(mount["name"]),
            extension=extension,
            type=file_type,
            path=path_for_item,
            size=payload.get("size"),
            modify_time=payload.get("modify_time"),
        )

    def _build_uploaded_file_item(self, mount: Dict[str, Any], uploaded_path: str, local_path: Path) -> FileItem:
        virtual_path = f"/{mount['name']}/{uploaded_path}" if uploaded_path else f"/{mount['name']}"
        filename = Path(virtual_path).name or local_path.name
        suffix = Path(filename).suffix
        extension = suffix[1:] if suffix else None
        parent_path = str(Path(virtual_path).parent).replace("\\", "/")
        if parent_path == ".":
            parent_path = "/"
        try:
            stat = local_path.stat()
            size = int(stat.st_size)
            modify_time = int(stat.st_mtime)
        except Exception:
            size = None
            modify_time = None
        return FileItem(
            storage=self._disk_name,
            fileid=virtual_path,
            parent_fileid=parent_path,
            name=filename,
            basename=Path(filename).stem,
            extension=extension,
            type="file",
            path=virtual_path,
            size=size,
            modify_time=modify_time,
        )

    def _normalize_uploaded_relative_path(self, mount: Dict[str, Any], uploaded_path: str) -> str:
        normalized = self._to_storage_sub_path(uploaded_path)
        mount_name = str(mount.get("name", "") or "").strip().strip("/")
        if not mount_name:
            return normalized
        if normalized == mount_name:
            return ""
        prefix = f"{mount_name}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        return normalized

    def _as_local_path(self, value: str | PathLike[str] | Path) -> Path:
        return Path(value).expanduser()

    def _remember_error(self, error: Exception) -> None:
        self._store_last_error(str(error or "").strip() or "unknown error")

    def api_roots(self, body: Dict[str, Any] | None = None) -> dict:
        try:
            payload = self._client().list_roots()
            self._store_last_error("")
            self._store_last_roots(list(payload.get("roots", []) or []))
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_resolve(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().resolve_storage(body or {})
            self._store_last_error("")
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_probe(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().probe_storage(body or {})
            self._store_last_error("")
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def api_upload_probe(self, body: Dict[str, Any]) -> dict:
        try:
            payload = self._client().upload_probe(body or {})
            self._store_last_error("")
            return payload
        except Exception as exc:
            self._remember_error(exc)
            raise

    def list_files(self, fileitem: FileItem, recursion: bool = False) -> List[FileItem]:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return []
        try:
            path = str(getattr(fileitem, "path", "/") or "/")
            kind, mount, sub_path = self._resolve_virtual_path(path)
            if kind == "root":
                return [self._mount_item(item) for item in self._root_mounts()]
            if kind == "missing" or mount is None:
                return []
            if getattr(fileitem, "type", "") == "file":
                item = self.get_item(fileitem)
                return [item] if item else []
            response = self._client().list_entries(
                {
                    "root_key": mount["root_key"],
                    "sub_path": sub_path,
                }
            )
            items = [self._to_file_item(item, mount) for item in list(response.get("items", []) or [])]
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
        kind, mount, sub_path = self._resolve_virtual_path(getattr(fileitem, "path", "/"))
        if kind in {"root", "missing"} or mount is None:
            return None
        current = self._to_storage_sub_path(sub_path)
        target = "/".join(part for part in [current, str(name or "").strip().strip("/")] if part)
        try:
            response = self._client().mkdir({"root_key": mount["root_key"], "sub_path": target})
            self._last_error = ""
            return self._to_file_item(response.get("item"), mount)
        except Exception as exc:
            self._remember_error(exc)
            return None

    def get_folder(self, fileitem: FileItem | Path, name: str | None = None) -> FileItem | None:
        if isinstance(fileitem, Path):
            target_path = fileitem
            folder_name = str(name or "").strip().strip("/")
            if folder_name:
                target_path = target_path / folder_name
            existing = self.get_file_item(self._disk_name, target_path)
            if existing is not None and getattr(existing, "type", "") == "dir":
                return existing
            parent_path = target_path.parent
            if str(parent_path) == str(target_path):
                return None
            parent_item = self.get_folder(parent_path)
            if parent_item is None:
                return None
            return self.create_folder(parent_item, target_path.name)
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        folder_name = str(name or "").strip().strip("/")
        if not folder_name:
            item = self.get_item(fileitem)
            if item is not None and getattr(item, "type", "") == "dir":
                return item
            path_value = Path(str(getattr(fileitem, "path", "/")) or "/")
            return self.get_folder(path_value)
        base_path = str(getattr(fileitem, "path", "/") or "/").rstrip("/") or "/"
        target_path = Path(f"{base_path}/{folder_name}" if base_path != "/" else f"/{folder_name}")
        return self.get_folder(target_path)

    def upload_file(self, fileitem: FileItem, path: str | PathLike[str] | Path, new_name: str | None = None) -> FileItem | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        kind, mount, sub_path = self._resolve_virtual_path(getattr(fileitem, "path", "/"))
        if kind in {"root", "missing"} or mount is None:
            return None
        local_path = self._as_local_path(path)
        filename = str(new_name or local_path.name).strip()
        payload = {
            "root_key": mount["root_key"],
            "sub_path": self._to_storage_sub_path(sub_path),
            "filename": filename,
        }
        try:
            response = self.transfer_local_file(str(local_path), payload=payload, run_probe=True)
            self._last_error = ""
            uploaded_path = "/".join(part for part in [payload["sub_path"], filename] if part)
            uploaded_path = self._normalize_uploaded_relative_path(mount, uploaded_path)
            if not response:
                return None
            virtual_path = f"/{mount['name']}/{uploaded_path}" if uploaded_path else f"/{mount['name']}"
            uploaded_item = self.get_file_item(self._disk_name, Path(virtual_path))
            if uploaded_item is not None:
                return uploaded_item
            return self._build_uploaded_file_item(mount, uploaded_path, local_path)
        except Exception as exc:
            self._remember_error(exc)
            return None

    def upload(self, fileitem: FileItem, path: str | PathLike[str] | Path, new_name: str | None = None) -> FileItem | None:
        return self.upload_file(fileitem, path, new_name=new_name)

    def delete_file(self, fileitem: FileItem) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        kind, mount, sub_path = self._resolve_virtual_path(getattr(fileitem, "path", "/"))
        if kind in {"root", "mount", "missing"} or mount is None:
            return False
        try:
            response = self._client().delete_entry({"root_key": mount["root_key"], "sub_path": sub_path})
            self._last_error = ""
            return bool(response.get("deleted"))
        except Exception as exc:
            self._remember_error(exc)
            return False

    def delete(self, fileitem: FileItem) -> bool | None:
        return self.delete_file(fileitem)

    def rename_file(self, fileitem: FileItem, name: str) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        kind, mount, sub_path = self._resolve_virtual_path(getattr(fileitem, "path", "/"))
        if kind in {"root", "mount", "missing"} or mount is None:
            return False
        try:
            response = self._client().rename_entry(
                {
                    "root_key": mount["root_key"],
                    "sub_path": sub_path,
                    "new_name": str(name or "").strip(),
                }
            )
            self._last_error = ""
            return bool(response.get("renamed"))
        except Exception as exc:
            self._remember_error(exc)
            return False

    def rename(self, fileitem: FileItem, name: str) -> bool | None:
        return self.rename_file(fileitem, name)

    def exists(self, fileitem: FileItem) -> bool | None:
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        return self.get_item(fileitem) is not None

    def get_item(self, fileitem: FileItem | Path) -> FileItem | None:
        if isinstance(fileitem, Path):
            return self.get_file_item(self._disk_name, fileitem)
        if getattr(fileitem, "storage", "") != self._disk_name:
            return None
        return self.get_file_item(self._disk_name, Path(str(getattr(fileitem, "path", "/")) or "/"))

    def get_file_item(self, storage: str, path: Path) -> FileItem | None:
        if storage != self._disk_name:
            return None
        kind, mount, sub_path = self._resolve_virtual_path(path.as_posix())
        if kind == "root":
            return self._root_directory_item()
        if kind == "mount" and mount is not None:
            return self._mount_item(mount)
        if kind == "missing" or mount is None:
            return None
        try:
            response = self._client().get_item({"root_key": mount["root_key"], "sub_path": sub_path})
            self._last_error = ""
            return self._to_file_item(response.get("item"), mount)
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
        mount = self._find_mount_by_root_key(self._effective_root_key())
        if mount is None:
            mounts = self._root_mounts()
            if not mounts:
                return None
            mount = mounts[0]
        try:
            response = self._client().usage({"root_key": mount["root_key"]})
            self._store_last_error("")
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
                    self._store_last_error("")
                    self._store_last_transfer({
                        "mode": "rapid_upload",
                        "payload": probe_payload,
                    })
                    return {
                        "status": "ok",
                        "transfer_strategy": "rapid_upload",
                        "probe": probe_payload,
                    }
            response = client.stream_upload(stream, file_size=file_size, payload=merged_payload)
            self._store_last_error("")
            self._store_last_transfer({
                "mode": "direct_stream",
                "provider": str(response.get("provider", "") or "").strip(),
                "payload": response,
            })
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
