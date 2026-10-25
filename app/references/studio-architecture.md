# 统一工作台：实现合同

此文件约束共享工具的后续 AI 修改，不限制角色、动作或画风。生成规范继续以主技能及 simple／detailed 分支为准；工作台不能把技术 fixture 或 hash 通过称作美术通过。

## 唯一拥有者

| 模块 | 责任 |
| --- | --- |
| `assets/studio/index.html`、`studio.css` | 项目侧栏、角色／衣装／动作层级、预览、时间轴、按需检查器、工程文件与导出面板；浅纸／墨绿主题 |
| `studio.mjs` | 唯一人物播放器、源／压缩图一致绘制、时序、整帧偏移、草稿、导出任务状态 |
| `controls.mjs` | 自定义下拉、对话框、通知、动作列表、键盘；可内联到离线包 |
| `workspace.mjs` | 工程选择、工作区切换、可拖分栏、配置变更前保存及重载；只在在线模式加载 |
| `file-console.mjs` | 文件列表、预览、导入、文本修改、重命名、回收站、序列导入 |
| `sampling.mjs`、`comparison.mjs` | 原有抽样方案与人工审阅权威；通过 `motionStudioBridge` 提供 pause／pending，不另建人物播放器 |
| `tool-shell.mjs`、`tool-theme.css` | 两个工具页的共同控件、嵌入模式和主题，显示选项折叠 |
| `scripts/motion_studio.py` | 加载／校验 sequence、registration、runtime，通用项目API、草稿校验、服务装配 |
| `motion_catalog.py` | 有界目录浏览／工程扫描、空工程创建、同一服务下的工程请求命名空间 |
| `studio_files.py` | 限域文件操作、引用保护、旧版本校验、回收站、工程配置与草稿写入 |
| `studio_exports.py` | 导出任务队列及终态；与旧同步接口共用并发门 |
| `sprite_export.py` | 唯一图包编码、trim、去重、packing、来源、ZIP和离线预览 |

保持 ESM 与少依赖；不增加另一个框架、状态库、人物专用演示页或复制导出算法。所有异步加载需有过期校验，页面关闭释放请求、RAF、timer、observer、分栏监听；切隐藏工具时暂停播放。在线API不可被打进离线执行路径。

桌面窗口与生命周期由 `assets/desktop/Studio.swift`、`scripts/studio_desktop.py`、`scripts/install_desktop.py` 与 `scripts/setup_studio.py` 负责，见[桌面合同](desktop-workbench.md)。`scripts/studio_control.py` 通过活动实例核验调用相同 HTTP API，供 AI 和脚本使用。菜单通过有界 `motionStudioCommands` 调用共享UI；不另造播放器。

## 目录合同

```text
<资源工程>/
  studio.project.json           # 稳定ID、相对来源与工具配置
  <既有参考／原始母图／制作目录> # 不强制搬迁，不被导出覆盖
  .motion-studio/draft.json      # 已保存调整；不当作美术母版
  .motion-studio-trash/<id>/     # 删除文件、文本及配置的修改前版本
  exports/<唯一id>/
    package.json
    report.json                # 本地报告，不加入运行ZIP
    atlases/
    frames/                    # 可选独立PNG
    provenance/                # 可选制作证据，默认不生成
    preview.html               # 可选离线审阅，默认不生成
    package.zip                # 默认仅package.json + atlases/
```

工程中的本机草稿和回收站不提交Git；导出包按实际交付范围归档。工程扫描不索引导出目录和隐藏目录，避免历史包自我递归。`studio.project.json`不超过64 KiB；扫描上限8层、2000目录、20000目录项、100工程；单服务最多100个已打开工程。目录文件列表最多2000项。需要更大范围先拆工程，不取消限制。

## API与数据边界

- 工程：`GET /api/catalog`、`GET /api/catalog/directories?path=...`、`POST /api/catalog/root {path}`、`POST /api/catalog/open {id}`、`POST /api/catalog/create {path,name}`。目录须已存在；创建用排他写，不覆盖旧配置。
- 文件：`GET /api/files?path=...`、`/api/files/inspect?path=...`、`/api/files/trash`。路径相对当前工程。普通文件不可越界、指向符号链接或修改隐藏内部目录。
- 文件写入：`POST /api/files/mkdir {path}`、`upload {path,content:<base64>}`、`write {path,text,sha256?}`、`rename {path,target,sha256?}`、`trash {path,sha256?}`、`restore {id,target?}`。已有文件修改／删除必须携带读取到的hash；上传上限24 MiB；文本编辑上限1 MB。恢复不覆盖同名目标。
- 动作：`POST /api/files/sequence {name,files,durationMs,fingerprint,metadata?,loop?}`；`POST /api/files/action {operation:rename|remove,clipId,name?,scope?:current|family,fingerprint}`。配置修改有备份、旧hash和工程指纹检查。重命名不改clip ID；UI按family重命名同一角色／衣装／动作的所有方向，其他衣装不受影响；移除不删源图并清除该条分类覆盖。
- 调整：`GET|POST /api/files/draft`。沿用 version/projectId/fingerprint/clips 合同，校验所有slot、时长、偏移和事件重排限制。重开自动应用匹配的已保存调整，源图变化不能悄悄套用旧偏移。
- 导出：`POST /api/exports {draft,options}` → 202 `{id,status,phase}`；`GET /api/exports/<id>` → queued/running/complete/failed。complete带结果与下载URL，failed带可读error。只保留最近16个任务状态；失败/取消等待均恢复按钮。取消等待不等于中止编码。
- 新建序列的 metadata 明确 characterId/Name、variantId/Name、actionId/Name、direction；小写kebab ID，显示名独立，组合身份重复明确拒绝。旧无metadata请求继续兼容。工程 `clipMetadata` 只允许覆盖真实存在完整clip ID的明确分类字段，不从中文猜分组。
- 运行导出 `includeFrames/includePreview/includeProofs` 默认false，ZIP仅含运行清单与图片；报告留本机。包顶层consumer说明矩形、时间与宿主所有权。可选离线预览共用UI，在线API不进入离线路径。
- 旧 `POST /api/export` 与 CLI 继续兼容，共用编码器和单任务并发门。新页面不使用长时间静默同步请求。

所有API限制本机同源请求。每工程独立URL命名空间绑定清单，共用一个监听器；切目录索引不能改变旧请求的资源归属。生成操作仍走现有生图技能，不由文件API提交生图或改游戏存档。

### 本轮回归要点

- 配置改动后的草稿迁移，先将新旧帧URL归一化；只在原始frames与steps都一致时保留调整。曾出现相对／绝对URL误判，重命名后175ms被重置成120ms；必须验证重命名、重开和导出后的真实时长。
- 导出按钮应能显示校验失败、服务失败、编码失败与忙碌，不能留下“没反应”；进度显示阶段与耗时，不捏造百分比。
- 下载包沿用同一HTML/CSS与共享控件，内联模块去掉导入／导出语法；既检查HTTP，也检查真实file://播放，不因静态语法通过就声称离线可用。
- origin是导出约定，markers是人工观察点，身体根点需真实标定。默认只看人物；视图页分别开启标记，不把头部点称为身体中心。
- 压缩相同纹理可以去重，播放时间轴不能去重。文件KB与RGBA解码估算分别显示，后者不是浏览器实测峰值或GPU纹理压缩。

## 开源来源

实际内置 MIT [Split.js 1.6.5](https://github.com/nathancahill/split)，仅用于可拖分栏。文件与许可证见 `assets/studio/vendor/THIRD-PARTY.md`。组织方式参考 [Aseprite 时间轴](https://www.aseprite.org/docs/timeline/) 和 [Piskel](https://github.com/piskelapp/piskel) 的画布、时间轴、属性分区；没有复制其编辑器代码，也不声称接入完整Piskel。

## 定向验证

在 `scripts/` 下运行：

```sh
python3 -m unittest test_motion_studio test_motion_catalog test_studio_files test_studio_exports test_sprite_export
```

涉及抽样／对照数据逻辑时再运行对应测试。对修改的 `.mjs` 做 `node --check`。浏览器至少实测：空目录创建→导入→创建动作→改时长→保存→重命名→重开；文本建改名删恢复；引用阻断；导出失败可重试→成功ZIP→解压离线播放。另打开已配置的骨骼／对照页，核对无可见原生select、未保存数据、隐藏暂停、桌面和窄屏溢出。本轮实际证据写项目目录，不写入全局技能的角色专有资产。
