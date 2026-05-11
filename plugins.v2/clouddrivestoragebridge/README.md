# CloudDriveStorageBridge

MoviePilot V2 插件，用来把 `clouddrive-mini` 作为原生存储后端接入，并通过项目自身账号体系直接登录，不再依赖宿主机额外安装 `moviepilot-storage` 插件。

## 作用

- 读取 `clouddrive-mini` 当前已经挂载好的云盘根目录
- 在 MoviePilot 中注册一个原生存储入口 `CloudDrive Mini 存储`
- 进入该存储根目录时，直接显示挂载好的云盘列表
- 按 MoviePilot 传入的目录信息解析目标保存位置
- 在 MoviePilot 侧发起目录可写性探测和上传预检查
- 大文件直传走 `clouddrive-mini` 的直传链路，不经过 MoviePilot 插件自身缓存

## 目录放置

把本目录复制到 `MoviePilot-Plugins/plugins.v2/clouddrivestoragebridge/`

主类名是 `CloudDriveStorageBridge`，目录名为其小写形式，符合 V2 规范。

## package.v2.json 示例

```json
{
  "CloudDriveStorageBridge": {
    "name": "CloudDrive 存储桥接",
    "description": "连接 clouddrive-mini，在 MoviePilot 中直接显示挂载云盘并提供直传能力。",
    "labels": "存储,云盘,桥接,直传",
    "version": "18.0",
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
- `root_key`: 可选。用于兼容旧路径或指定默认容量查询目标

如果 `clouddrive-mini` 没有开启项目登录鉴权，账号密码可以留空，插件会直接请求内置存储接口。

## 提供的插件 API

- `GET|POST /api/v1/plugin/CloudDriveStorageBridge/roots`
- `POST /api/v1/plugin/CloudDriveStorageBridge/resolve`
- `POST /api/v1/plugin/CloudDriveStorageBridge/probe`
- `POST /api/v1/plugin/CloudDriveStorageBridge/upload-probe`

## 原生存储接入

插件同时提供 `get_module()` 能力映射，并监听 `StorageOperSelection` 事件。

当前已接入的原生存储方法包括：

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

其中最关键的变化是：

1. `CloudDrive Mini 存储` 根目录 `/` 会直接显示挂载好的云盘
2. 后续路径会自动拆成 `挂载盘 + 相对路径`
3. 插件再把它解析回 `root_key + sub_path` 调用 `clouddrive-mini`

## 直传说明

不要在 MoviePilot 插件 API 上再额外挂一个大文件上传入口。那样文件会先经过 MoviePilot 端的请求体缓存，50G 级别文件并不合适。

正确用法是：

1. 先调 `upload-probe`
2. 如果命中秒传，直接结束
3. 如果需要上传，再从宿主侧调用 `CloudDriveStorageBridge.transfer_file(...)`
4. `transfer_file(...)` 会把文件流直接转发到 `clouddrive-mini` 的 `/api/moviepilot/storage/upload-stream`

如果 MoviePilot 宿主侧拿到的是一个已经下载到本地的临时文件路径，可以直接调用：

- `CloudDriveStorageBridge.transfer_local_file(local_path, payload=..., run_probe=True)`

## 18.0

- 将 MoviePilot 中注册的原生存储入口更名为 `CloudDrive Mini 存储`
- 避免与 `CloudDrive2储存` 插件共用同一个存储名时，被宿主同时命中两个插件实现
- 对应现网问题就是：文件已经成功整理，但宿主在后续链路又调到另一份 CloudDrive 插件，最终被误记为失败

## 17.0

- `init_plugin()` 会把规范化后的配置回写到 MoviePilot 插件配置存储
- 修复页面实例可用、但原生存储实例 `get_config()` 为空时，`storage/list` 与上传链路拿不到挂载盘的问题
- 对应现网现象就是：插件页显示正常，但 `CloudDrive Mini 存储` 根目录仍然是空的

## 16.0

- 兼容 MoviePilot 以字符串形式传入本地文件路径调用 `upload()` / `upload_file()`
- 不再因为 `path.name`、`stat()` 读取失败而静默返回 `None`
- 修复整理链路中文件已选定目标目录，但上传阶段被统一记成“上传 CloudDrive Mini 存储 失败”的一类问题

## 15.0

- 修复 MoviePilot 原生存储实例无法自行恢复持久化配置的问题
- 即使插件页面实例与存储实例分离，`list_files()`、`upload_file()`、`storage_usage()` 等调用也会主动读取配置并刷新挂载根目录
- 避免 `storage/list` 返回空挂载，连带导致整理上传链路直接失败

## 14.0

- 修复文件已直传成功但立即回查目标项为空时，被 MoviePilot 误判为“上传失败”的问题
- 上传后如果远端条目暂时未能立即回查到，插件会按目标路径返回合成的文件项，避免整理链路误报失败

## 13.0

- 仅提升插件发布版本号，便于 MoviePilot 侧识别并拉取最新插件包
- 延续 `11.0` 的宿主兼容方法入口与递归补建逻辑

## 11.0

- 兼容 MoviePilot 直接以 `Path` 调用 `get_folder()` 和 `get_item()`
- 目标目录不存在时会自动按父目录递归补建，避免整理阶段报“目标目录获取失败”
- 补充 `upload()`、`delete()`、`rename()` 宿主兼容入口，避免整理阶段直接报对象缺少方法

## 0.8.0

- 兼容 MoviePilot 上传流程调用 `get_folder()`
- 目录已存在时直接复用，不重复创建

## 0.7.0

- 修复 MoviePilot 页面实例与原生存储实例状态分叉问题
- 让原生存储实例共享最新配置、根目录快照和传输状态

## 0.6.0

- 改为在 MoviePilot 根目录下直接展示 `clouddrive-mini` 已挂载云盘
- 新增虚拟路径到 `root_key + sub_path` 的自动解析
- 保留原生存储上传、删除、重命名、建目录和容量查询能力
