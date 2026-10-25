# MotionLab

角色动作工作台与 `$character-motion` 技能的公开下载入口。支持 macOS 12+，提供 M 芯片和 Intel 两个独立安装包。

**[下载最新版本](https://github.com/rainlotus97/motionlab-releases/releases/latest)**

## 安装

1. 点击下载页上对应芯片的链接：M 系列选 `arm64.zip`，Intel 选 `x86_64.zip`，下载后解压。“ → 关于本机”可查看芯片。
2. 将 `MotionLab.app` 拖入“应用程序”（或自己的 `~/Applications`），双击打开。
3. 首次启动会安装技能，并在 `~/Desktop/MotionLab/` 创建资源库；开启新的 Codex 会话使用 `$character-motion`。

无需 Xcode、Python、Git、GitHub 账号或 token。生成图片使用当前 AI 环境的生图能力，也可按使用者选择配置其他服务；日常调帧、骨骼参考和导出可离线使用。

当前应用为 ad-hoc 签名，尚未 Apple 公证。首次被 macOS 拦截时，确认从本仓库下载后，按 [Apple 官方说明](https://support.apple.com/en-us/102445) 在“系统设置 → 隐私与安全性”允许打开。不要关闭系统安全检查。

## 升级

菜单 **MotionLab → 检查更新… → 下载并安装**，程序根据实际芯片选择更新包，校验下载包后保存调整、退出、替换并重开。也可退出后用新版 app 手动覆盖。自动升级保留旧 app 备份，素材与工程独立存放，不会被覆盖。

应用目录需可写；若没有系统“应用程序”目录权限，请使用 `~/Applications`。新版本以正式 Release 为准。此仓库公开分发安装包，并在 `app/` 保存同版本的构建快照；开发仓库与历史仍保持私有。快照和安装包仅含技能脚本、工具及以下精选示例，不含个人资源库与密钥。

## 随包示例

- 松团日常装一套：八方向跑动与蹦跳，9 段动作。图像保留创作者所有权，提供演示使用，不授予再次公开分发许可。
- Quaternius UAL 1 骨骼库一套：43 个动作，含原始来源文件与 CC0 许可。身体比例适配为参考草案，不代表最终人物蒙皮完成。

每个包只带对应芯片的 Python/Pillow，相关许可保留在包内。两个包由 GitHub Actions 分别在 ARM 和 Intel Mac runner 原生构建、验证；普通用户无需构建环境。`universal` 包只供 0.1.2 自动升级过渡，手动下载无需选择。下载校验值与验证记录在每个 Release 附件中。工具与示例可用不代表所有美术已经通过最终视觉验收。

## 自动构建与发布

[GitHub Actions](https://github.com/rainlotus97/motionlab-releases/actions/workflows/release.yml) 在 M 芯片与 Intel runner 分别构建并验证，推送与 `app/VERSION` 一致的 `v<版本>` 标签后，全部通过才自动发布 Release。Actions 的 Run workflow 可手动验证并下载临时 artifact；正式安装仍从 Releases 下载。构建和发布均在 GitHub 完成，无需维护者本地编译，也无需额外配置跨仓库密钥。
