# MotionLab

打开应用程序目录中的 `MotionLab.app`（源码开发入口可能在本目录）。重复打开使用同一窗口；关闭窗口释放服务。`⌘,` 打开设置，文件菜单可导入、保存和导出，视图菜单可进入骨骼参考和逐帧对照。

- `projects/<project-id>/studio.project.json`：每个资源工程的入口，素材和调整归工程所有。
- `references/`、`studio.tools.json`：共用骨骼来源与方案；没有来源时普通动作仍可使用。
- 各工程 `exports/`：导出结果，默认 ZIP 只有 `package.json + atlases/`。
- `.studio/`：本机运行记录，迁移时不用复制。

换电脑时复制 `projects/`、`references/` 和 `studio.tools.json` 到 `~/Desktop/MotionLab/`，从 GitHub 最新 Release 下载应用即可，无需 Xcode 或 Python。不要复制旧机绑定本地源码路径的开发 app。菜单“检查更新…”可下载安装新版；下载与更新均无需登录。手动升级先退出，再替换 app，保留本资源库。

默认安装附带松团日常装（9 段动作）与 UAL 1 骨骼库（43 个动作）。这是从仓库复制的可编辑工程；`git pull` 不会同步本目录的修改，重复安装也不会覆盖同名目录。生成服务遵守当前用户选择；没有指定时可使用可用的内置生图能力。

AI 使用 `$character-motion` 读取工程清单，可通过 `scripts/studio_control.py` 查询活动服务、操作工程和导出。交给游戏时提供完整图包；消费包内身份、时序和定位约定，不重新猜裁格。
