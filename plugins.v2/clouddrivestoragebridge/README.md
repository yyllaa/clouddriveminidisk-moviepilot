# CloudDriveStorageBridge

MoviePilot V2 插件，用来把 `clouddrive-mini` 作为原生存储后端接入，并通过项目自身账号体系直接登录，不再依赖宿主机额外安装 `moviepilot-storage` 插件。

## 作用

- 读取 `clouddrive-mini` 当前已经挂载好的云盘根目录
- 按 MoviePilot 传入的目录信息解析目标保存位置
- 在 MoviePilot 侧发起目录可写性探测和上传预检查
- 上传大文件时直接走 `clouddrive-mini` 的直传链路，不经过 MoviePilot 插件自身缓存
- 以 MoviePilot 原生存储插件形态提供目录浏览、建目录、删除、重命名、容量查询和上传能力

## 依赖前提

1. `clouddrive-mini` 主程序可正常访问
2. MoviePilot 能访问 `clouddrive-mini` 的 HTTP 地址
3. 如果 `clouddrive-mini` 开启了项目登录鉴权，需要提供项目登录账号和密码

## 目录放置

把本目录复制到 `MoviePilot-Plugins/plugins.v2/clouddrivestoragebridge/`

主类名是 `CloudDriveStorageBridge`，目录名为其小写形式，符合 V2 规范。

## package.v2.json 示例

```json
{
  "CloudDriveStorageBridge": {
    "name": "CloudDrive 存储桥接",
    "description": "连接 clouddrive-mini 挂载目录，为 MoviePilot 提供可选存储路径和直传能力。",
    "labels": "存储,云盘,桥接",
    "version": "0.5.0",
    "icon": "Cloudrive_A.png",
    "author": "yyllaa",
    "level": 1
  }
}
```

## 插件配置

- `server_url`: `clouddrive-mini` 地址，例如 `http://192.168.9.16:8765`
- `username`: `clouddrive-mini` 项目登录账号
- `password`: `clouddrive-mini` 项目登录密码
- `root_key`: 默认使用的挂载根目录，可留空

如果 `clouddrive-mini` 没有开启项目登录鉴权，账号密码可以留空，插件会直接请求内置存储接口。

## 提供的插件 API

- `GET|POST /api/v1/plugin/CloudDriveStorageBridge/roots`
- `POST /api/v1/plugin/CloudDriveStorageBridge/resolve`
- `POST /api/v1/plugin/CloudDriveStorageBridge/probe`
- `POST /api/v1/plugin/CloudDriveStorageBridge/upload-probe`

这些接口用于 MoviePilot 前端、工作流或宿主扩展调用。

## 原生存储接入

插件同时提供 `get_module()` 能力映射，并监听 `StorageOperSelection` 事件。

当前已接上的原生存储方法包括：

- `list_files`
- `any_files`
- `upload_file`
- `delete_file`
- `rename_file`
- `create_folder`
- `exists`
- `get_item`
- `get_file_item`
- `get_parent_item`
- `support_transtype`
- `storage_usage`

对应的 `clouddrive-mini` 内置接口包括：

- `GET /api/moviepilot/storage/roots`
- `POST /api/moviepilot/storage/item`
- `POST /api/moviepilot/storage/list`
- `POST /api/moviepilot/storage/mkdir`
- `POST /api/moviepilot/storage/delete`
- `POST /api/moviepilot/storage/rename`
- `POST /api/moviepilot/storage/usage`
- `POST /api/moviepilot/storage/resolve`
- `POST /api/moviepilot/storage/probe`
- `POST /api/moviepilot/storage/upload-probe`
- `POST /api/moviepilot/storage/upload-stream`

## 直传说明

不要在 MoviePilot 插件 API 上再额外挂一个大文件上传入口。那样文件会先经过 MoviePilot 端的请求体缓存，50G 级别文件并不合适。

正确用法是：

1. 先调 `upload-probe`
2. 如果命中秒传，直接结束
3. 如果需要上传，再从宿主侧调用 `CloudDriveStorageBridge.transfer_file(...)`
4. `transfer_file(...)` 会把文件流直接转发到 `clouddrive-mini` 的 `/api/moviepilot/storage/upload-stream`

如果 MoviePilot 宿主侧拿到的是一个已经下载到本地的临时文件路径，可以直接调用：

- `CloudDriveStorageBridge.transfer_local_file(local_path, payload=..., run_probe=True)`

这样宿主不需要自己处理文件打开、文件大小统计和流转发。

这条链路可以保持：

- 不落 `clouddrive-mini` 本地上传缓存目录
- 不回退到插件 body 整包读取
- 仅在 provider 支持 `known_size_stream` 时放行直传
