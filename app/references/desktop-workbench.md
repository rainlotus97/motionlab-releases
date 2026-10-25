# 桌面应用与资源库

用户请求打开工作台时，默认使用已安装的桌面 app，不为每个会话启动第二个服务。macOS 12+ 的 Release 包自带两种架构的 Python/Pillow 与原生 Cocoa/WKWebView，不需要 Xcode、系统 Python、Git 或 Node。

普通安装从 [最新 Release](https://github.com/rainlotus97/motionlab-releases/releases/latest) 下载 ZIP，解压并将 `MotionLab.app` 放到应用程序目录。首次启动安装技能和精选示例；数据独立在 `~/Desktop/MotionLab/`。菜单“检查更新…”下载安装正式 Release，保存调整后重启，无需登录或填写凭证。手动升级则退出后替换 app。升级不改工程和素材；替换失败恢复旧 app，旧版备份保留在应用旁。当前为 ad-hoc 签名，首装可能需按 README 中 Apple 官方说明允许打开。

源码开发才使用 `python3 scripts/setup_studio.py` 或 `Open.command`，需要 Python 3.10+、Swift 编译器（Xcode Command Line Tools）。此安装生成绑定本地路径的开发 app，不能当作便携包复制给其他电脑。`--library` 可指定数据目录。

Release 环境执行工具时，使用技能目录的 `scripts/run <脚本名> <参数>`，例如 `scripts/run studio_control.py status`。它自动选随包 Python；文档中的 `python3 scripts/foo.py` 等价于 `scripts/run foo.py`。不要因为机器没有 python3 而要求用户安装 Xcode。技能安装链接指向 app 内资源；个人参数、服务配置与素材保存在 app 外，升级会替换 app 内代码。

## 窗口与进程

- 系统打开已有应用时激活当前窗口；窗口锁进一步拒绝重复进程创建第二个窗口。
- 一个应用进程拥有一个 Python 服务、一个 127.0.0.1 监听端口。端口用系统空闲分配；不固定抢占端口，不杀陌生服务。
- 多工程用 `/p/<project-id>/` 路径隔离，复用同一监听器。旧请求仍绑定原工程，切换不会改写其他请求的归属。
- 关闭最后窗口或退出应用先保留未保存调整（失败可返回工作台），再终止子服务；父应用异常退出时 Python 检测父 PID 变化并释放服务。磁盘锁文件残留不代表占用，以 OS 文件锁为准。
- `.studio/instance.json` 与 `.studio/service.log` 仅用于本机诊断。不要用陈旧 PID 文件任意 kill；桌面应用不复用旧会话手工启动的 serve。
- 运行中可在菜单「角色动作工坊 → 显示资源目录」打开 Finder。菜单「文件」包含工程、导入、保存、导出；「视图」包含动作、骨骼、对照、工程文件与检查器。`⌘,` 打开设置，主界面侧栏保留动作树。

## 数据结构与迁移

```text
~/Desktop/MotionLab/
  MotionLab.app
  projects/
    <project-id>/studio.project.json
    <project-id>/sources/      # 实际采用图、必要原图及制作来源
    <project-id>/actions/      # 序列帧／配准产物
    <project-id>/review/       # 本工程冻结方案、映射与审阅
    <project-id>/exports/      # 明确请求的输出
  references/                 # 全局骨骼数据、来源许可与抽样方案
  studio.tools.json            # 全局 sampling 配置；路径相对资源库
  .studio/                    # 锁、日志、最近工程；无需迁移
```

新电脑默认创建短英文目录 `~/Desktop/MotionLab/`。旧默认中文目录在关闭应用后自动迁移；若新旧目录同时存在，优先使用新目录，不覆盖或合并旧素材。默认仅随仓库分发用户授权的松团日常装和 UAL 1 骨骼库，其他素材与生成记录留在本地。迁移时复制 `projects/`、`references/`、`studio.tools.json`，下载 Release app；无需复制 `.studio` 或旧机开发 app。默认安装将精选示例复制到资源库，并登记骨骼库；已有同名目录不覆盖，已有同 ID 骨骼库不重复登记。使用 `--no-examples` 跳过；没有任何工程时创建“我的角色”空工程。

骨骼参考与逐帧对照都是全局工具能力。骨骼来源与方案存全局库；逐帧对照必须针对具体人物帧，因此映射、冻结快照、人工记录仍归资源工程。不要把角色目录当成通用工具代码安装位置，也不要因为移动全局库而丢掉来源许可或把旧审阅冒充当前通过。

随仓库提供的松团只有日常装 9 clips；骨骼只含 UAL 1 的 43 个动作。完整个人库可以另外迁移，其他项目不依赖这些示例。普通动作不要求骨骼库；空库时说明如何登记真实来源。

## AI 与程序控制

可视化应用与 AI 共用同一份工程、草稿、导出器和文件 API。无需启第二个服务，也无需靠模拟鼠标来修改资产。应用运行时：

```sh
python3 scripts/studio_control.py status
python3 scripts/studio_control.py projects
# 使用 projects 返回的 id，读取对应工程
python3 scripts/studio_control.py request --project-id <id> --route /api/project
# JSON 正文放文件，不塞进 shell 字符串；支持实现合同列出的写入、草稿、异步导出 API
python3 scripts/studio_control.py request --project-id <id> --route /api/files/draft --data-file /absolute/path/draft.json
```

可加 `--library /absolute/path/library`。控制脚本读取 `.studio/instance.json`，核对活动服务的随机实例标识和 PID，拒绝过期地址、代理和重定向，不凭陈旧 PID 杀进程。每个工程用自己的 URL 命名空间；写入仍执行 hash／fingerprint 校验。它控制工程数据，不向页面注入任意 JavaScript；外部写入后在原生「视图 → 刷新素材」重新读取，有未保存调整时先保存或取消。完整路由见[实现合同](studio-architecture.md)。

应用没开时可直接用同一 `.venv/bin/python scripts/motion_studio.py inspect|apply|export`，不需要图形界面。CLI 导出示例见工作台分支；输出新目录，原图保持。应用图标采用用户认可位图，安装时生成所有 ICNS 尺寸；更新图标、原生代码或 Python 路径会重新构建入口。

## 维护验证

新增桌面启动代码后检查双次启动只剩一个窗口／监听器、关闭再打开、端口释放、无效项目不损坏现有项目、路径隔离与全局骨骼读取。服务测试：`python3 -m unittest test_studio_desktop test_motion_catalog`（在 scripts/）。原生菜单、设置与下载需实际在 Mac 窗口操作验证。不要把服务单元测试称作原生 UI 验证。
