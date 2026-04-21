# clouddriveminidisk-moviepilot

这是 `CloudDriveMiniDisk` 的 MoviePilot V2 插件仓库。

## 仓库结构

- `package.v2.json`
  插件元数据。
- `icons/Cloudrive_A.png`
  插件图标。
- `plugins.v2/clouddriveminidisk`
  插件源码目录。

## 当前版本

当前仓库版本为 `v0.1.2`，在原有浏览、详情、建目录、删除、重命名、下载、复制、移动、容量查询能力基础上，补充了：

- 插件配置页自动侦测并选择 `account_id`
- 作者主页链接修正
- 插件上传优先走项目直传接口，并附带摘要头，减少中转缓存依赖

## 插件源码入口

- `plugins.v2/clouddriveminidisk/__init__.py`
- `plugins.v2/clouddriveminidisk/clouddrive_mini_api.py`
- `plugins.v2/clouddriveminidisk/README.md`

## 说明

插件依赖已运行的 `clouddrive-mini` HTTP 服务，实际运行仍建议在真实 MoviePilot 环境中验证。
