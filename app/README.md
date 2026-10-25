# 角色动作工坊 · Character Motion

一个技能入口，四个工作分支：**生图、工作台、骨骼参考、游戏接入**。从文字或已有角色制作完整帧动画，按资源工程整理角色／衣装／动作／方向，调帧后导出可供游戏使用的图集和时间轴。

## 下载与更新（普通使用）

**macOS 12+ 直接从 [最新 Release](https://github.com/rainlotus97/motionlab-releases/releases/latest) 按芯片下载。无需 Xcode、Python、Git 或终端安装。**

| Mac 类型 | 下载文件 |
| --- | --- |
| Apple Silicon（M 系列） | `MotionLab-<版本>-arm64.zip` |
| Intel | `MotionLab-<版本>-x86_64.zip` |

“ → 关于本机”可查看芯片。每个包只带对应运行环境，均包含工作台、技能和两套精选资源。`universal` 仅用于旧版自动升级过渡，手动安装无需选它。

1. 打开公开 Releases 页面，下载 ZIP 并解压，无需登录。
2. 将 `MotionLab.app` 拖入“应用程序”（也可放 `~/Applications`），双击打开。不要长期从压缩包临时目录运行。
3. 首次启动会登记 `$character-motion` 技能，并在 `~/Desktop/MotionLab/` 创建独立资源库。开启新 Codex 会话使用技能。

当前包使用 ad-hoc 签名，尚未获得 Apple Developer ID 签名与公证。首次下载可能被 macOS 拦截；确认来自本仓库后，按 [Apple 官方说明](https://support.apple.com/en-us/102445) 在“系统设置 → 隐私与安全性”选择“仍要打开”。不需要关闭 Gatekeeper 或运行删除隔离标记的命令。

**更新：应用菜单“检查更新…” → “下载并安装”。** 应用检查 GitHub 已发布的正式 Release，按实际芯片选择安装包，再按 SHA-256 校验下载包和应用签名，保存调整、退出、替换并重新打开。普通 Git 提交不会触发更新，维护者需发布新版本。应用目录须可写；没有系统应用目录写权限时，使用自己的 `~/Applications`。

下载和更新使用独立的公开发行仓库 `rainlotus97/motionlab-releases`，无需 GitHub 账号或 token；源码仓库保持私有。安装包内的技能脚本和精选示例随包公开。也可以在网页下载新包，退出后手动替换同名 app。

升级只替换应用，不覆盖桌面上的 `projects/`、`references/` 或工具配置。自动升级在应用旁保留 `MotionLab-backup-<版本>-*.app`；替换或打开命令失败时恢复旧应用。确认新版正常后可删除旧应用备份。素材修改不会自动跨机同步，迁移作品仍需复制资源库数据。

生图使用当前环境提供的能力。未指定服务时可用内置生图；用户有明确偏好时调整对应配置与实际调用。Release 不绑定随想、不带生图密钥。工作台、骨骼与导出可离线使用。

## 源码安装（开发者可选）

只有源码构建需要预先安装 Python 3.10+ 与 Xcode Command Line Tools（`xcode-select --install`）：

```sh
git clone https://github.com/rainlotus97/character-motion.git
cd character-motion
python3 scripts/setup_studio.py
```

也可双击仓库的 `Open.command`。脚本创建 `.venv`，不改系统 Python，生成绑定本地源码路径的桌面 app；这个开发入口不用于分发。源码更新用 `git pull --ff-only` 后重新运行安装命令。普通用户使用上面的 Release。

Release 首次打开把 app 内的技能链接为 `~/.codex/skills/character-motion`；源码安装则链接当前仓库，均遵守 `CODEX_HOME`。已有主技能和旧分支会备份到 skills 目录之外，不删除用户工程。替换 app 后再次打开，技能随版本更新；不要在 app 内保存个人素材或修改工具代码。

```text
$character-motion 生图：沿用这个角色，补一段喝水动作。
$character-motion 工作台：打开当前角色工程，调整时序并导出给游戏。
$character-motion 骨骼：查看动作库，保存本次采用的选帧方案。
$character-motion 接入：沿用游戏坐标和状态机，支持移动、换装和动作切换。
```

## 随包资源

精选资源约 25.6 MiB，随 Release 和源码提供，无需 LFS 或外部素材链接：

- **松团一套日常装**：八方向跑动 + SE 蹦跳，9 段动作、40 个播放段，约 1.1 MiB。保留透明图集、时序和配准点。
- **UAL 1 一套骨骼库**：Quaternius 的 43 个双足动作，原始 GLB、可直接播放的 JSON、长腿／短腿／圆身比例与 CC0 许可，约 24.6 MiB。短腿和圆身适配仍为参考草案。

其他衣装、顾长惜未通过的动作、UAL 2／四足／100STYLE 库、历史母图、生成记录、导出缓存和密钥不上传。资源范围及文件校验值见 [examples/manifest.json](examples/manifest.json)。图像资源保留用户所有权；骨骼遵守附带原许可。

默认安装复制示例到桌面资源库，已有同名目录完整保留；更新不会覆盖你的调帧。已有骨骼动作 ID 重合时保留原库，不重复登记。源码安装只装工具用 `--no-examples`；只安装不打开窗口用 `--no-open`。可单独运行 `python3 scripts/install_examples.py --library /absolute/path/MotionLab` 安装示例。

## 工程入口和交付出口

每个资源工程独立持有 `studio.project.json`。ID 使用小写 kebab-case，显示名称可中文；角色、衣装、动作、方向分别记录。先选工程，再选角色和衣装，只展开当前动作类型的方向。制作目录通过“工程文件”按需打开，默认不会递归展开历史版本。

Release 用户直接打开已安装的 `MotionLab.app`。应用代码与运行环境随 app 分发，工程单独放在 `~/Desktop/MotionLab/`。重复打开激活同一窗口，关闭窗口释放服务；多工程共用一个本地端口，无 Node 服务。

主界面只保留工程切换、动作树、预览和时间轴；「文件」「视图」菜单提供常用命令，`⌘,` 或左下角“设置与工具”进入二级页面。全局骨骼库在 `references/`，人物工程在 `projects/`。首次安装附带一套松团日常装及一套 UAL 1 骨骼库；详情见[桌面入口](references/desktop-workbench.md)。

默认运行 ZIP 只有 `package.json` 与 `atlases/`。JSON 保留角色／衣装／动作／朝向、逐段时长、循环、原点和裁边恢复，包内 `consumer` 给出绘制约定；另一会话读取它即可消费，不需要重新裁图或访问原机器。预览、独立PNG和制作证据为显式选项；详细编码报告留在本地导出目录。

```sh
scripts/run motion_studio.py export \
  --project /absolute/path/to/character/studio.project.json \
  --out /absolute/path/to/character/exports/new-package --preset balanced
```

运行包的 schema 目前为本工具格式，需要宿主适配；它不自动接通战斗、奖励、移动或存档。精确字段与绘制位置见[交付合同](references/asset-layout-and-export.md)，所有权与生命周期见[运行接入](references/runtime-integration.md)。原画、生成请求、角色专有记录和历史候选留在本地工程；仅明确授权的精选示例收入 `examples/`。

AI 可运行 `scripts/run studio_control.py status`、`projects`、`request`（自动选择随包 Python，无需系统 Python），通过同一个本机接口操作工程与导出；详情见[桌面控制合同](references/desktop-workbench.md#ai-与程序控制)。

## 维护

`SKILL.md` 负责路由，`references/` 负责条件说明，`scripts/` 负责装配／编码／服务，`assets/studio/` 是唯一共享播放器和工作台。工具模块与接口见[实现合同](references/studio-architecture.md)。修改UI不另写播放器，修改输出不复制编码器。代码授权以维护者后续声明为准；内置 Split.js 的 MIT 许可与来源保存在 `assets/studio/vendor/`。

在 `scripts/` 目录运行相关检查：

```sh
python3 -m unittest test_motion_studio test_motion_catalog test_studio_files test_studio_exports test_sprite_export
# 安装与精选资源改动
python3 -m unittest test_install_examples test_studio_desktop test_sampling_plans test_studio_update
```

骨骼／对照／配准模块改动再运行对应测试。技术通过只证明数据与工具行为；动作自然度、角色外观和真机表现需独立审阅。

发布流程见[Release 构建与维护](references/release-maintenance.md)。发布版本号放在 `VERSION`，运行时来源和 SHA-256 固定在 `release-runtime.json`；下载缓存和成品 ZIP 不进入 Git。
