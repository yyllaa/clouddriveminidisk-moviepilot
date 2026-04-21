from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.helper.storage import StorageHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import FileItem, StorageOperSelectionEventData, StorageUsage
from app.schemas.types import ChainEventType

from .clouddrive_mini_api import CloudDriveMiniApi
from .version import VERSION


class CloudDriveMiniDisk(_PluginBase):
    plugin_name = "CloudDrive Mini存储"
    plugin_desc = "使用 clouddrive-mini 项目 HTTP API 作为 MoviePilot 自定义存储。"
    plugin_icon = "Cloudrive_A.png"
    plugin_version = VERSION
    plugin_author = "zztt"
    author_url = "https://github.com/yyllaa"
    plugin_config_prefix = "clouddriveminidisk_"
    plugin_order = 99
    auth_level = 1

    _enabled = False
    _disk_name = "CloudDriveMini存储"
    _api: Optional[CloudDriveMiniApi] = None
    _https = False
    _host = "127.0.0.1"
    _port = "8765"
    _base_url = "http://127.0.0.1:8765"
    _username = ""
    _password = ""
    _account_id = ""
    _mode = "personal"
    _root_path = "/"
    _timeout = 60
    _upload_chunk_size_mb = 4

    def init_plugin(self, config: Optional[Dict] = None) -> None:
        config = config or {}
        storage_helper = StorageHelper()
        try:
            storages = storage_helper.get_storagies()
        except Exception:
            storages = []
        if not any(s.type == self._disk_name and s.name == self._disk_name for s in storages):
            try:
                storage_helper.add_storage(storage=self._disk_name, name=self._disk_name, conf={})
            except Exception as error:
                logger.warning("【CloudDriveMiniDisk】注册存储失败: %s", error)

        self._enabled = bool(config.get("enabled", False))
        if self._api:
            self._api.close()
            self._api = None

        self._https = bool(config.get("https", False))
        scheme = "https" if self._https else "http"
        host = str(config.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        port = str(config.get("port") or "8765").strip() or "8765"
        self._host = host
        self._port = port
        base_url = f"{scheme}://{host}:{port}"
        self._base_url = base_url
        self._username = str(config.get("username") or "").strip()
        self._password = str(config.get("password") or "")
        self._account_id = str(config.get("account_id") or "").strip()
        self._mode = str(config.get("mode") or "personal").strip() or "personal"
        self._root_path = str(config.get("root_path") or "/").strip() or "/"
        self._timeout = int(config.get("timeout") or 60)
        self._upload_chunk_size_mb = int(config.get("upload_chunk_size_mb") or 4)
        if not self._enabled:
            return

        try:
            self._api = CloudDriveMiniApi(
                base_url=base_url,
                disk_name=self._disk_name,
                account_id=self._account_id,
                mode=self._mode,
                root_path=self._root_path,
                username=self._username,
                password=self._password,
                timeout=self._timeout,
                upload_chunk_size_mb=self._upload_chunk_size_mb,
            )
            # Fail fast so invalid地址/认证配置能在保存后立刻暴露。
            self._api._ensure_auth()
        except Exception as error:
            logger.error("【CloudDriveMiniDisk】初始化失败: %s", error)
            if self._api:
                self._api.close()
            self._api = None
            self._enabled = False

    def get_state(self) -> bool:
        return self._enabled and self._api is not None

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def _detect_account_items(self) -> Tuple[List[Dict[str, str]], str]:
        detector: Optional[CloudDriveMiniApi] = None
        try:
            detector = CloudDriveMiniApi(
                base_url=self._base_url,
                disk_name=self._disk_name,
                username=self._username,
                password=self._password,
                timeout=self._timeout,
                upload_chunk_size_mb=self._upload_chunk_size_mb,
            )
            snapshot = detector.list_accounts()
        except Exception as error:
            logger.warning("【CloudDriveMiniDisk】自动侦测账号失败: %s", error)
            return [], str(error)
        finally:
            if detector:
                detector.close()

        active_account_id = str(snapshot.get("active_account_id") or "").strip()
        items: List[Dict[str, str]] = []
        seen: set[str] = set()
        for account in snapshot.get("accounts", []):
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("account_id") or "").strip()
            if not account_id or account_id in seen:
                continue
            seen.add(account_id)
            display_name = str(account.get("display_name") or account_id).strip() or account_id
            provider = str(account.get("provider") or "").strip()
            title = f"{display_name}（{account_id}）" if display_name != account_id else account_id
            if provider:
                title = f"{title} - {provider}"
            if account_id == active_account_id:
                title = f"{title} - 当前活动账号"
            items.append({"title": title, "value": account_id})

        if self._account_id and self._account_id not in seen:
            items.insert(0, {"title": f"{self._account_id}（当前配置）", "value": self._account_id})

        return items, ""

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        account_items, account_detect_error = self._detect_account_items()
        account_select_hint = (
            f"自动侦测失败：{account_detect_error}"
            if account_detect_error
            else (
                "已从 clouddrive-mini 自动读取账号列表"
                if account_items
                else "未检测到账号；请确认服务地址和登录信息，保存后重新打开配置页"
            )
        )
        alert_content = [
            {"component": "div", "text": "说明：这是基于 clouddrive-mini HTTP API 的存储插件。"},
            {"component": "div", "text": "已支持：浏览、详情、建目录、删除、重命名、下载、分片上传、复制、移动。"},
            {"component": "div", "text": "可以先手填 account_id，也可以直接从“自动侦测账号”里选择。"},
            {"component": "div", "text": "如果项目启用了登录认证，请先填写用户名和密码并保存后再重新打开配置页。"},
        ]
        if account_detect_error:
            alert_content.append({"component": "div", "text": f"自动侦测失败：{account_detect_error}"})

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "https", "label": "使用 HTTPS"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "host",
                                            "label": "服务地址",
                                            "hint": "clouddrive-mini 的 HTTP 地址，不带 http(s)",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "port",
                                            "label": "端口",
                                            "hint": "默认 8765",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "account_id",
                                            "label": "账号 ID",
                                            "hint": "留空时使用当前活动账号",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "account_id",
                                            "label": "自动侦测账号",
                                            "items": account_items,
                                            "clearable": True,
                                            "hint": account_select_hint,
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "mode",
                                            "label": "存储模式",
                                            "items": [
                                                {"title": "个人云", "value": "personal"},
                                                {"title": "家庭云", "value": "family"},
                                            ],
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "root_path",
                                            "label": "根路径",
                                            "hint": "插件看到的 / 会映射到这里",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "username",
                                            "label": "项目用户名",
                                            "hint": "仅当 clouddrive-mini 启用了项目登录时需要",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "password",
                                            "label": "项目密码",
                                            "type": "{{ 'password' }}",
                                            "hint": "仅当 clouddrive-mini 启用了项目登录时需要",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "timeout",
                                            "label": "请求超时（秒）",
                                            "type": "number",
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
                                            "model": "upload_chunk_size_mb",
                                            "label": "上传分片大小（MB）",
                                            "type": "number",
                                            "hint": "通过任务分片上传，建议 4",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "density": "compact",
                                            "class": "mt-2",
                                        },
                                        "content": alert_content,
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "https": False,
            "host": "127.0.0.1",
            "port": "8765",
            "account_id": "",
            "mode": "personal",
            "root_path": "/",
            "username": "",
            "password": "",
            "timeout": 60,
            "upload_chunk_size_mb": 4,
        }

    def get_page(self) -> List[dict]:
        status_text = "已连接" if self.get_state() else ("已启用但未连接" if self._enabled else "未启用")
        status_type = "success" if self.get_state() else ("warning" if self._enabled else "info")
        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VAlert",
                                "props": {
                                    "type": status_type,
                                    "variant": "tonal",
                                    "density": "compact",
                                },
                                "text": f"当前状态：{status_text}",
                            }
                        ],
                    }
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {"component": "VCardTitle", "text": "连接信息"},
                                    {
                                        "component": "VCardText",
                                        "text": f"服务地址：{self._base_url}\n账号 ID：{self._account_id or '当前活动账号'}\n模式：{'家庭云' if self._mode == 'family' else '个人云'}\n根路径：{self._root_path}",
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {"component": "VCardTitle", "text": "传输设置"},
                                    {
                                        "component": "VCardText",
                                        "text": f"请求超时：{self._timeout} 秒\n上传分片：{self._upload_chunk_size_mb} MB\n支持操作：浏览、详情、建目录、删除、重命名、下载、上传、复制、移动",
                                    },
                                ],
                            }
                        ],
                    },
                ],
            },
        ]

    def get_module(self) -> Dict[str, Any]:
        return {
            "list_files": self.list_files,
            "any_files": self.any_files,
            "download_file": self.download_file,
            "upload_file": self.upload_file,
            "delete_file": self.delete_file,
            "rename_file": self.rename_file,
            "copy_file": self.copy_file,
            "move_file": self.move_file,
            "get_file_item": self.get_file_item,
            "get_parent_item": self.get_parent_item,
            "snapshot_storage": self.snapshot_storage,
            "storage_usage": self.storage_usage,
            "support_transtype": self.support_transtype,
            "create_folder": self.create_folder,
            "exists": self.exists,
            "get_item": self.get_item,
        }

    @eventmanager.register(ChainEventType.StorageOperSelection)
    def storage_oper_selection(self, event: Event) -> None:
        if not self._enabled or not self._api:
            return
        event_data: StorageOperSelectionEventData = event.event_data
        if event_data.storage == self._disk_name:
            event_data.storage_oper = self._api

    def list_files(self, fileitem: FileItem, recursion: bool = False) -> Optional[List[FileItem]]:
        if fileitem.storage != self._disk_name or not self._api:
            return []
        if recursion:
            result = self._api.iter_files(fileitem)
            if result is not None:
                return result
        return self._api.list(fileitem)

    def any_files(self, fileitem: FileItem, extensions: Optional[list] = None) -> Optional[bool]:
        if fileitem.storage != self._disk_name or not self._api:
            return None

        def walk(item: FileItem) -> bool:
            for child in self._api.list(item):
                if child.type == "file":
                    if not extensions:
                        return True
                    if child.extension and f".{child.extension.lower()}" in extensions:
                        return True
                elif walk(child):
                    return True
            return False

        return walk(fileitem)

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.create_folder(fileitem, name)

    def download_file(self, fileitem: FileItem, path: Optional[Path] = None) -> Optional[Path]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.download(fileitem, path)

    def upload_file(self, fileitem: FileItem, path: Path, new_name: Optional[str] = None) -> Optional[FileItem]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.upload(fileitem, path, new_name)

    def delete_file(self, fileitem: FileItem) -> Optional[bool]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.delete(fileitem)

    def rename_file(self, fileitem: FileItem, name: str) -> Optional[bool]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.rename(fileitem, name)

    def copy_file(self, fileitem: FileItem, path: Path, new_name: str) -> Optional[bool]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.copy(fileitem, path, new_name)

    def move_file(self, fileitem: FileItem, path: Path, new_name: str) -> Optional[bool]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.move(fileitem, path, new_name)

    def exists(self, fileitem: FileItem) -> Optional[bool]:
        if fileitem.storage != self._disk_name:
            return None
        return True if self.get_item(fileitem) else False

    def get_item(self, fileitem: FileItem) -> Optional[FileItem]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self.get_file_item(fileitem.storage, Path(fileitem.path))

    def get_file_item(self, storage: str, path: Path) -> Optional[FileItem]:
        if storage != self._disk_name or not self._api:
            return None
        return self._api.get_item(path)

    def get_parent_item(self, fileitem: FileItem) -> Optional[FileItem]:
        if fileitem.storage != self._disk_name or not self._api:
            return None
        return self._api.get_parent(fileitem)

    def snapshot_storage(
        self,
        storage: str,
        path: Path,
        last_snapshot_time: Optional[float] = None,
        max_depth: int = 5,
    ) -> Optional[Dict[str, Dict]]:
        if storage != self._disk_name or not self._api:
            return None
        return self._api.snapshot_storage(
            storage,
            path,
            last_snapshot_time=last_snapshot_time,
            max_depth=max_depth,
        )

    def storage_usage(self, storage: str) -> Optional[StorageUsage]:
        if storage != self._disk_name or not self._api:
            return None
        return self._api.usage()

    def support_transtype(self, storage: str) -> Optional[dict]:
        if storage != self._disk_name or not self._api:
            return None
        return self._api.support_transtype()

    def stop_service(self) -> None:
        if self._api:
            try:
                self._api.close()
            except Exception as error:
                logger.debug("【CloudDriveMiniDisk】关闭会话失败: %s", error)
        self._api = None
