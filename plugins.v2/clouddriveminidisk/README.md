# CloudDrive Mini Disk

`CloudDriveMiniDisk` 是一个 MoviePilot V2 存储插件，用于将
`clouddrive-mini` 项目的 HTTP API 接入为自定义存储后端。

## 当前能力

当前代码已经实现：

- 自定义存储注册
- 存储选择钩子
- 目录浏览
- 文件详情查询
- 新建文件夹
- 删除
- 重命名
- 下载
- 通过 `clouddrive-mini` 上传接口上传文件
- 复制
- 移动
- 通过账号概览查询存储容量
- 一个用于诊断的简易插件详情页
- 在插件配置页自动侦测并选择 `account_id`

## 目录文件

- `__init__.py`
  插件入口、表单定义、模块钩子注册。
- `clouddrive_mini_api.py`
  与 `clouddrive-mini` 通信的存储 API 适配层。
- `version.py`
  插件版本号。
- `requirements.txt`
  Python 运行时依赖。

## 运行依赖

插件依赖一个已运行的 `clouddrive-mini` HTTP 服务。

默认值：

- 主机：`127.0.0.1`
- 端口：`8765`
- 协议：`http`

如果 `clouddrive-mini` 开启了项目认证，还需要配置：

- `username`
- `password`

## 配置项说明

- `enabled`
  是否启用插件。
- `https`
  是否使用 HTTPS 代替 HTTP。
- `host`
  `clouddrive-mini` 服务地址。
- `port`
  `clouddrive-mini` 服务端口。
- `account_id`
  目标账号 ID。留空时可在配置页自动侦测并选择。
- `mode`
  运行模式，可选 `personal` 或 `family`。
- `root_path`
  插件可见的 `/` 会映射到这个远端路径。
- `username`
  当项目启用认证时使用的用户名。
- `password`
  当项目启用认证时使用的密码。
- `timeout`
  HTTP 请求超时时间，单位为秒。
- `upload_chunk_size_mb`
  上传分片大小，单位为 MB。

## API 对应关系

插件当前映射到以下 `clouddrive-mini` 接口：

- 浏览目录：`GET /api/files` 或 `GET /api/family/files`
- 查询详情：`GET /api/files/detail` 或 `GET /api/family/detail`
- 新建目录：`POST /api/files/mkdir` 或 `POST /api/family/mkdir`
- 重命名：`POST /api/files/rename` 或 `POST /api/family/rename`
- 删除：`POST /api/files/delete` 或 `POST /api/family/delete`
- 下载：`GET /api/files/download` 或 `GET /api/family/download`
- 直接上传：`POST /api/files/upload` 或 `POST /api/family/upload`
- 创建上传任务：`POST /api/tasks/upload/create`
- 上传分片：`POST /api/tasks/upload/chunk`
- 查询上传任务状态：`GET /api/tasks/detail`
- 查询容量：`GET /api/accounts/overview`

## 版本信息

当前仓库元数据版本为 `v0.1.2`：

```json
{
  "CloudDriveMiniDisk": {
    "name": "CloudDrive Mini存储",
    "description": "使用 clouddrive-mini 项目 HTTP API 作为 MoviePilot 自定义存储。",
    "labels": "存储",
    "version": "0.1.2",
    "icon": "Cloudrive_A.png",
    "author": "yyllaa",
    "level": 1,
    "history": {
      "v0.1.2": "插件上传优先走项目直传接口并附带摘要头，减少中转缓存依赖；同步中文文档和作者主页。",
      "v0.1.1": "支持在插件配置页自动侦测并选择 account_id，保留原有配置可直接升级。",
      "v0.1.0": "初始版本：接入 clouddrive-mini 作为 MoviePilot 自定义存储，支持浏览、详情、建目录、删除、重命名、下载和分片上传。"
    }
  }
}
```

## 当前不足

- 还没有独立的自动化测试用例
- `get_page()` 目前主要用于诊断，不是正式操作界面
- 与真实 MoviePilot 运行环境的集成还需要在线验证

## 建议验证步骤

1. 启用插件。
2. 配置 `host`、`port`、`account_id`、`mode` 和 `root_path`。
3. 保存配置并确认存储已经出现在系统中。
4. 浏览一个目录。
5. 新建一个文件夹。
6. 上传一个小文件。
7. 再把它下载回来。
8. 执行重命名。
9. 执行复制。
10. 执行移动。
11. 检查容量信息是否正常。
