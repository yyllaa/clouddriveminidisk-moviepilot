from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath
from time import sleep, time
from typing import Any, Dict, List, Optional

import requests

from app.core.config import global_vars, settings
from app.log import logger
from app.modules.filemanager.storages import transfer_process
from app.schemas import FileItem, StorageUsage


class CloudDriveMiniError(RuntimeError):
    pass


def _normalize_posix_path(path_text: str | Path | None) -> str:
    raw = str(path_text or "").replace("\\", "/").strip()
    if not raw or raw == ".":
        return "/"
    parts = [part for part in raw.split("/") if part and part != "."]
    return "/" + "/".join(parts) if parts else "/"


def _to_timestamp(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


class CloudDriveMiniApi:
    def __init__(
        self,
        *,
        base_url: str,
        disk_name: str,
        account_id: str = "",
        mode: str = "personal",
        root_path: str = "/",
        username: str = "",
        password: str = "",
        timeout: int = 60,
        upload_chunk_size_mb: int = 4,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.disk_name = str(disk_name or "").strip() or "CloudDrive Mini存储"
        self.account_id = str(account_id or "").strip()
        self.mode = "family" if str(mode or "").strip().lower() == "family" else "personal"
        self.root_path = _normalize_posix_path(root_path or "/")
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.timeout = max(10, int(timeout or 60))
        self.upload_chunk_size = max(256 * 1024, min(int(upload_chunk_size_mb or 4) * 1024 * 1024, 8 * 1024 * 1024))
        self.session = requests.Session()
        self._auth_checked = False
        self._auth_enabled = False
        self.transtype = {"move": "移动", "copy": "复制"}

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _endpoint(self, action: str) -> str:
        suffix = str(action or "").strip("/")
        prefix = "/api/family" if self.mode == "family" else "/api/files"
        return f"{prefix}/{suffix}" if suffix else prefix

    def _root_item(self) -> FileItem:
        return FileItem(
            storage=self.disk_name,
            fileid=self.root_path,
            parent_fileid="",
            name=self.disk_name,
            basename=self.disk_name,
            extension=None,
            type="dir",
            path="/",
            size=None,
            modify_time=None,
        )

    def _visible_path(self, remote_path: str) -> str:
        normalized = _normalize_posix_path(remote_path)
        if self.root_path == "/":
            return normalized
        root_prefix = self.root_path.rstrip("/")
        if normalized == root_prefix:
            return "/"
        if normalized.startswith(f"{root_prefix}/"):
            suffix = normalized[len(root_prefix) :]
            return suffix if suffix.startswith("/") else f"/{suffix}"
        return normalized

    def _remote_path(self, visible_path: str | Path | None) -> str:
        normalized = _normalize_posix_path(visible_path)
        if self.root_path == "/":
            return normalized
        if normalized == "/":
            return self.root_path
        return f"{self.root_path.rstrip('/')}{normalized}"

    def _item_from_remote(
        self,
        path_text: str,
        raw: dict[str, Any],
        *,
        parent_fileid: str = "",
    ) -> FileItem:
        remote_path = _normalize_posix_path(path_text or raw.get("path") or "/")
        visible_path = self._visible_path(remote_path)
        is_dir = bool(raw.get("is_folder", False))
        if is_dir and visible_path != "/" and not visible_path.endswith("/"):
            visible_path = f"{visible_path}/"
        name = str(raw.get("name") or (self.disk_name if visible_path == "/" else PurePosixPath(remote_path).name) or "")
        basename = name if is_dir else (Path(name).stem or name)
        extension = None if is_dir else (Path(name).suffix.lstrip(".") or None)
        return FileItem(
            storage=self.disk_name,
            fileid=str(raw.get("fid") or raw.get("id") or remote_path),
            parent_fileid=parent_fileid or str(raw.get("parent_fileid") or ""),
            name=name,
            basename=basename,
            extension=extension,
            type="dir" if is_dir else "file",
            path=visible_path,
            size=None if is_dir else int(raw.get("size") or 0),
            modify_time=_to_timestamp(raw.get("updated_at") or raw.get("modify_time") or raw.get("created_at")),
        )

    def _ensure_auth(self, *, force: bool = False) -> None:
        if self._auth_checked and not force:
            return
        status_url = f"{self.base_url}/api/auth/status"
        response = self.session.get(status_url, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        self._auth_enabled = bool(data.get("enabled"))
        self._auth_checked = True
        if not self._auth_enabled or bool(data.get("authenticated")):
            return
        if not self.username or not self.password:
            raise CloudDriveMiniError("CloudDrive Mini 已启用项目登录，请在插件配置中填写用户名和密码")
        login_url = f"{self.base_url}/api/auth/login"
        login_response = self.session.post(
            login_url,
            json={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        login_response.raise_for_status()
        login_data = login_response.json()
        if str(login_data.get("status", "") or "").strip().lower() == "error":
            raise CloudDriveMiniError(str(login_data.get("message") or "CloudDrive Mini 登录失败"))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if self.account_id:
            request_params.setdefault("account_id", self.account_id)
        request_payload = dict(payload or {})
        if request_payload and self.account_id:
            request_payload.setdefault("account_id", self.account_id)
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            params=request_params,
            json=request_payload or None,
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            self._ensure_auth(force=True)
            return self._request_json(method, path, params=params, payload=payload, retry_auth=False)
        response.raise_for_status()
        data = response.json()
        if str(data.get("status", "") or "").strip().lower() == "error":
            raise CloudDriveMiniError(str(data.get("message") or f"{path} failed"))
        return data

    def _request_stream(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> requests.Response:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if self.account_id:
            request_params.setdefault("account_id", self.account_id)
        response = self.session.get(
            f"{self.base_url}{path}",
            params=request_params,
            timeout=self.timeout,
            stream=True,
        )
        if response.status_code == 401 and retry_auth:
            response.close()
            self._ensure_auth(force=True)
            return self._request_stream(path, params=params, retry_auth=False)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                data = response.json()
            finally:
                response.close()
            if str(data.get("status", "") or "").strip().lower() == "error":
                raise CloudDriveMiniError(str(data.get("message") or f"{path} failed"))
            raise CloudDriveMiniError(f"{path} returned JSON instead of file stream")
        return response

    def _request_binary(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: bytes,
        headers: Optional[Dict[str, str]] = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if self.account_id:
            request_params.setdefault("account_id", self.account_id)
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            params=request_params,
            headers=headers or {},
            data=data,
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            self._ensure_auth(force=True)
            return self._request_binary(method, path, params=params, data=data, headers=headers, retry_auth=False)
        response.raise_for_status()
        result = response.json()
        if str(result.get("status", "") or "").strip().lower() == "error":
            raise CloudDriveMiniError(str(result.get("message") or f"{path} failed"))
        return result

    def _detail(self, remote_path: str) -> Optional[dict[str, Any]]:
        endpoint = self._endpoint("detail")
        try:
            data = self._request_json("GET", endpoint, params={"path": remote_path})
        except CloudDriveMiniError as error:
            if "not found" in str(error).lower():
                return None
            raise
        detail = data.get("detail", {})
        return dict(detail) if isinstance(detail, dict) else None

    def _list_all(self, remote_path: str) -> List[dict[str, Any]]:
        endpoint = self._endpoint("")
        items: List[dict[str, Any]] = []
        page_cursor = ""
        while True:
            params = {"path": remote_path, "page_size": 100}
            if page_cursor:
                params["page_cursor"] = page_cursor
            data = self._request_json("GET", endpoint, params=params)
            current_items = data.get("items", [])
            if isinstance(current_items, list):
                items.extend([dict(item) for item in current_items if isinstance(item, dict)])
            page_cursor = str(data.get("next_page_cursor") or "").strip()
            if not page_cursor:
                break
        return items

    def list(self, fileitem: FileItem) -> List[FileItem]:
        if fileitem.type == "file":
            current = self.get_item(Path(fileitem.path))
            return [current] if current else []
        remote_dir = self._remote_path(fileitem.path)
        items: List[FileItem] = []
        for item in self._list_all(remote_dir):
            item_name = str(item.get("name") or "").strip()
            child_remote = _normalize_posix_path(item.get("path") or f"{remote_dir.rstrip('/')}/{item_name}" if remote_dir != "/" else f"/{item_name}")
            items.append(self._item_from_remote(child_remote, item, parent_fileid=fileitem.fileid))
        return items

    def iter_files(self, fileitem: FileItem) -> Optional[List[FileItem]]:
        if fileitem.type == "file":
            current = self.get_item(Path(fileitem.path))
            return [current] if current else []
        items: List[FileItem] = []
        try:
            for child in self.list(fileitem):
                if child.type == "dir":
                    sub = self.iter_files(child)
                    if sub:
                        items.extend(sub)
                else:
                    items.append(child)
            return items
        except Exception as error:
            logger.error("【CloudDriveMini】递归列目录失败 %s: %s", fileitem.path, error)
            return None

    def get_item(self, path: Path) -> Optional[FileItem]:
        visible_path = _normalize_posix_path(path.as_posix())
        if visible_path == "/":
            return self._root_item()
        remote_path = self._remote_path(visible_path)
        detail = self._detail(remote_path)
        if not detail:
            return None
        actual_remote_path = _normalize_posix_path(detail.get("path") or remote_path)
        return self._item_from_remote(actual_remote_path, detail)

    def get_parent(self, fileitem: FileItem) -> Optional[FileItem]:
        parent_path = PurePosixPath(_normalize_posix_path(fileitem.path)).parent.as_posix() or "/"
        return self.get_item(Path(parent_path))

    def create_folder(self, fileitem: FileItem, name: str) -> Optional[FileItem]:
        parent_path = self._remote_path(fileitem.path)
        data = self._request_json("POST", self._endpoint("mkdir"), payload={"parent_path": parent_path, "name": name})
        created_path = _normalize_posix_path(data.get("path") or f"{parent_path.rstrip('/')}/{name}" if parent_path != "/" else f"/{name}")
        return self.get_item(Path(self._visible_path(created_path)))

    def get_folder(self, path: Path) -> Optional[FileItem]:
        visible_path = _normalize_posix_path(path.as_posix())
        if visible_path == "/":
            return self._root_item()
        existing = self.get_item(Path(visible_path))
        if existing:
            return existing
        current = self._root_item()
        for part in [part for part in visible_path.split("/") if part]:
            next_visible = _normalize_posix_path(f"{current.path.rstrip('/')}/{part}" if current.path != "/" else f"/{part}")
            child = self.get_item(Path(next_visible))
            if child is None:
                child = self.create_folder(current, part)
            if child is None:
                return None
            current = child
        return current

    def detail(self, fileitem: FileItem) -> Optional[FileItem]:
        return self.get_item(Path(fileitem.path))

    def delete(self, fileitem: FileItem) -> bool:
        remote_path = self._remote_path(fileitem.path)
        self._request_json("POST", self._endpoint("delete"), payload={"path": remote_path})
        return True

    def rename(self, fileitem: FileItem, name: str) -> bool:
        remote_path = self._remote_path(fileitem.path)
        self._request_json("POST", self._endpoint("rename"), payload={"path": remote_path, "new_name": name})
        return True

    def _copied_or_moved_visible_path(
        self,
        source_item: FileItem,
        target_dir: FileItem,
    ) -> str:
        target_dir_path = _normalize_posix_path(target_dir.path)
        target_name = str(source_item.name or "").strip()
        if target_dir_path == "/":
            return f"/{target_name}"
        return f"{target_dir_path.rstrip('/')}/{target_name}"

    def copy(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        remote_path = self._remote_path(fileitem.path)
        target_dir = self.get_folder(path)
        if not target_dir:
            return False
        target_remote = self._remote_path(target_dir.path)
        result = self._request_json("POST", self._endpoint("copy"), payload={"src_path": remote_path, "dst_dir_path": target_remote})
        result_path = str(result.get("path") or "").strip()
        target_item = self.get_item(
            Path(self._visible_path(result_path) if result_path else self._copied_or_moved_visible_path(fileitem, target_dir))
        )
        if new_name and new_name != fileitem.name:
            if not target_item:
                return False
            return self.rename(target_item, new_name)
        return True

    def move(self, fileitem: FileItem, path: Path, new_name: str) -> bool:
        remote_path = self._remote_path(fileitem.path)
        target_dir = self.get_folder(path)
        if not target_dir:
            return False
        target_remote = self._remote_path(target_dir.path)
        result = self._request_json("POST", self._endpoint("move"), payload={"src_path": remote_path, "dst_dir_path": target_remote})
        result_path = str(result.get("path") or "").strip()
        target_item = self.get_item(
            Path(self._visible_path(result_path) if result_path else self._copied_or_moved_visible_path(fileitem, target_dir))
        )
        if new_name and new_name != fileitem.name:
            if not target_item:
                return False
            return self.rename(target_item, new_name)
        return True

    def download(self, fileitem: FileItem, path: Optional[Path] = None) -> Optional[Path]:
        if fileitem.type != "file":
            return None
        target_dir = Path(path) if path else Path(settings.TEMP_PATH)
        target_dir.mkdir(parents=True, exist_ok=True)
        local_path = target_dir / fileitem.name
        response = self._request_stream(self._endpoint("download"), params={"path": self._remote_path(fileitem.path)})
        progress_callback = transfer_process(Path(fileitem.path).as_posix())
        total_bytes = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        try:
            with local_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=10 * 1024 * 1024):
                    if global_vars.is_transfer_stopped(fileitem.path):
                        response.close()
                        return None
                    if not chunk:
                        continue
                    file_obj.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        progress_callback((downloaded * 100) / total_bytes)
            progress_callback(100)
            return local_path
        except Exception as error:
            logger.error("【CloudDriveMini】下载失败 %s: %s", fileitem.path, error)
            if local_path.exists():
                local_path.unlink(missing_ok=True)
            return None
        finally:
            response.close()

    def _create_upload_task(self, filename: str, file_size: int, remote_dir_path: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/api/tasks/upload/create",
            payload={
                "filename": filename,
                "file_size": file_size,
                "remote_dir_path": remote_dir_path,
                "mode": self.mode,
                "chunk_size": self.upload_chunk_size,
            },
        )

    def _upload_task_detail(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", "/api/tasks/detail", params={"task_id": task_id})

    def _upload_chunk(self, task_id: str, chunk_index: int, chunk_count: int, chunk_size: int, body: bytes) -> dict[str, Any]:
        return self._request_binary(
            "POST",
            "/api/tasks/upload/chunk",
            params={
                "task_id": task_id,
                "chunk_index": chunk_index,
                "chunk_count": chunk_count,
                "chunk_size": chunk_size,
            },
            data=body,
            headers={"Content-Type": "application/octet-stream"},
        )

    def _wait_upload_complete(self, task_id: str, *, timeout_seconds: int = 3600) -> dict[str, Any]:
        deadline = time() + timeout_seconds
        while time() < deadline:
            task = self._upload_task_detail(task_id)
            status = str(task.get("status") or "").strip().lower()
            if status == "success":
                return task
            if status in {"error", "partial", "cancelled"}:
                raise CloudDriveMiniError(str(task.get("error_message") or f"upload task failed: {status}"))
            sleep(1)
        raise CloudDriveMiniError("upload task timeout")

    def upload(self, fileitem: FileItem, path: Path, new_name: Optional[str] = None) -> Optional[FileItem]:
        target_dir = fileitem if fileitem.type == "dir" else self.get_parent(fileitem)
        if not target_dir:
            return None
        remote_dir_path = self._remote_path(target_dir.path)
        local_path = Path(path)
        if not local_path.exists() or not local_path.is_file():
            raise CloudDriveMiniError(f"local file not found: {local_path}")
        target_name = str(new_name or local_path.name).strip() or local_path.name
        task = self._create_upload_task(target_name, int(local_path.stat().st_size or 0), remote_dir_path)
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            raise CloudDriveMiniError("upload task_id is empty")
        detail = task.get("detail", {}) if isinstance(task.get("detail"), dict) else {}
        chunk_size = max(256 * 1024, int(detail.get("chunk_size") or self.upload_chunk_size))
        total_chunks = max(1, int(detail.get("total_chunks") or ((local_path.stat().st_size + chunk_size - 1) // chunk_size)))
        uploaded_chunks = {
            int(value)
            for value in (task.get("uploaded_chunks") or [])
            if str(value).isdigit()
        }
        target_marker = PurePosixPath(target_dir.path).joinpath(target_name).as_posix()
        progress_callback = transfer_process(target_marker)
        uploaded_bytes = sum(min(chunk_size, max(0, int(local_path.stat().st_size) - index * chunk_size)) for index in uploaded_chunks)
        if local_path.stat().st_size > 0:
            progress_callback((uploaded_bytes * 100) / int(local_path.stat().st_size))
        with local_path.open("rb") as file_obj:
            for chunk_index in range(total_chunks):
                if chunk_index in uploaded_chunks:
                    continue
                if global_vars.is_transfer_stopped(target_marker):
                    return None
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                self._upload_chunk(task_id, chunk_index, total_chunks, chunk_size, chunk)
                uploaded_bytes += len(chunk)
                if local_path.stat().st_size > 0:
                    progress_callback((uploaded_bytes * 100) / int(local_path.stat().st_size))
        task_state = self._wait_upload_complete(task_id)
        progress_callback(100)
        result = task_state.get("result", {}) if isinstance(task_state.get("result"), dict) else {}
        detail = task_state.get("detail", {}) if isinstance(task_state.get("detail"), dict) else {}
        actual_target_path = str(
            result.get("path")
            or detail.get("target_path")
            or f"{remote_dir_path.rstrip('/')}/{detail.get('target_name') or target_name}"
        ).strip()
        return self.get_item(Path(self._visible_path(actual_target_path)))

    def snapshot_storage(
        self,
        storage: str,
        path: Path,
        last_snapshot_time: Optional[float] = None,
        max_depth: int = 5,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        if storage != self.disk_name:
            return None
        result: Dict[str, Dict[str, Any]] = {}
        root = self.get_item(path)
        if not root:
            return {}

        def walk(item: FileItem, depth: int = 0) -> None:
            if item.type == "dir":
                if depth >= max_depth:
                    return
                for child in self.list(item):
                    walk(child, depth + 1)
                return
            modify_time = getattr(item, "modify_time", 0) or 0
            if last_snapshot_time and modify_time and modify_time <= last_snapshot_time:
                return
            result[item.path] = {
                "size": item.size or 0,
                "modify_time": modify_time,
                "type": item.type,
            }

        walk(root)
        return result

    def usage(self) -> Optional[StorageUsage]:
        if not self.account_id:
            return None
        try:
            data = self._request_json(
                "GET",
                "/api/accounts/overview",
                params={"account_id": self.account_id, "background_refresh": "1"},
            )
        except Exception as error:
            logger.warning("【CloudDriveMini】读取容量信息失败: %s", error)
            return None
        account = data.get("account", {}) if isinstance(data.get("account"), dict) else {}
        usage_payload = (
            account.get("family_storage", {}) if self.mode == "family" else account.get("storage", {})
        )
        if not isinstance(usage_payload, dict):
            return None
        try:
            total = int(
                usage_payload.get("total_bytes")
                or usage_payload.get("total")
                or usage_payload.get("total_size")
                or 0
            )
        except (TypeError, ValueError):
            total = 0
        try:
            available = int(
                usage_payload.get("free_bytes")
                or usage_payload.get("available")
                or usage_payload.get("available_bytes")
                or 0
            )
        except (TypeError, ValueError):
            available = 0
        if total <= 0 and available <= 0:
            return None
        return StorageUsage(total=total, available=available)

    def support_transtype(self) -> dict:
        return dict(self.transtype)
