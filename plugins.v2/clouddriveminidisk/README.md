# CloudDrive Mini Disk

`CloudDriveMiniDisk` is a MoviePilot V2 storage plugin that uses the
`clouddrive-mini` project's HTTP API as a custom storage backend.

## Status

This plugin directory is now beyond the skeleton stage. The current code
already includes:

- custom storage registration
- storage selection hook
- directory browsing
- file detail lookup
- folder creation
- delete
- rename
- download
- upload through the `clouddrive-mini` chunk upload task API
- copy
- move
- storage usage query through account overview
- a simple plugin detail page for diagnostics

## Directory Files

- `__init__.py`
  Plugin entry, form definition, module hook registration.
- `clouddrive_mini_api.py`
  Storage API adapter that talks to `clouddrive-mini`.
- `version.py`
  Plugin version.
- `requirements.txt`
  Python runtime dependency.
- `package.v2.snippet.json`
  Metadata snippet for a MoviePilot plugin repository.

## Runtime Dependency

The plugin requires a running `clouddrive-mini` HTTP service.

Default values:

- host: `127.0.0.1`
- port: `8765`
- scheme: `http`

If project authentication is enabled in `clouddrive-mini`, configure:

- `username`
- `password`

## Install

Copy this directory into a MoviePilot plugin repository as:

```text
plugins.v2/clouddriveminidisk
```

Then merge the content of `package.v2.snippet.json` into that repository's
`package.v2.json`.

## package.v2 Metadata

```json
{
  "CloudDriveMiniDisk": {
    "name": "CloudDrive Mini存储",
    "description": "使用 clouddrive-mini 项目 HTTP API 作为 MoviePilot 自定义存储。",
    "labels": "存储",
    "version": "0.1.0",
    "icon": "Cloudrive_A.png",
    "author": "zztt",
    "level": 1,
    "history": {
      "v0.1.0": "初始版本：接入 clouddrive-mini 作为自定义存储，支持浏览、详情、建目录、删除、重命名、下载、分片上传。"
    }
  }
}
```

## Config Fields

- `enabled`
  Enable the plugin.
- `https`
  Use HTTPS instead of HTTP.
- `host`
  `clouddrive-mini` host.
- `port`
  `clouddrive-mini` port.
- `account_id`
  Target account ID. If empty, use the active account.
- `mode`
  `personal` or `family`.
- `root_path`
  The plugin-visible `/` maps to this remote path.
- `username`
  Project auth username when project auth is enabled.
- `password`
  Project auth password when project auth is enabled.
- `timeout`
  HTTP timeout in seconds.
- `upload_chunk_size_mb`
  Upload chunk size in MB.

## API Mapping

The plugin currently maps to these `clouddrive-mini` APIs:

- browse: `GET /api/files` or `GET /api/family/files`
- detail: `GET /api/files/detail` or `GET /api/family/detail`
- mkdir: `POST /api/files/mkdir` or `POST /api/family/mkdir`
- rename: `POST /api/files/rename` or `POST /api/family/rename`
- delete: `POST /api/files/delete` or `POST /api/family/delete`
- download: `GET /api/files/download` or `GET /api/family/download`
- upload session: `POST /api/tasks/upload/create`
- upload chunk: `POST /api/tasks/upload/chunk`
- upload task state: `GET /api/tasks/detail`
- storage usage: `GET /api/accounts/overview`

## Current Gaps

- no dedicated test suite for this standalone plugin directory
- `get_page()` is diagnostic only, not an operational UI
- real MoviePilot runtime integration still needs live verification

## Suggested Live Verification

1. Enable the plugin.
2. Set `host`, `port`, `account_id`, `mode`, and `root_path`.
3. Save config and confirm the storage appears.
4. Browse a directory.
5. Create a folder.
6. Upload a small file.
7. Download it back.
8. Rename it.
9. Copy it.
10. Move it.
11. Check storage usage.

## Next Useful Work

- add a dedicated local test file for this plugin
- verify `family` mode against a real account
- improve error normalization for user-facing messages
- decide whether `copy/move` should expose more detailed success feedback
