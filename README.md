# CloudDriveStorageBridge

MoviePilot V2 插件骨架，用来对接 `clouddrive-mini` 里的 `moviepilot-storage` 桥接插件。

## 作用

- 从 `clouddrive-mini` 读取已经挂载好的云盘根目录
- 按 MoviePilot 传入的子路径解析目标保存位置
- 在 MoviePilot 侧发起目录可写性探测
- 在真正上传前先做 `upload-probe`
- 需要上传文件内容时，走 `clouddrive-mini` 的直传路由，不经过 MoviePilot 插件 HTTP 上传缓冲

## 依赖前提

1. `clouddrive-mini` 已启用 `moviepilot-storage` 插件
2. 该插件已配置 `token`
3. MoviePilot 能访问 `clouddrive-mini` 的 HTTP 地址

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
    "version": "0.1.0",
    "icon": "Cloudrive_A.png",
    "author": "yyllaa",
    "level": 1
  }
}
```

## 插件配置

- `server_url`: `clouddrive-mini` 地址，例如 `http://192.168.9.16:8765`
- `token`: `moviepilot-storage` 插件使用的桥接 token
- `root_key`: 默认使用的挂载根目录，可留空

目录归类继续使用 MoviePilot 自身的目录配置；桥接插件不额外维护电影 / 剧集 / 动漫目录。需要指定目标子目录时，在调用 `resolve`、`upload-probe` 或 `transfer_file(...)` 时传入 `sub_path`。

## 提供的插件 API

- `GET /api/v1/plugin/CloudDriveStorageBridge/roots`
- `POST /api/v1/plugin/CloudDriveStorageBridge/resolve`
- `POST /api/v1/plugin/CloudDriveStorageBridge/probe`
- `POST /api/v1/plugin/CloudDriveStorageBridge/upload-probe`

这些接口用于 MoviePilot 前端、工作流或后续宿主扩展调用。

## 直传说明

不要在 MoviePilot 插件 API 上再额外挂一个大文件上传入口。那样文件会先经过 MoviePilot 端的请求体缓冲，50G 级别文件不合适。

正确用法是：

1. 先调 `upload-probe`
2. 如果命中秒传，直接结束
3. 如果需要上传，再从宿主侧调用 `CloudDriveStorageBridge.transfer_file(...)`
4. `transfer_file(...)` 会把文件流直接转发到 `clouddrive-mini` 的 `/api/public/moviepilot-storage/upload-stream`

如果 MoviePilot 宿主侧拿到的是一个已经下载到本地的临时文件路径，可以直接调用：

- `CloudDriveStorageBridge.transfer_local_file(local_path, payload=..., run_probe=True)`

这样宿主不需要自己处理文件打开、文件大小统计和流转发。

这样可以保持：

- 不落 `clouddrive-mini` 本地上传缓存目录
- 不回退到插件 body 整包读取
- 仅在 provider 支持 `known_size_stream` 时放行直传
