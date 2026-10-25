# 共用动作工作台

入口为 `$character-motion 工作台`。用于看已有帧／图集、调时长、播放顺序与整帧偏移，以及进入已存在的骨架库／人工标记页。默认从[桌面应用](desktop-workbench.md)启动，先读项目清单，复用当前工作台；**换人物或重新生图只更换清单与素材，不重新写HTML／播放器。**共享UI与服务器在本技能`assets/studio/`和`scripts/motion_studio.py`。修改工具前读[模块、接口与验证合同](studio-architecture.md)，复用其唯一播放器和导出器。

## 已实现功能与边界

- 顶部工程名打开目录选择器；可浏览本机目录、扫描已有工程，或在已有空目录创建工程。动画是主工作区；工程文件、骨骼和对照从 Mac「视图」菜单或“设置与工具”二级页进入。缺少数据的工具说明所需配置。
- 资产区支持建目录、批量导入图片、预览、创建／修改文本、重命名、移入回收站及恢复；正在被工程引用的源文件与父目录禁止破坏性修改。选择静态同尺寸图片可按文件名自然排序创建动作，随后在时间轴调整顺序与时长。动作可重命名或从工作台移除，原图不随之删除。
- 项目侧栏按角色→衣装→动作类型收起，预览右上常驻空间九宫格，只显示选中类型实际已有方向；缺失方向留空不可点，单向动作只保留其对应槽位；正常／慢速播放、逐段拖动、循环／一次性、末帧停留。详细帧检查器和画面设置按需展开，不平铺所有衣装方向。
- 固定显示高度、深浅／透明棋盘、上一帧影像；导出原点虚线与具名配准点可分别开关，图例显示当前逻辑坐标。
- 每播放段毫秒时长、顺序前后移动、整帧X/Y平移。不改变原画，不缩放、不扭曲肢体、不自动改视角。
- 保存调整同时写工程内`.motion-studio/draft.json`与浏览器缓存；重开工程自动恢复匹配草稿。下载／导入JSON仍可迁移。绑定项目及源资产hash，源图变化时不套用旧调整。通过工作台添加／重命名／移除动作时，仅对来源与原始时序均未变化的动作迁移调整，并重新绑定新指纹。重置当前动作保留其他动作。
- Python显式应用草稿，输出新的motion_tool制作manifest，再复用pack／verify／preview／animate。偏移导致非透明像素越界由pack阻止；页面红框只是提醒。
- 骨骼抽样和逐帧对照嵌入统一工作区，保留各自数据与保存边界；切区暂停隐藏播放器。其他资料从更多菜单的项目links打开。工具入口独立于角色；未配置数据时显示有意义的空状态，不伪造内容。
- 桌面资源库 `studio.tools.json` 配置全局 `sampling`；旧独立 HTTP 工程仍兼容项目内 `sampling`。骨骼工具支持原版连续、精确相位预设、自选帧预演、方案增删改／采用／导出。按[抽样方案合同](sampling-plans.md)执行；它修改的是生图姿态方案，不增删已有母图或自动生图。
- 项目配置`comparisons`时复用`/comparison.html`逐格对比冻结骨架方案与人物帧：同步双画布、目标叠加、统一比较中轴／脚线、全部缩略、正常／慢速与单次末→首审查。人工问题可保存到项目或下载JSON；目标骨架不是从人物图识别出来的关节。
- 一键生成当前／全部动作的序列图包：透明裁边、统一降采样、精确相同图块去重、非旋转多页图集、无损／有损WebP或无损PNG、可选独立PNG。同台切换原图／压缩后，分别看实际文件字节和图集RGBA解码估算；详见[统一资产与导出合同](asset-layout-and-export.md)。

调整草稿不是已写回母版或正式游戏。界面沿用浅纸、墨绿、低饱和视觉；只显示名称、数值、引用／错误状态，流程说明留在技能文档。使用自定义下拉、对话框和提示，不弹原生select／alert／confirm。渲染只展示图像本身；root_motion和事件作为数据保留，工作台不模拟游戏移动／伤害。含事件或非零根运动的动作禁止简单重排，需另做时序语义设计。当前不重绘源姿态、不内置生图、光流、自动抠图或自动骨骼识别；PNG／WebP文件编码也不等于KTX2／ASTC等GPU纹理压缩。确需高级图层／音效等且已有XSXB Frame Tuner，可用其Lite分支，遵循相应技能；不为预览任务擅自接Godot运行时。

## 项目清单与启动

项目中长期保留一个`studio.project.json`；路径相对它所在目录，资源必须在该目录内（包括解析后的符号链接）。把它放在角色或共享资产根目录，不放系统盘根。示例：

```json
{
  "version": 1,
  "id": "character-e-study",
  "name": "角色动作工作台",
  "sources": [
    {"type":"registration","id":"e-walk","name":"E行走","path":"aligned/report.json"},
    {"type":"runtime","id":"accepted-actions","path":"runtime/motion.runtime.json"}
  ],
  "links": [
    {"name":"骨骼参考库","path":"motion-library/index.html"},
    {"name":"母版标记","path":"annotate.html"}
  ]
}
```

只填真实存在且本次需要的条目。registration读公共配准输出的report和同目录`<id>.png`，blocked报告拒绝载入；runtime读motion_tool格式并先verify，其他格式需明确转换。runtime来源可设`excludeActions:["historical-action-id"]`排除历史对照，按精确动作ID匹配。根清单可设`provenancePaths:["motion-plan.json","derivations.json"]`，将现有JSON制作证据的hash绑定草稿，选择附带制作证据时才将原字节放在`provenance/`，不复制高分辨率母图。sources可空，用于仅打开骨骼参考链接。每个项目ID应稳定且区分人物。

### 原点、配准点与身体根点

三者需区分：`origin`是逻辑画布／导出的固定定位约定；`markers`是随整帧偏移移动的人工配准观察点；身体中心、脚底或游戏身体根点必须有独立的实际标定。瞳孔中点、单眼、耳冠中点均不能自动称作身体中心。不根据透明包围盒（可能含大尾巴）强行重置中心，不为了让点与十字重合而平移人物。

source可设`originLabel:"导出原点"`和`markerLabels:{"stable-center":"双眼中点"}`，runtime另可设`markerLabelsByAction:{"walk-NE":{"stable-center":"双耳冠中点"}}`按精确动作ID覆盖；registration只读取基础标签。标签须与已有记录对应，未知标记保留原ID，不猜解剖含义。显示标签不改旧标记ID、坐标、时序或图像；导出包保留标签，缩放只作用于坐标一次。验证原图／压缩后使用同一位置，并在整帧偏移时确认原点固定、配准点随图移动。

```sh
python3 scripts/motion_studio.py inspect --project /project/character/studio.project.json
python3 scripts/motion_studio.py serve --project /project/character/studio.project.json --port 8911
# 可选：启动时索引覆盖多个动作工程的资产上级目录
python3 scripts/motion_studio.py serve --project /project/character/studio.project.json --asset-root /project/assets --port 8911
```

上面的 serve 仅供开发调试；日常用[桌面启动器](desktop-workbench.md)。一个服务共用系统分配端口，以工程路径命名空间隔离请求，切工程不启动子服务。监听127.0.0.1，不抢占已有端口。应用关闭即释放服务，日志留资源库 `.studio/`。导出只生成新的 `exports/<export-id>/`，不覆盖原图。

## 资源工程、文件与调整

点击顶部工程名，在自定义面板输入目录绝对路径或逐级浏览。选择目录后扫描已有`studio.project.json`；已有工程直接打开，没有清单则可“在此创建工程”。创建只新增清单，不搬迁／覆盖已有素材。每个工程绑定自己的URL路径，共用同一监听端口；切工程不分配额外端口。目录扫描有界且不跟随符号链接；完整限制见[模块合同](studio-architecture.md)。

资产操作限定在当前清单目录。图片批量导入不覆盖同名文件，导入失败保留此前成功项并显示具体错误。文字编辑检查旧hash，防止盖掉AI或其他窗口的新修改；删除移入`.motion-studio-trash/`，恢复时不覆盖现有文件，可填写另一个工程内路径。源文件、被引用JSON与它们的父目录标为“工程引用中”；不靠强制删除破坏现有动作。移除动作仅更新配置，制作母图与历史包继续保留。

新增`sequence`来源适用于普通静态序列，不强制转换为游戏运行manifest：

```json
{"type":"sequence","id":"pet-hop","name":"蹦跳","files":["frames/01.png","frames/02.png"],"canvas":[512,512],"origin":[256,512],"durationMs":120,"loop":true}
```

新建动作填写角色／衣装／动作的稳定ID及显示名，方向可空；组合身份重复时拒绝，不生成一排无法归类的随机动作。旧无分类来源保持兼容，按来源数据展示。命名见[资产合同](asset-layout-and-export.md#命名分组和入口)。

文件路径相对工程；1–256张不同图片、尺寸一致，文件顺序就是源姿态顺序。可选 `steps:[{"frame":0,"durationMs":400},…]` 显式记录重复姿态与不等长播放段；索引必须指向 files，不能靠重复原图冒充新姿态。UI初始origin为画布底边中点，**只是导出约定，不是自动识别身体中心**。不能以加入工作台替代动作、透明边界或配准验收；需人工根点时填写真实约定。JSON的`clipNames`映射显示名，`excludedClips`按完整clip ID排除动作，不混用衣装／源动作ID。

动画保存自动应用当前有效帧输入，再持久化全部动作调整。工程切换遇到未保存输入使用页内对话框保存后打开或取消；不使用浏览器原生确认框。骨骼和对照在隐藏时暂停，切回保持本页状态；其本机草稿与正式采用／人工保存仍分开，未增加跨浏览器自动恢复承诺。离线预览禁用目录、文件操作和再次编码；可播放、调帧、下载调整JSON，不请求本机API。

## 冻结方案与人物逐帧对照

复用同一服务的`/comparison.html?id=<comparison-id>`，图片变更只更新项目映射和素材，不另造角色专用播放器。项目配置示例：

```json
"comparisons": {
  "reviewStore": "comparison-reviews.json",
  "sources": [{"id":"e-candidate","path":"comparison/e-candidate.json"}]
}
```

定义文件包含`version:1 / id / name / clipId / planContract / alignment / frames`；`planContract`是已导出的**冻结方案JSON路径**，相对定义文件所在目录。`frames`逐格明确`slot / artFrameId / artStepSlot`，后者为人物clip当前播放数组的索引，不是骨架原生帧号。禁止只靠两套素材都叫01–08推断对应。比较画布／origin必须与人物clip相同；整组共用人工确认的`pixelsPerSourceUnit / sourceOriginX / sourceFloorY`，不逐格自动拉伸或拟合人物。

页面分别显示源小数索引、源时刻／相位、原生帧或插值贡献、人物帧ID和各自停留时间；比较播放统一使用冻结目标的时长。规范人物帧与可追溯原始母图有独立链接；详情保留方案快照、配置、来源与人物的hash。`metadata.relationship:"historical-unmatched"`标记挂在新目标下诊断的旧素材；`candidate-not-reviewed`标记待审候选。这些关系是制作记录，不是姿态检测结果。

对照前读取本次参考精度：定义文件可设`metadata.referenceIntent:"rhythm"`表示阶段／节奏参考，或`"exact"`表示精确姿态转绘；未设时按本次角色目标验收，不默认要求逐点复现。前者主要检查交替支撑、摆动方向、身体前后／高度轨迹与首尾；后者才以冻结关节位置逐点核对。页面显示本次意图，并随复制的修图说明保留；阶段参考首次打开该版本时默认关闭人物骨架叠加，用户仍可开启，同页切回版本保留选择。叠加不自动规定精度，长裙下无法确认的点记为不确定。短腿萌宠可以直接以认可完整帧预览和调帧，不为使用工作台补建成人骨架。两种路线都保留正常起伏，身体比例、相机、承重和连贯性仍需实际看图。

人工逐格选待审／需修正／本格通过，记录角度、比例、手足、衣发、配准或首尾问题。保存以store revision及comparison fingerprint共同核验；图片／快照／来源变化后旧结论标为stale，不沿用当前通过。下载JSON明确区分已存与本地未存文字。**hash一致只证明来源字节与映射可追溯，不能证明人物已遵守骨架。**本格通过、整段流畅和运行接入分别验收；发现母版错误就按本次参考精度与具体缺陷实际修图，不能以新增对比页代替修复。

## 一键压缩与可移植图包

顶部“导出”打开统一面板，选择当前动作／当前衣装／全部动作及原作／均衡／轻量；尺寸、格式、质量、单页上限和独立PNG收在更多选项。点击“生成图包”后显示任务阶段、已耗时间、明确失败原因或下载结果。结果显示原输入文件／输出图集字节、原解码／输出页RGBA估算、页数与ZIP体积，以及真实导出目录。可切回原图在同一显示大小比较；导出后再编辑，会将旧包标为过期。取消只取消等待，已开始的本地编码可能完成为一个独立包，不写源图。

默认ZIP只含 `package.json` 和 `atlases/`，本机导出目录另有报告和ZIP。独立PNG、离线预览及JSON制作证据默认关闭，在更多选项中明确选择才加入。解压后保留相对目录；预览使用包内数据和图片，不请求原服务API。记录实际HTTP／本地文件验证范围，某个工具拒绝file导航时不能声称已验证双击打开。

```sh
python3 scripts/motion_studio.py export --project /project/character/studio.project.json --out /project/character/exports/new-package --preset balanced
```

包保留稳定clip ID、每播放段时长、循环／一次性、标记和事件；降采样后的root坐标与画布使用同一逻辑像素单位，原数据仍可从来源追溯。trim的`sourceSize/spriteSourceSize/pivot`与atlas rect分别保存，渲染使用`drawRect`恢复原位置，不拉满整个画布。格式与参数见[导出合同](asset-layout-and-export.md)；这是可重建的通用位图包，不冒充所有引擎可直接导入或真实GPU压缩。

## 保存与重建闭环

浏览器下载`<project>.studio-draft.json`，交给技能应用，不要求用户手工执行命令。用户请求“应用调整”时：

```sh
python3 scripts/motion_studio.py apply --project /project/character/studio.project.json --draft /project/character/adjustment.json --out /project/character/tuned-motion.json
python3 scripts/motion_tool.py inspect --manifest /project/character/tuned-motion.json --out /project/character/tuned-inspection.json
python3 scripts/motion_tool.py pack --manifest /project/character/tuned-motion.json --out /project/character/tuned-runtime
python3 scripts/motion_tool.py verify --runtime /project/character/tuned-runtime/motion.runtime.json
```

输出manifest必须是新路径，不能覆盖输入。此版本导出一个共享画布／origin的人物，制作候选会按本次clip顺序生成`clip0`等内部ID，并在 `studioProvenance.clipMap` 保留原clip身份和完整分类，标记 `authoringOnly`；正式运行交付优先使用保持原ID的精简图包，制作候选接入前按映射恢复原宿主动作ID，不直接覆盖旧游戏资源。不同人物／画布分开导出；不自动缩放它们以迁就合同。偏移写入整帧anchor，不双重烘入根运动。图集通过后再生成WebP，并在项目清单加入候选runtime供同台切换；源素材、原时间轴和旧运行版保持。

仅看效果不必导出。仅改时长不必生图。错误腿位／裙摆／视角回到完整母版修图；工具不能用小幅平移掩盖结构错误。

## 最小验证

新增／更换素材：查实际画布、hash、配准／runtime报告、图片加载、正常与慢速循环、末→首和小尺寸。调整流程：改一段时长／偏移，保存与恢复，再下载／导入核对；显式应用后用原工具验证导出。目录功能需以第二份有效清单实际打开，核对当前URL、工程名、动作和图片；返回原工程后核对旧标签仍绑定原清单。另查只换扫描根不换当前工程、未保存草稿的保留／取消、无效路径与空目录提示。换项目复用同一UI，不重新定制。技术fixture仅验证合同，不证明人物自然度。
