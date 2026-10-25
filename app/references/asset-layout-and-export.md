# 共用资产目录与导出合同

简洁完整帧、复杂完整帧和调帧入口共用。目标是**原画可追溯、动作可重建、同台审阅、运行包可搬走**；目录示例用于新项目，既有项目只需声明对应路径，不强迫搬动已认可母图。

## 项目根与稳定路径

```text
<character-project>/
  studio.project.json          共用工作台入口；稳定project/source ID
  motion-plan.json             当前采用版、参考权威与动作范围
  references/                  身份／材质／动作／衣装参考
  sources/<revision>/          原始生成母图、请求、采用／拒绝记录
  sources/imports/             工作台默认导入落点（可另选工程内目录）
  authored/<revision>/         制作manifest、配准记录、规范完整帧、检查
  exports/<export-id>/         本次独立导出；禁止覆盖旧包与原资产
```

工作台加载素材的路径相对`studio.project.json`，且解析后仍位于项目根内。把入口放在能容纳既有素材的合理角色／证据根；不要为了套目录复制整套母图、使用越界符号链接，或把工作台UI复制到每个项目。历史目录可继续存在，例如`v8-soft/source/`与`v8-soft/runtime/`；在制作记录中说明角色即可。

`projectId`区分资源工程；`characterId/sourceId/variantId/actionId/direction/frameId`分别标识角色、来源、衣装、动作、朝向与独特姿态。显示名可以改，机器ID保持稳定，不用排序位置`clip0`代替身份。源hash改变使旧草稿失效；新增候选不覆盖已采用版。`motion-plan.json`与工作台的当前runtime指针必须一致，旧版本只标历史／对照。

## 命名、分组和入口

新增角色／衣装／动作 ID 使用小写 kebab-case（字母、数字和连字符），显示名另存，可中文。方向固定 `N/NE/E/SE/S/SW/W/NW`，无方向记 `null`，不要从中文标签猜语义。新动作以角色、衣装、动作和方向组合成稳定来源 ID，重复身份明确拒绝；组合来源ID最长100位，改显示名不换 ID。既有 ID 不强制迁移。

新工程建议以角色或一组实际共享的资产命名目录，例如 `songtuan/`；规范帧在 `authored/<revision>/<variant>/<action>/<direction>/frame-001.png`（无方向可省略该层），原始生成母图在 `sources/<revision>/`，请求及采用记录与其同行。这是新增资产约定，不搬动已有被引用文件。所有导入明确显示目标目录；不要把未知图片静默放进工程根或按母图文件名推断动作。

唯一制作入口为 `studio.project.json`；页面按角色→衣装→动作类型→方向选择，默认只展开当前衣装。原目录树只在“工程文件”中按当前一级与面包屑浏览。入口加载的 `sequence/registration/runtime` 是制作来源；导出的 `package.json` 是消费合同，两者不能互换。

来源可显式填写 `characterId/characterName/variantId/variantName/actionId/actionName/direction`；runtime原有角色和衣装信息默认继承，仅尾部 `-N/-NE/...` 约定用于朝向拆分。需要自定义分组显示时，在根清单 `clipMetadata` 按**完整clip ID**覆盖字段，不改变源动作 ID 或图像。例如：

```json
"clipMetadata": {
  "soft-v8/everyday/walk-E": {"actionName":"跑动"},
  "soft-v8/everyday/jump": {"actionName":"蹦跳","direction":"SE"}
}
```

同一工程应只启用已采用的语义动作来源；历史候选通过记录／对照保留，避免同时启用同一角色、衣装、动作与方向的多个候选而使选择歧义。不同衣装按各自已有动作展示，缺少某方向或特殊动作时不伪造可选项，也不偷偷借用另一衣装。历史与采用版本留在工程记录中；某个工程没有收录的素材不能因为在附近目录里就当作当前动作。

## 制作层和交付层

- **制作层**保留原图字节、实际尺寸／alpha／hash、生成请求、参考分工、采用格、真实sourceRect、共同像素密度与锚点。坏格替换仍登记完整新姿态的来源，不从最终atlas反推原画。源路径与hash共同追溯；跨机器包不能依赖原机器绝对路径才能播放。
- **交付层**从已通过的规范完整帧导出：统一目标尺度、透明边界trim、受限页尺寸、padding／边缘扩展、可选无损／有损编码。保留重建布局所需的trim偏移、逻辑画布与pivot，不按紧bbox重新居中。像素完全相同的图块可以共用存储，播放段、时长、事件和root轨迹不去重。
- **审阅层**复用`character-motion/assets/studio/`和项目清单。同一动作切换原画与导出候选，在相同显示大小、深浅底、正常／半速及首尾接缝比较；导出技术通过不等于美术通过。动图WebP是便于审阅的容器，不能代替可供引擎逐帧读取的atlas与时间轴。

## 可移植序列包的最小语义

下面规定需要保留的数据语义，具体JSON层级以导出器实际schema为准；不把尚未实现的字段写成已支持引擎格式。

| 数据 | 必须能还原的内容 |
| --- | --- |
| 项目／构建 | schema版本、稳定项目和角色ID、导出配置、源清单fingerprint、导出文件hash／字节数 |
| 页 | 相对文件名、实际宽高、格式、hash；多页时每帧明确page索引 |
| 帧 | 稳定来源frame ID、atlas像素rect、是否旋转；导出包局部引用key可重新生成，但来源身份仍可追溯 |
| trim | 完整逻辑画布`sourceSize`、trim图在完整画布中的`spriteSourceSize`偏移与尺寸；缺省不能猜bbox中心 |
| 原点 | 共同origin／pivot及坐标单位，必要的标记；pivot在画布外也须保留 |
| 原资产 | 来源ID、原输入路径或项目相对路径、原文件hash、sourceRect、共同缩放与整帧偏移／镜像；与打包后的atlas rect区分 |
| 时间轴 | 稳定clip ID及衣装／动作／方向，独特帧引用、每播放段毫秒时长、loop、root_motion与events |
| 对称复用 | 镜像源与transform；只在身份允许时复用，不靠导出器修正单侧饰品 |

常见TexturePacker式的`frame/sourceSize/spriteSourceSize/pivot`可作为兼容字段，但它本身不完整承载任意时序、方向、事件与原图来源；这些仍放在本包manifest，不能声称“通用JSON”即自动接入所有引擎。所有相对运行路径和图集随包同行，离开原目录后仍能解码、重建完整画布并按原时序播放。原母图默认不装入轻量运行ZIP；来源记录仍保留，用户另要制作源包时单独导出。

## 压缩选择与真实指标

先在同一尺度比较无损基线，再提供有损／降尺寸配置。默认保留alpha，trim只剔完全透明边距；可见柔毛边缘不能为节省几KB硬切。ZIP主要方便一键交付，已压缩的PNG／WebP再ZIP常常节省有限。

分别报告：①原输入文件总字节；②运行页字节、完整包总字节／ZIP字节；③完整帧展开像素；④实际图集页RGBA8基础像素估计`sum(width*height*4)`。第四项不是真机实测，也不包括mipmap／CPU副本；透明留白同样占像素。降低文件质量通常只减少下载与磁盘，不能据此宣称GPU内存同比下降。降低目标宽高、有效trim、紧凑装箱与减少重复纹理可减少页像素；按需驻留与释放由宿主落实。KTX2／Basis等GPU压缩只有真实编码、解码与宿主支持经过验证才列为已实现。

不承诺“任何几MB原图都压到几十KB”。细毛、alpha、颜色与目标像素会改变结果。提供实际字节、页像素和可视对照，让用户在保真与体积之间选；不能压低质量后仅展示小缩略图掩盖损失。内存规划详见[多格母图与预算](sheet-authoring-and-budget.md)。

## 当前导出入口

共用工作台的“导出→生成图包”按当前草稿导出全部或当前动作，调用本地`POST /api/exports`并查询任务终态，新建`exports/<export-id>/`；旧同步`POST /api/export`仅保留兼容。默认运行 ZIP 仅含 `package.json` 与 `atlases/`。`report.json` 留在本机导出目录，ZIP 本身也不嵌进自身。高级选项 `includeFrames/includePreview/includeProofs` 均默认 false，分别附加独立PNG、复用同一UI的离线预览及既有JSON制作证据。下载ZIP后保留相对目录；只有显式包含预览时才提供离线演示入口。导出后在原工作台切换“原图／压缩后”比较；再次调整的草稿需重新导出，不能把上次包冒充当前状态。

命令行走同一`scripts/sprite_export.py`：

```sh
python3 scripts/sprite_export.py --project /project/character/studio.project.json --out /project/character/exports/new-export --preset balanced
```

`--out`父目录须已存在，目标目录必须是新路径。`lossless`为原尺寸无损WebP，`balanced`为50%尺寸、WebP质量82，`compact`为25%尺寸、质量65；这些是工具预设，不是所有角色的验收标准。可改`--scale 1|0.75|0.5|0.25`、`--format webp|png`、`--quality 1..100`、`--max-page-size 1024|2048|4096`；PNG始终无损，WebP的`lossless`预设保持无损。`--clip`可重复选择稳定clip ID，`--draft`读取当前草稿，`--include-frames`附带独立序列，`--include-preview`附带离线演示，`--include-proofs`附带制作JSON；均默认关闭。参数实际语法以`--help`为准。

`package.json`的`pages/frames/clips`保存页、trim和时序；`provenance.files`登记输入图集相对路径／hash，各帧`source`保留可获得的工程内相对资产路径／hash和sourceRect；工程外原路径从便携运行清单中省略，不能发布个人绝对路径。项目可声明`provenancePaths`绑定既有JSON制作记录；只有显式选择制作证据时才复制这些原字节，包内`provenance.proofFiles`记录原路径／hash和复制路径。制作证据可能有项目本机信息，交付前按用途检查，不把它当作默认运行依赖。历史runtime没有完整原画字段时记录现有来源，不编造缺失链路。`report.json`分别报告输入文件、输出atlas、图集RGBA估计、独特sprite与播放段；具体实测值和检查证据写回当前项目，不因工具存在就宣称通过视觉或真机验证。详见[共用工作台](studio-workbench.md)。

最小验收：从工作台导出实际角色候选，解压到独立目录，检查引用无绝对路径依赖、hash和alpha、重建完整画布的位置、逐播放段时序／事件／root保留；无损包逐像素比对，有损包记录实际误差并做同尺寸审图。检查原资产及输入清单未变；验证重复点击新建不同export-id、失败不留半成品，超页尺寸多页输出或明确拒绝。完成后把实际导出路径、配置、大小报告和检查结果回写项目记录，不只提供一条未经运行的命令。

## 给其他会话／宿主使用

交出整个运行 ZIP，消费者首先读取 `package.json` 的 `format/version` 与顶层 `consumer`，再按 `clips[].id` 或显式 `characterId + variantId + actionId + direction` 选动作。显示名称只用于UI。每个动作的 `steps[].frame` 索引该动作 `frames`，`durationMs` 为毫秒；循环按总时长取模，一次性停在最后一段。

`clip.frames[].rect` 是图集像素 `[left,top,right,bottom]`；目的 `drawRect` 是逻辑画布 `[x,y,width,height]`，两种矩形不能混算。图片URL相对 `package.json`，在 `clip.canvas` 上按 drawRect 恢复，再把 `clip.origin` 放到游戏世界锚点。坐标已经完成导出缩放，不能再乘一次 `meta.scale`。全局 `frames[id].frame` 的 `{x,y,w,h}` 是同一图集矩形的另一种表达。

运行清单不重复包含工作台状态 `previewProject`。宿主自己决定移动、碰撞、动作中断与事件消费；动画包不创建存档或自动奖励，也不能凭动作存在就假设衣装的所有状态均齐备。交接时只需说明采用包路径、期望状态映射、缺项策略和宿主拥有者，无需让对方重新扫描母图或重写裁切。

## 相关格式依据

布局语义参考[TexturePacker裁边与位置恢复](https://www.codeandweb.com/texturepacker/knowledgebase/keep-animation-alignment-with-trim)、[边缘扩展](https://www.codeandweb.com/texturepacker/knowledgebase/sprite-extrude)及[Phaser图集说明](https://docs.phaser.io/phaser/concepts/textures)。文件编码参考[Google WebP编码说明](https://developers.google.com/speed/webp/docs/compression)；GPU压缩边界参考[Khronos KTX2](https://www.khronos.org/ktx/)。这些说明支持格式选择，实际压缩率和视觉损失仍以本次源图实测。
