from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
from time import sleep, time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from app.core.config import global_vars, settings
from app.log import logger
from app.schemas import FileItem, StorageUsage


class CloudDriveMiniError(RuntimeError):
    pass


class _ProgressFileReader:
    def __init__(self, file_obj: Any, total_bytes: int, progress_callback: Any) -> None:
        self._file_obj = file_obj
        self._total_bytes = max(0, int(total_bytes or 0))
        self._progress_callback = progress_callback
        self._uploaded_bytes = 0

    def __len__(self) -> int:
        return self._total_bytes

    def read(self, size: int = -1) -> bytes:
        chunk = self._file_obj.read(size)
        if chunk:
            self._uploaded_bytes += len(chunk)
            if self._total_bytes > 0 and self._progress_callback is not None:
                self._progress_callback(min((self._uploaded_bytes * 100) / self._total_bytes, 100))
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file_obj.seek(offset, whence)

    def tell(self) -> int:
        return self._file_obj.tell()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._file_obj, item)


def _noop_progress_callback(_progress: float) -> None:
    return None


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

    def _stringify_log_value(self, value: Any, *, limit: int = 600) -> str:
        if isinstance(value, (dict, list, tuple)):
            try:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                text = repr(value)
        else:
            text = str(value)
        if len(text) > limit:
            return f"{text[:limit]}...(truncated)"
        return text

    def _upload_log(self, level: str, message: str, **fields: Any) -> None:
        logger_fn = getattr(logger, level, logger.info)
        if not fields:
            logger_fn("【CloudDriveMiniUpload】%s", message)
            return
        rendered = ", ".join(
            f"{key}={self._stringify_log_value(value)}"
            for key, value in fields.items()
            if value is not None
        )
        logger_fn("【CloudDriveMiniUpload】%s | %s", message, rendered)

    def _should_log_upload_path(self, path: str) -> bool:
        return str(path or "").strip() in {
            "/api/tasks/upload/create",
            "/api/tasks/upload/chunk",
            "/api/tasks/detail",
            "/api/files/upload",
            "/api/family/upload",
        }

    def _summarize_upload_result(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        detail = data.get("detail", {}) if isinstance(data.get("detail"), dict) else {}
        result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
        summary: dict[str, Any] = {}
        for key in ("status", "message", "task_id", "path", "name"):
            value = data.get(key)
            if value not in {None, ""}:
                summary[key] = value
        for key in ("target_name", "target_path", "requires_upload", "requires_block_hashes", "md5_block_size"):
            value = detail.get(key)
            if value not in {None, ""}:
                summary[f"detail.{key}"] = value
        for key in ("path", "name", "requires_upload", "rapid_upload"):
            value = result.get(key)
            if value not in {None, ""}:
                summary[f"result.{key}"] = value
        return summary or data

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
        attach_account_id: bool = True,
    ) -> dict[str, Any]:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if attach_account_id and self.account_id:
            request_params.setdefault("account_id", self.account_id)
        request_payload = dict(payload or {})
        if request_payload and attach_account_id and self.account_id:
            request_payload.setdefault("account_id", self.account_id)
        if self._should_log_upload_path(path):
            self._upload_log(
                "info",
                "request_json start",
                method=method.upper(),
                path=path,
                params=request_params,
                payload=request_payload,
            )
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            params=request_params,
            json=request_payload or None,
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            if self._should_log_upload_path(path):
                self._upload_log("warning", "request_json retry after 401", path=path, status_code=response.status_code)
            self._ensure_auth(force=True)
            return self._request_json(
                method,
                path,
                params=params,
                payload=payload,
                retry_auth=False,
                attach_account_id=attach_account_id,
            )
        if self._should_log_upload_path(path):
            self._upload_log(
                "info",
                "request_json response",
                path=path,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
            )
        response.raise_for_status()
        data = response.json()
        if self._should_log_upload_path(path):
            self._upload_log("info", "request_json result", path=path, result=self._summarize_upload_result(data))
        if str(data.get("status", "") or "").strip().lower() == "error":
            raise CloudDriveMiniError(str(data.get("message") or f"{path} failed"))
        return data

    def _request_stream(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
        attach_account_id: bool = True,
    ) -> requests.Response:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if attach_account_id and self.account_id:
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
            return self._request_stream(path, params=params, retry_auth=False, attach_account_id=attach_account_id)
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
        attach_account_id: bool = True,
    ) -> dict[str, Any]:
        self._ensure_auth()
        request_params = {key: value for key, value in (params or {}).items() if value not in {None, ""}}
        if attach_account_id and self.account_id:
            request_params.setdefault("account_id", self.account_id)
        if self._should_log_upload_path(path):
            self._upload_log(
                "info",
                "request_binary start",
                method=method.upper(),
                path=path,
                params=request_params,
                body_bytes=len(data or b""),
                headers=headers or {},
            )
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            params=request_params,
            headers=headers or {},
            data=data,
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry_auth:
            if self._should_log_upload_path(path):
                self._upload_log("warning", "request_binary retry after 401", path=path, status_code=response.status_code)
            self._ensure_auth(force=True)
            return self._request_binary(
                method,
                path,
                params=params,
                data=data,
                headers=headers,
                retry_auth=False,
                attach_account_id=attach_account_id,
            )
        if self._should_log_upload_path(path):
            self._upload_log(
                "info",
                "request_binary response",
                path=path,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
            )
        response.raise_for_status()
        result = response.json()
        if self._should_log_upload_path(path):
            self._upload_log("info", "request_binary result", path=path, result=self._summarize_upload_result(result))
        if str(result.get("status", "") or "").strip().lower() == "error":
            raise CloudDriveMiniError(str(result.get("message") or f"{path} failed"))
        return result

    def list_accounts(self) -> dict[str, Any]:
        data = self._request_json("GET", "/api/accounts", attach_account_id=False)
        active_account_id = str(data.get("active_account_id") or "").strip()
        accounts: List[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in data.get("accounts", []):
            if not isinstance(raw, dict):
                continue
            account_id = str(raw.get("account_id") or "").strip()
            if not account_id or account_id in seen:
                continue
            seen.add(account_id)
            item = dict(raw)
            item["account_id"] = account_id
            item["display_name"] = str(raw.get("display_name") or account_id).strip() or account_id
            item["provider"] = str(raw.get("provider") or "").strip()
            accounts.append(item)
        return {
            "active_account_id": active_account_id,
            "accounts": accounts,
        }

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
        progress_callback = _noop_progress_callback
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

    def _file_digests(self, path: Path) -> dict[str, str]:
        md5_digest = hashlib.md5()
        sha1_digest = hashlib.sha1()
        sha256_digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                if not chunk:
                    break
                md5_digest.update(chunk)
                sha1_digest.update(chunk)
                sha256_digest.update(chunk)
        return {
            "md5": md5_digest.hexdigest(),
            "sha1": sha1_digest.hexdigest().upper(),
            "sha256": sha256_digest.hexdigest(),
        }

    def _md5_block_hashes(self, path: Path, block_size: int) -> list[str]:
        normalized_block_size = max(1, int(block_size or 0))
        blocks: list[str] = []
        with path.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(normalized_block_size)
                if not chunk:
                    break
                block_md5 = hashlib.md5()
                block_md5.update(chunk)
                blocks.append(block_md5.hexdigest())
        return blocks

    def _current_provider(self) -> str:
        accounts_data = self.list_accounts()
        target_account_id = self.account_id or str(accounts_data.get("active_account_id") or "").strip()
        for account in accounts_data.get("accounts", []):
            if not isinstance(account, dict):
                continue
            if str(account.get("account_id") or "").strip() != target_account_id:
                continue
            return str(account.get("provider") or "").strip()
        return ""

    def _create_upload_task(
        self,
        filename: str,
        file_size: int,
        remote_dir_path: str,
        *,
        content_hash: str = "",
        content_hash_algorithm: str = "",
        sha1: str = "",
        md5: str = "",
        md5_block_size: int = 0,
        md5_block_hashes: Optional[List[str]] = None,
        probe_only: bool = False,
    ) -> dict[str, Any]:
        self._upload_log(
            "info",
            "create upload task",
            filename=filename,
            file_size=file_size,
            remote_dir_path=remote_dir_path,
            mode=self.mode,
            chunk_size=self.upload_chunk_size,
            probe_only=probe_only,
            has_sha256=bool(content_hash),
            has_sha1=bool(sha1),
            has_md5=bool(md5),
            md5_block_size=md5_block_size,
            md5_block_count=len(list(md5_block_hashes or [])),
        )
        return self._request_json(
            "POST",
            "/api/tasks/upload/create",
            payload={
                "filename": filename,
                "file_size": file_size,
                "remote_dir_path": remote_dir_path,
                "mode": self.mode,
                "chunk_size": self.upload_chunk_size,
                "content_hash": content_hash,
                "content_hash_algorithm": content_hash_algorithm,
                "sha1": sha1,
                "md5": md5,
                "md5_block_size": md5_block_size,
                "md5_block_hashes": list(md5_block_hashes or []),
                "probe_only": probe_only,
            },
        )

    def _upload_file_direct(
        self,
        local_path: Path,
        filename: str,
        remote_dir_path: str,
        progress_callback: Any,
        *,
        digests: Optional[Dict[str, str]] = None,
    ) -> dict[str, Any]:
        endpoint = "/api/family/upload" if self.mode == "family" else "/api/files/upload"
        self._ensure_auth()
        request_params: Dict[str, Any] = {}
        if self.account_id:
            request_params["account_id"] = self.account_id
        file_size = int(local_path.stat().st_size or 0)
        if file_size <= 0:
            raise CloudDriveMiniError("upload body is empty")
        encoded_filename = quote(filename, safe="")
        encoded_remote_dir_path = quote(remote_dir_path, safe="/")
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(file_size),
            "X-Filename": encoded_filename,
            "X-Remote-Dir": encoded_remote_dir_path,
            "X-Upload-Origin": "plugin",
        }
        normalized_digests = dict(digests or {})
        if str(normalized_digests.get("sha256") or "").strip():
            headers["X-Content-Hash"] = str(normalized_digests.get("sha256") or "").strip()
            headers["X-Content-Hash-Algorithm"] = "SHA256"
        if str(normalized_digests.get("sha1") or "").strip():
            headers["X-Content-Sha1"] = str(normalized_digests.get("sha1") or "").strip()
        if str(normalized_digests.get("md5") or "").strip():
            headers["X-Content-Md5"] = str(normalized_digests.get("md5") or "").strip()
        self._upload_log(
            "info",
            "direct upload start",
            endpoint=endpoint,
            filename=filename,
            encoded_filename=encoded_filename,
            remote_dir_path=remote_dir_path,
            encoded_remote_dir_path=encoded_remote_dir_path,
            file_size=file_size,
            params=request_params,
            has_sha256=bool(normalized_digests.get("sha256")),
            has_sha1=bool(normalized_digests.get("sha1")),
            has_md5=bool(normalized_digests.get("md5")),
        )
        if progress_callback is not None:
            progress_callback(0)
        for attempt in range(2):
            self._upload_log("info", "direct upload attempt", endpoint=endpoint, filename=filename, attempt=attempt + 1)
            with local_path.open("rb") as file_obj:
                upload_stream = _ProgressFileReader(file_obj, file_size, progress_callback)
                response = self.session.request(
                    "POST",
                    f"{self.base_url}{endpoint}",
                    params=request_params,
                    headers=headers,
                    data=upload_stream,
                    timeout=max(self.timeout, 3600),
                )
            if response.status_code == 401 and attempt == 0:
                self._upload_log("warning", "direct upload retry after 401", endpoint=endpoint, filename=filename)
                response.close()
                self._ensure_auth(force=True)
                if progress_callback is not None:
                    progress_callback(0)
                continue
            self._upload_log(
                "info",
                "direct upload response",
                endpoint=endpoint,
                filename=filename,
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
            )
            response.raise_for_status()
            result = response.json()
            response.close()
            self._upload_log("info", "direct upload result", endpoint=endpoint, filename=filename, result=self._summarize_upload_result(result))
            if str(result.get("status", "") or "").strip().lower() == "error":
                raise CloudDriveMiniError(str(result.get("message") or f"{endpoint} failed"))
            if progress_callback is not None:
                progress_callback(100)
            return result
        raise CloudDriveMiniError(f"{endpoint} failed")

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
        self._upload_log("info", "wait upload complete start", task_id=task_id, timeout_seconds=timeout_seconds)
        while time() < deadline:
            task = self._upload_task_detail(task_id)
            status = str(task.get("status") or "").strip().lower()
            self._upload_log("debug", "wait upload complete poll", task_id=task_id, status=status, task=self._summarize_upload_result(task))
            if status == "success":
                self._upload_log("info", "wait upload complete success", task_id=task_id, task=self._summarize_upload_result(task))
                return task
            if status in {"error", "partial", "cancelled"}:
                self._upload_log("error", "wait upload complete failed", task_id=task_id, task=self._summarize_upload_result(task))
                raise CloudDriveMiniError(str(task.get("error_message") or f"upload task failed: {status}"))
            sleep(1)
        self._upload_log("error", "wait upload complete timeout", task_id=task_id, timeout_seconds=timeout_seconds)
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
        file_size = int(local_path.stat().st_size or 0)
        target_marker = PurePosixPath(target_dir.path).joinpath(target_name).as_posix()
        progress_callback = _noop_progress_callback
        provider = self._current_provider()
        self._upload_log(
            "info",
            "upload start",
            local_path=str(local_path),
            target_name=target_name,
            target_dir_path=target_dir.path,
            remote_dir_path=remote_dir_path,
            file_size=file_size,
            account_id=self.account_id or "(active)",
            provider=provider,
            mode=self.mode,
            base_url=self.base_url,
            root_path=self.root_path,
        )
        digests: dict[str, str] = {}
        if self.mode == "personal" and provider in {"yun139", "unicom", "115", "clouddrive2"}:
            digests = self._file_digests(local_path)
            self._upload_log(
                "info",
                "upload digests computed",
                target_name=target_name,
                file_size=file_size,
                sha256=digests.get("sha256", ""),
                sha1=digests.get("sha1", ""),
                md5=digests.get("md5", ""),
            )
        if self.mode == "personal" and provider == "yun139":
            task = self._create_upload_task(
                target_name,
                file_size,
                remote_dir_path,
                content_hash=str(digests.get("sha256") or ""),
                content_hash_algorithm="SHA256",
                sha1=str(digests.get("sha1") or ""),
                md5=str(digests.get("md5") or ""),
                probe_only=True,
            )
            task_detail = task.get("detail", {}) if isinstance(task.get("detail"), dict) else {}
            task_result = task.get("result", {}) if isinstance(task.get("result"), dict) else {}
            status = str(task.get("status") or "").strip().lower()
            self._upload_log("info", "yun139 probe result", target_name=target_name, status=status, task=self._summarize_upload_result(task))
            if status == "success" or not bool(task_result.get("requires_upload", True)):
                progress_callback(100)
                actual_target_path = str(
                    task_result.get("path")
                    or task_detail.get("target_path")
                    or f"{remote_dir_path.rstrip('/')}/{task_detail.get('target_name') or target_name}"
                ).strip()
                self._upload_log("info", "yun139 probe completed without direct upload", target_name=target_name, actual_target_path=actual_target_path)
                return self.get_item(Path(self._visible_path(actual_target_path)))
        elif self.mode == "personal" and provider == "clouddrive2":
            task = self._create_upload_task(
                target_name,
                file_size,
                remote_dir_path,
                content_hash=str(digests.get("sha256") or ""),
                content_hash_algorithm="SHA256",
                sha1=str(digests.get("sha1") or ""),
                md5=str(digests.get("md5") or ""),
                probe_only=True,
            )
            task_detail = task.get("detail", {}) if isinstance(task.get("detail"), dict) else {}
            task_result = task.get("result", {}) if isinstance(task.get("result"), dict) else {}
            status = str(task.get("status") or "").strip().lower()
            self._upload_log("info", "clouddrive2 probe result", target_name=target_name, status=status, task=self._summarize_upload_result(task))
            if status == "success" or not bool(task_result.get("requires_upload", True)):
                progress_callback(100)
                actual_target_path = str(
                    task_result.get("path")
                    or task_detail.get("target_path")
                    or f"{remote_dir_path.rstrip('/')}/{task_detail.get('target_name') or target_name}"
                ).strip()
                self._upload_log("info", "clouddrive2 probe completed without direct upload", target_name=target_name, actual_target_path=actual_target_path)
                return self.get_item(Path(self._visible_path(actual_target_path)))
            if bool(task_detail.get("requires_block_hashes")):
                md5_block_size = int(task_result.get("md5_block_size", 0) or task_detail.get("md5_block_size", 0) or 0)
                if md5_block_size > 0:
                    self._upload_log("info", "clouddrive2 probe requests block hashes", target_name=target_name, md5_block_size=md5_block_size)
                    retry_task = self._create_upload_task(
                        target_name,
                        file_size,
                        remote_dir_path,
                        content_hash=str(digests.get("sha256") or ""),
                        content_hash_algorithm="SHA256",
                        sha1=str(digests.get("sha1") or ""),
                        md5=str(digests.get("md5") or ""),
                        md5_block_size=md5_block_size,
                        md5_block_hashes=self._md5_block_hashes(local_path, md5_block_size),
                        probe_only=True,
                    )
                    retry_detail = retry_task.get("detail", {}) if isinstance(retry_task.get("detail"), dict) else {}
                    retry_result = retry_task.get("result", {}) if isinstance(retry_task.get("result"), dict) else {}
                    retry_status = str(retry_task.get("status") or "").strip().lower()
                    self._upload_log("info", "clouddrive2 block-hash probe result", target_name=target_name, status=retry_status, task=self._summarize_upload_result(retry_task))
                    if retry_status == "success" or not bool(retry_result.get("requires_upload", True)):
                        progress_callback(100)
                        actual_target_path = str(
                            retry_result.get("path")
                            or retry_detail.get("target_path")
                            or f"{remote_dir_path.rstrip('/')}/{retry_detail.get('target_name') or target_name}"
                        ).strip()
                        self._upload_log("info", "clouddrive2 block-hash probe completed without direct upload", target_name=target_name, actual_target_path=actual_target_path)
                        return self.get_item(Path(self._visible_path(actual_target_path)))
        elif self.mode == "personal" and provider == "115":
            task = self._create_upload_task(
                target_name,
                file_size,
                remote_dir_path,
                content_hash=str(digests.get("sha256") or ""),
                content_hash_algorithm="SHA256",
                sha1=str(digests.get("sha1") or ""),
                md5=str(digests.get("md5") or ""),
                probe_only=True,
            )
            task_detail = task.get("detail", {}) if isinstance(task.get("detail"), dict) else {}
            task_result = task.get("result", {}) if isinstance(task.get("result"), dict) else {}
            status = str(task.get("status") or "").strip().lower()
            self._upload_log("info", "115 probe result", target_name=target_name, status=status, task=self._summarize_upload_result(task))
            if status == "success" or not bool(task_result.get("requires_upload", True)):
                progress_callback(100)
                actual_target_path = str(
                    task_result.get("path")
                    or task_detail.get("target_path")
                    or f"{remote_dir_path.rstrip('/')}/{task_detail.get('target_name') or target_name}"
                ).strip()
                self._upload_log("info", "115 probe completed without direct upload", target_name=target_name, actual_target_path=actual_target_path)
                return self.get_item(Path(self._visible_path(actual_target_path)))
        if global_vars.is_transfer_stopped(target_marker):
            self._upload_log("warning", "upload aborted before direct upload", target_name=target_name, target_marker=target_marker)
            return None
        self._upload_log("info", "fall back to direct upload", target_name=target_name, provider=provider, mode=self.mode)
        upload_result = self._upload_file_direct(local_path, target_name, remote_dir_path, progress_callback, digests=digests)
        result = upload_result if isinstance(upload_result, dict) else {}
        detail = {}
        actual_target_path = str(
            result.get("path")
            or detail.get("target_path")
            or f"{remote_dir_path.rstrip('/')}/{detail.get('target_name') or target_name}"
        ).strip()
        self._upload_log("info", "upload finished", target_name=target_name, actual_target_path=actual_target_path, result=self._summarize_upload_result(result))
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
